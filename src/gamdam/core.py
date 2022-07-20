from __future__ import annotations
from collections.abc import AsyncIterable, AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import shlex
import subprocess
import sys
import textwrap
from typing import Dict, List, Optional, TypeVar
import anyio
from anyio.streams.text import TextReceiveStream, TextSendStream
from pydantic import AnyHttpUrl, BaseModel, validator
from .aioutil import LineReceiveStream

if sys.version_info[:2] >= (3, 10):
    # So aiter() can be re-exported without mypy complaining:
    from builtins import aiter as aiter
    from contextlib import aclosing
else:
    from async_generator import aclosing

    T = TypeVar("T")

    def aiter(obj: AsyncIterable[T]) -> AsyncIterator[T]:
        return obj.__aiter__()


log = logging.getLogger(__package__)


class Downloadable(BaseModel):
    path: Path
    url: AnyHttpUrl
    metadata: Optional[Dict[str, List[str]]] = None
    extra_urls: Optional[List[AnyHttpUrl]] = None

    @validator("path")
    def _no_abs_path(cls, v: Path) -> Path:  # noqa: B902, U100
        if v.is_absolute():
            raise ValueError("Download target paths cannot be absolute")
        return v


class DownloadResult(BaseModel):
    downloadable: Downloadable
    success: bool
    key: Optional[str] = None
    error_messages: Optional[List[str]] = None


@dataclass
class Report:
    downloaded: int = 0
    failed: int = 0


@dataclass
class TextProcess(anyio.abc.AsyncResource):
    p: anyio.abc.Process
    name: str
    stdin: anyio.abc.ObjectSendStream[str]
    stdout: anyio.abc.ObjectReceiveStream[str]

    async def chat(self, s: str) -> str:
        await self.stdin.send(s + "\n")
        return await self.stdout.receive()

    async def aclose(self) -> None:
        await self.stdin.aclose()
        await self.stdout.aclose()
        await self.p.aclose()
        if self.p.returncode not in (None, 0):
            log.warning(
                "git-annex %s command exited with return code %d",
                self.name,
                self.p.returncode,
            )


@dataclass
class Downloader:
    addurl: TextProcess
    repo: Path
    report: Report = field(init=False, default_factory=Report)
    in_progress: dict[str, Downloadable] = field(init=False, default_factory=dict)

    async def feed_addurl(self, objects: AsyncIterator[Downloadable]) -> None:
        async with self.addurl.stdin:
            async with aclosing(objects):  # type: ignore[type-var]
                async for obj in objects:
                    path = str(obj.path)
                    if path in self.in_progress:
                        log.warning(
                            "Multiple entries encountered downloading to %s;"
                            " discarding extra",
                            path,
                        )
                    else:
                        self.in_progress[path] = obj
                        log.info("Downloading %s to %s", obj.url, path)
                        await self.addurl.stdin.send(f"{obj.url} {path}\n")
                log.debug("Done feeding URLs to addurl")

    async def read_addurl(
        self, senders: list[anyio.abc.ObjectSendStream[DownloadResult]]
    ) -> None:
        async with AsyncExitStack() as stack:
            for s in senders:
                await stack.enter_async_context(s)
            async with self.addurl.stdout:
                async for line in self.addurl.stdout:
                    data = json.loads(line)
                    if "success" not in data:
                        # Progress message
                        log.info(
                            "%s: Downloaded %d / %s bytes (%s)",
                            data["action"]["file"],
                            data["byte-progress"],
                            data.get("total-size", "???"),
                            data.get("percent-progress", "??.??%"),
                        )
                    elif not data["success"]:
                        log.error(
                            "%s: download failed:%s",
                            data["file"],
                            format_errors(data["error-messages"]),
                        )
                        self.report.failed += 1
                        dl = self.in_progress.pop(data["file"])
                        r = DownloadResult(
                            downloadable=dl,
                            success=False,
                            error_messages=data["error-messages"],
                        )
                        for s in senders:
                            await s.send(r)
                    else:
                        path = data["file"]
                        key = data.get("key")
                        log.info("Finished downloading %s (key = %s)", path, key)
                        self.report.downloaded += 1
                        dl = self.in_progress.pop(path)
                        r = DownloadResult(downloadable=dl, success=True, key=key)
                        for s in senders:
                            await s.send(r)
                log.debug("Done reading from addurl")

    async def add_metadata(
        self, receiver: anyio.abc.ObjectReceiveStream[DownloadResult]
    ) -> None:
        async with await open_git_annex(
            "registerurl", "--batch", "--json", "--json-error-messages", path=self.repo
        ) as registerurl:
            async with await open_git_annex(
                "metadata",
                "--batch",
                "--json",
                "--json-error-messages",
                path=self.repo,
            ) as metadata:
                async with receiver:
                    async for r in receiver:
                        if not r.success:
                            continue
                        if r.downloadable.metadata:
                            # TODO: Do something on EOF?
                            data = json.loads(
                                await metadata.chat(
                                    json.dumps(
                                        {
                                            "file": str(r.downloadable.path),
                                            "fields": r.downloadable.metadata,
                                        }
                                    )
                                )
                            )
                            if not data["success"]:
                                log.error(
                                    "%s: setting metadata failed:%s",
                                    r.downloadable.path,
                                    format_errors(data["error-messages"]),
                                )
                            else:
                                log.info("Set metadata on %s", r.downloadable.path)
                        if r.key is not None:
                            for u in r.downloadable.extra_urls or []:
                                log.info(
                                    "Registering URL %r for %s", u, r.downloadable.path
                                )
                                # TODO: Do something on EOF?
                                data = json.loads(
                                    await registerurl.chat(f"{r.key} {u}")
                                )
                                if not data["success"]:
                                    log.error(
                                        "%s: registering URL %r failed:%s",
                                        r.downloadable.path,
                                        u,
                                        format_errors(data["error-messages"]),
                                    )
                                else:
                                    log.info(
                                        "Registered URL %r for %s",
                                        u,
                                        r.downloadable.path,
                                    )
                    log.debug("Done post-processing metadata")


async def download(
    repo: Path,
    objects: AsyncIterator[Downloadable],
    jobs: Optional[int] = None,
    addurl_opts: Optional[list[str]] = None,
    subscriber: Optional[anyio.abc.ObjectSendStream[DownloadResult]] = None,
) -> Report:
    async with await open_git_annex(
        "addurl",
        "--batch",
        "--with-files",
        "--jobs",
        "cpus" if jobs is None else str(jobs),
        "--json",
        "--json-error-messages",
        "--json-progress",
        *(addurl_opts if addurl_opts is not None else []),
        path=repo,
    ) as p:
        dm = Downloader(p, repo)
        async with anyio.create_task_group() as nursery:
            sender: anyio.abc.ObjectSendStream[DownloadResult]
            receiver: anyio.abc.ObjectReceiveStream[DownloadResult]
            sender, receiver = anyio.create_memory_object_stream(0)
            all_senders: list[anyio.abc.ObjectSendStream[DownloadResult]] = [sender]
            if subscriber is not None:
                all_senders.append(subscriber)
            nursery.start_soon(dm.feed_addurl, objects)
            nursery.start_soon(dm.read_addurl, all_senders)
            nursery.start_soon(dm.add_metadata, receiver)

    log.info("Downloaded %d files", dm.report.downloaded)
    if dm.report.failed:
        log.error("%d files failed to download", dm.report.failed)
    return dm.report


async def open_git_annex(*args: str, path: Optional[Path] = None) -> TextProcess:
    log.debug("Running git-annex %s", shlex.join(args))
    p = await anyio.open_process(
        ["git-annex", *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        cwd=path,
    )
    assert p.stdin is not None
    assert p.stdout is not None
    return TextProcess(
        p,
        name=args[0],
        stdin=TextSendStream(p.stdin, "utf-8"),
        stdout=LineReceiveStream(TextReceiveStream(p.stdout, "utf-8")),
    )


def format_errors(messages: list[str]) -> str:
    if not messages:
        return " <no error message>"
    elif len(messages) == 1:
        return " " + messages[0]
    else:
        return "\n\n" + textwrap.indent("".join(messages), " " * 4) + "\n"
