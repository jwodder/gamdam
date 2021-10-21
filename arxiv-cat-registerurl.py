from __future__ import annotations

__requires__ = [
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
import subprocess
from typing import Any, AsyncIterator, Dict, List, Optional, Union
from urllib.parse import urlparse
import click
import httpx
import trio

log = logging.getLogger("arxiv-cat")


@dataclass
class Downloadable:
    path: str
    url: str
    extra_urls: List[str]


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


async def open_git_annex(
    *args: str, path: Optional[Path] = None, capture: bool = True
) -> TextProcess:
    # Note: The syntax for starting an interactable process will change in trio
    # 0.20.0.
    log.debug("Running git-annex %s", shlex.join(args))
    p = await trio.open_process(
        ["git-annex", *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE if capture else None,
        cwd=path,
    )
    return TextProcess(p, name=args[0])


@dataclass
class Downloader:
    addurl: TextProcess
    repo_path: Path
    downloaded: int = 0
    failures: int = 0
    paths2urls: Dict[str, List[str]] = field(init=False, default_factory=dict)
    url_sender: trio.abc.SendChannel = field(init=False)
    url_receiver: trio.abc.ReceiveChannel = field(init=False)

    def __post_init__(self) -> None:
        self.url_sender, self.url_receiver = trio.open_memory_channel(0)

    async def feed_addurl(self, objects: AsyncIterator[Downloadable]) -> None:
        async with self.addurl.p.stdin:
            async for obj in objects:
                if obj.path in self.paths2urls:
                    raise ValueError(f"Path {obj.path!r} downloaded to multiple times")
                self.paths2urls[obj.path] = obj.extra_urls
                log.info("Downloading %r to %r", obj.url, obj.path)
                await self.addurl.send(f"{obj.url} {obj.path}\n")
            log.debug("Done feeding URLs to addurl")

    async def read_addurl(self) -> None:
        async with self.url_sender:
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
                    if key is not None:
                        extra_urls = self.paths2urls.pop(path)
                        await self.url_sender.send((key, extra_urls))
            log.debug("Done reading from addurl")

    async def registerurl(self) -> None:
        async with await open_git_annex(
            "registerurl", "--batch", path=self.repo_path, capture=False
        ) as p:
            async with self.url_receiver:
                async for key, extra_urls in self.url_receiver:
                    for u in extra_urls:
                        log.info("Registering URL %r for key %s", u, key)
                        await p.send(f"{key} {u}\n")
                        # log.info("URL registered")
                log.debug("Done registering URLs")


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
            nursery.start_soon(dm.registerurl)
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
                yield Downloadable(path=path, url=pdflink, extra_urls=[e.id])
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
    subprocess.run(
        ["git", "commit", "-m", f"Downloaded {downloaded} URLs"],
        cwd=repo,
        check=True,
    )


if __name__ == "__main__":
    main()
