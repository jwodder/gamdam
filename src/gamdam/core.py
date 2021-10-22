from __future__ import annotations
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import shlex
import subprocess
import sys
import textwrap
from typing import AsyncIterable, AsyncIterator, Dict, List, Optional, Tuple, TypeVar
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


log = logging.getLogger("gamdam")

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
    post_sender: trio.abc.SendChannel[Tuple[Downloadable, Optional[str]]] = field(
        init=False
    )
    post_receiver: trio.abc.ReceiveChannel[Tuple[Downloadable, Optional[str]]] = field(
        init=False
    )

    def __post_init__(self) -> None:
        self.post_sender, self.post_receiver = trio.open_memory_channel(0)

    async def feed_addurl(self, objects: AsyncIterator[Downloadable]) -> None:
        assert self.addurl.p.stdin is not None
        async with self.addurl.p.stdin:
            # The "type: ignore" can be removed once
            # <https://github.com/python-trio/trio-typing/pull/41> is released.
            async with aclosing(objects):  # type: ignore[type-var]
                async for obj in objects:
                    path = str(obj.path)
                    self.in_progress[path] = obj
                    log.info("Downloading %s to %s", obj.url, path)
                    await self.addurl.send(f"{obj.url} {path}\n")
                log.debug("Done feeding URLs to addurl")

    async def read_addurl(self) -> None:
        async with self.post_sender:
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
                            "%s: download failed; error messages:\n\n%s",
                            data["file"],
                            textwrap.indent("".join(data["error-messages"]), " " * 4),
                        )
                        self.report.failed += 1
                        self.in_progress.pop(data["file"])
                    else:
                        path = data["file"]
                        key = data.get("key")
                        log.info("Finished downloading %s (key = %s)", path, key)
                        self.report.downloaded += 1
                        dl = self.in_progress.pop(path)
                        if dl.metadata or (dl.extra_urls and key is not None):
                            await self.post_sender.send((dl, key))
                log.debug("Done reading from addurl")

    async def add_metadata(self) -> None:
        async with await open_git_annex(
            "registerurl", "--batch", path=self.repo
        ) as registerurl:
            async with await open_git_annex(
                "metadata",
                "--batch",
                "--json",
                "--json-error-messages",
                path=self.repo,
            ) as metadata:
                async with self.post_receiver:
                    async for dl, key in self.post_receiver:
                        if dl.metadata:
                            await metadata.send(
                                json.dumps(
                                    {"file": str(dl.path), "fields": dl.metadata}
                                )
                                + "\n"
                            )
                            # TODO: Do something if readline() returns ""
                            # (signalling EOF)
                            data = json.loads(await metadata.readline())
                            if not data["success"]:
                                log.error(
                                    "%s: setting metadata failed;"
                                    " error messages:\n\n%s",
                                    dl.path,
                                    textwrap.indent(
                                        "".join(data["error-messages"]), " " * 4
                                    ),
                                )
                            else:
                                log.info("Set metadata on %s", dl.path)
                        if key is not None:
                            for u in dl.extra_urls or []:
                                log.info("Registering URL %r for %s", u, dl.path)
                                await registerurl.send(f"{key} {u}\n")
                    log.debug("Done post-processing metadata")


async def download(
    repo: Path, objects: AsyncIterator[Downloadable], jobs: Optional[int] = None
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
        "--raw",
        path=repo,
    ) as p:
        dm = Downloader(p, repo)
        async with trio.open_nursery() as nursery:
            nursery.start_soon(dm.feed_addurl, objects)
            nursery.start_soon(dm.read_addurl)
            nursery.start_soon(dm.add_metadata)
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
