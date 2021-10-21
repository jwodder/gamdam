from __future__ import annotations

__requires__ = [
    "async_generator ~= 1.10; python_version < '3.10'",
    "click >= 8.0",
    "feedparser ~= 6.0",
    "httpx ~= 0.20.0",
    "trio ~= 0.19.0",
]

from collections import deque
from dataclasses import dataclass, field
import feedparser
import json
import logging
from pathlib import Path
import shlex
import sys
import subprocess
from typing import (
    Any,
    AsyncIterable,
    AsyncIterator,
    Dict,
    List,
    Optional,
    TypeVar,
    Union,
)
from urllib.parse import urlparse
import click
import httpx
import trio

# from tracer import Tracer

log = logging.getLogger("arxiv-cat")

if sys.version_info[:2] >= (3, 10):
    from contextlib import aclosing
else:
    from async_generator import aclosing

    T = TypeVar("T")

    def aiter(obj: AsyncIterable[T]) -> AsyncIterator[T]:
        return obj.__aiter__()

    async def anext(obj: AsyncIterator[T]) -> T:
        return await obj.__anext__()


@dataclass
class Downloadable:
    path: str
    url: str
    metadata: Dict[str, List[str]]


@dataclass
class TextProcess:
    p: trio.abc.Process
    name: str
    encoding: str = "utf-8"

    async def send(self, s: str) -> None:
        await self.p.stdin.send_all(s.encode(self.encoding))

    async def __aenter__(self) -> TextProcess:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.p.aclose()
        if self.p.returncode not in (None, 0):
            log.warning(
                "git-annex %s command exited with return code %d",
                self.name,
                self.p.returncode,
            )

    async def __aiter__(self) -> AsyncIterator[str]:
        buff = b""
        async for blob in self.p.stdout:
            lines = deque((buff + blob).splitlines(True))
            ### PROBLEM: This will break if splitlines() encounters a line
            ### ending in \r
            while lines and lines[0].endswith(b"\n"):
                yield lines.popleft().decode(self.encoding)
            buff = b"".join(lines)
        if buff:
            for line in buff.splitlines(True):
                yield line.decode(self.encoding)


async def open_git_annex(*args: str, path: Optional[Path] = None) -> TextProcess:
    # Note: The syntax for starting an interactable process will change in trio
    # 0.20.0.
    log.debug("Running git-annex %s", shlex.join(args))
    p = await trio.open_process(
        ["git-annex", *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        cwd=path,
    )
    return TextProcess(p, name=args[0])


@dataclass
class Downloader:
    addurl: TextProcess
    repo_path: Path
    downloaded: int = 0
    failures: int = 0
    paths2metadata: Dict[str, Dict[str, List[str]]] = field(
        init=False, default_factory=dict
    )
    md_sender: trio.abc.SendChannel = field(init=False)
    md_receiver: trio.abc.ReceiveChannel = field(init=False)

    def __post_init__(self) -> None:
        self.md_sender, self.md_receiver = trio.open_memory_channel(0)

    async def feed_addurl(self, objects: AsyncIterator[Downloadable]) -> None:
        async with self.addurl.p.stdin:
            async for obj in objects:
                if obj.path in self.paths2metadata:
                    raise ValueError(f"Path {obj.path!r} downloaded to multiple times")
                self.paths2metadata[obj.path] = obj.metadata
                log.info("Downloading %r to %r", obj.url, obj.path)
                await self.addurl.send(f"{obj.url} {obj.path}\n")
            log.debug("Done feeding URLs to addurl")

    async def read_addurl(self) -> None:
        async with self.md_sender:
            async for line in self.addurl:
                log.debug("Line read from addurl: %s", line.rstrip("\n"))
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
                        "%s: download failed; error messages: %r",
                        data["file"],
                        data["error-messages"],
                    )
                    self.failures += 1
                else:
                    path = data["file"]
                    key = data.get("key")
                    log.info("Finished downloading %s (key = %s)", path, key)
                    self.downloaded += 1
                    metadata = self.paths2metadata.pop(path)
                    await self.md_sender.send((path, metadata))
            log.debug("Done reading from addurl")

    async def metadata_adder(self) -> None:
        async with await open_git_annex(
            "metadata",
            "--batch",
            "--json",
            "--json-error-messages",
            path=self.repo_path,
        ) as p:
            async with aclosing(aiter(p)) as lines:
                async with self.md_receiver:
                    async for path, metadata in self.md_receiver:
                        log.debug(
                            "Sending metadata for %s to git-annex: %r", path, metadata
                        )
                        await p.send(
                            json.dumps({"file": path, "fields": metadata}) + "\n"
                        )
                        data = json.loads(await anext(lines))
                        log.debug(
                            "Response received from `git-annex metadata`: %r", data
                        )
                        if not data["success"]:
                            log.error(
                                "%s: setting metadata failed; error messages: %r",
                                path,
                                data["error-messages"],
                            )
                        else:
                            log.info("Set metadata on %s", path)
                log.debug("Done setting metadata")


async def download(
    repo_path: Path, objects: AsyncIterator[Downloadable], jobs: int = 10
) -> int:
    async with await open_git_annex(
        "addurl",
        "--batch",
        "--with-files",
        "--jobs",
        str(jobs),
        "--json",
        "--json-error-messages",
        "--json-progress",
        "--raw",
        path=repo_path,
    ) as p:
        dm = Downloader(p, repo_path)
        async with trio.open_nursery() as nursery:
            nursery.start_soon(dm.feed_addurl, objects)
            nursery.start_soon(dm.read_addurl)
            nursery.start_soon(dm.metadata_adder)
    log.info("Downloaded %d files", dm.downloaded)
    if dm.failures:
        # log.error("%d files failed to download", dm.failures)
        raise RuntimeError(f"{dm.failures} files failed to download")
    return dm.downloaded


INTER_API_SLEEP = 3
PER_PAGE = 100


async def aiterarxiv(category: str, limit: int) -> AsyncIterator[Downloadable]:
    async with httpx.AsyncClient() as client:
        for start in range(0, limit + PER_PAGE - 1, PER_PAGE):
            # <https://arxiv.org/help/api/user-manual>
            url = "http://export.arxiv.org/api/query"
            params: Dict[str, Union[str, int]] = {
                "search_query": f"cat:{category}",
                "start": start,
                "max_results": PER_PAGE,
            }
            r = await client.get(url, params=params)
            r.raise_for_status()
            feed = feedparser.parse(r.text)
            if not feed.entries:
                break
            for e in feed.entries:
                log.info("Found %s (%r)", e.id, e.title)
                try:
                    urlbits = urlparse(e.id)
                except ValueError:
                    log.warning("Could not parse arXiv ID %r", e.id)
                    continue
                path = urlbits.path.lstrip("/")
                if path.startswith("abs/"):
                    path = path[4:]
                if not path:
                    log.warning("Could not parse arXiv ID %r", e.id)
                    continue
                path += ".pdf"
                try:
                    (pdflink,) = [
                        link.href for link in e.links if link.type == "application/pdf"
                    ]
                except ValueError:
                    log.warning("Could not determine PDF download link for %s", e.id)
                    continue
                metadata = {
                    "url": [e.id],
                    "title": [e.title],
                    "published": [e.published],
                    "updated": [e.updated],
                    "category": [e.arxiv_primary_category["term"]],
                }
                try:
                    metadata["doi"] = [e.arxiv_doi]
                except AttributeError:
                    pass
                yield Downloadable(path=path, url=pdflink, metadata=metadata)
            if start + PER_PAGE < limit:
                await trio.sleep(INTER_API_SLEEP)
        log.info("Done fetching arXiv entries")


@click.command()
@click.option(
    "--limit", type=int, default=1000, help="Maximum number of items to download"
)
# Path to a git-annex repository; will be created if it does not already exist
@click.argument("repo", type=click.Path(file_okay=False, path_type=Path))
# arXiv category code
@click.argument("category")
def main(repo: Path, category: str, limit: int) -> None:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)-8s] %(name)s %(message)s",
        datefmt="%H:%M:%S%z",
        level=logging.DEBUG,
    )
    if not repo.exists():
        subprocess.run(["git", "init", repo], check=True)
        subprocess.run(["git-annex", "init"], cwd=repo, check=True)
    downloaded = trio.run(download, repo, aiterarxiv(category, limit))
    # downloaded = trio.run(download, repo, aiterarxiv(category, limit), instruments=[Tracer()])
    subprocess.run(
        ["git", "commit", "-m", f"Downloaded {downloaded} URLs"],
        cwd=repo,
        check=True,
    )


if __name__ == "__main__":
    main()
