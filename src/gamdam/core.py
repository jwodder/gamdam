from __future__ import annotations
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import shlex
import subprocess
import sys
import textwrap
from typing import (
    AsyncIterable,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    TypeVar,
)
from pydantic import AnyHttpUrl, BaseModel, validator
import trio

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

DEEP_DEBUG = 5


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
class TextProcess(trio.abc.AsyncResource):
    p: trio.Process
    name: str
    encoding: str = "utf-8"
    buff: bytes = b""

    async def aclose(self) -> None:
        await self.p.aclose()
        if self.p.returncode not in (None, 0):
            log.warning(
                "git-annex %s command exited with return code %d",
                self.name,
                self.p.returncode,
            )

    async def send(self, s: str) -> None:
        assert self.p.stdin is not None
        log.log(DEEP_DEBUG, "Sending to %s command: %r", self.name, s)
        await self.p.stdin.send_all(s.encode(self.encoding))

    async def readline(self) -> str:
        assert self.p.stdout is not None
        while True:
            try:
                i = self.buff.index(b"\n")
            except ValueError:
                blob = await self.p.stdout.receive_some()
                if blob == b"":
                    # EOF
                    log.log(DEEP_DEBUG, "%s command reached EOF", self.name)
                    line = self.buff.decode(self.encoding)
                    self.buff = b""
                    log.log(
                        DEEP_DEBUG, "Decoded line from %s command: %r", self.name, line
                    )
                    return line
                else:
                    self.buff += blob
            else:
                line = self.buff[: i + 1].decode(self.encoding)
                self.buff = self.buff[i + 1 :]
                log.log(DEEP_DEBUG, "Decoded line from %s command: %r", self.name, line)
                return line

    async def __aiter__(self) -> AsyncIterator[str]:
        while True:
            line = await self.readline()
            if line == "":
                break
            else:
                yield line


@dataclass
class Downloader:
    addurl: TextProcess
    repo: Path
    report: Report = field(init=False, default_factory=Report)
    in_progress: Dict[str, Downloadable] = field(init=False, default_factory=dict)

    async def feed_addurl(self, objects: AsyncIterator[Downloadable]) -> None:
        assert self.addurl.p.stdin is not None
        async with self.addurl.p.stdin:
            # The "type: ignore" can be removed once
            # <https://github.com/python-trio/trio-typing/pull/41> is released.
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
                        await self.addurl.send(f"{obj.url} {path}\n")
                log.debug("Done feeding URLs to addurl")

    async def read_addurl(
        self, senders: List[trio.abc.SendChannel[DownloadResult]]
    ) -> None:
        async with AsyncExitStack() as stack:
            for s in senders:
                await stack.enter_async_context(s)
            async with aclosing(
                aiter(self.addurl)
            ) as lineiter:  # type: ignore[type-var]
                async for line in lineiter:
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
        self, receiver: trio.abc.ReceiveChannel[DownloadResult]
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
                            await metadata.send(
                                json.dumps(
                                    {
                                        "file": str(r.downloadable.path),
                                        "fields": r.downloadable.metadata,
                                    }
                                )
                                + "\n"
                            )
                            # TODO: Do something if readline() returns ""
                            # (signalling EOF)
                            data = json.loads(await metadata.readline())
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
                                await registerurl.send(f"{r.key} {u}\n")
                                # TODO: Do something if readline() returns ""
                                # (signalling EOF)
                                data = json.loads(await registerurl.readline())
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
    addurl_opts: Optional[List[str]] = None,
    subscriber: Optional[
        Callable[[trio.abc.ReceiveChannel[DownloadResult]], Awaitable]
    ] = None,
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
        async with trio.open_nursery() as nursery:
            sender: trio.abc.SendChannel[DownloadResult]
            receiver: trio.abc.ReceiveChannel[DownloadResult]
            sender, receiver = trio.open_memory_channel(0)
            all_senders: List[trio.abc.SendChannel[DownloadResult]] = [sender]
            if subscriber is not None:
                sender2: trio.abc.SendChannel[DownloadResult]
                receiver2: trio.abc.ReceiveChannel[DownloadResult]
                sender2, receiver2 = trio.open_memory_channel(0)
                all_senders.append(sender2)
                nursery.start_soon(subscriber, receiver2)
            nursery.start_soon(dm.feed_addurl, objects)
            nursery.start_soon(dm.read_addurl, all_senders)
            nursery.start_soon(dm.add_metadata, receiver)

    log.info("Downloaded %d files", dm.report.downloaded)
    if dm.report.failed:
        log.error("%d files failed to download", dm.report.failed)
    return dm.report


async def open_git_annex(*args: str, path: Optional[Path] = None) -> TextProcess:
    # Note: The syntax for starting an interactable process will change in trio
    # 0.20.0.
    log.debug("Running git-annex %s", shlex.join(args))
    p = await trio.open_process(
        ["git-annex", *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        cwd=str(path),  # trio-typing says this has to be a string.
    )
    return TextProcess(p, name=args[0])


def format_errors(messages: List[str]) -> str:
    if not messages:
        return " <no error message>"
    elif len(messages) == 1:
        return " " + messages[0]
    else:
        return "\n\n" + textwrap.indent("".join(messages), " " * 4) + "\n"
