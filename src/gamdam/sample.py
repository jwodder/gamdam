import os.path
from pathlib import Path
import subprocess
from typing import AsyncIterator, Dict, Union
from urllib.parse import quote, urlparse
import click
import feedparser
import httpx
import trio
from .core import Downloadable, download, log
from .util import common_options, ensure_annex_repo, init_logging


async def arxiv_articles(category: str, limit: int) -> AsyncIterator[Downloadable]:
    INTER_API_SLEEP = 3
    PER_PAGE = 100
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
                yield Downloadable(
                    path=path, url=pdflink, metadata=metadata, extra_urls=[e.id]
                )
            if start + PER_PAGE < limit:
                await trio.sleep(INTER_API_SLEEP)
        log.info("Done fetching arXiv entries")


async def mtgimages(set_id: str) -> AsyncIterator[Downloadable]:
    INTER_API_SLEEP = 0.2
    async with httpx.AsyncClient() as client:
        url = "https://api.scryfall.com/cards/search"
        params = {"q": f"e:{set_id}", "unique": "prints"}
        while True:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            for c in data["data"]:
                log.info("Found card %r", c["name"])
                if "image_uris" in c:
                    image_url = c["image_uris"]["normal"]
                else:
                    image_url = c["card_faces"][0]["image_uris"]["normal"]
                urlbits = urlparse(image_url)
                _, ext = os.path.splitext(urlbits.path)
                yield Downloadable(
                    path=f"{c['collector_number']}-{quote(c['name'], safe='')}{ext}",
                    url=image_url,
                    ### TODO: Add more metadata and URLs
                    metadata={"id": [c["id"]]},
                    extra_urls=None,
                )
            if (next_url := data.get("next_page")) is not None:
                url = next_url
                params = {}
                await trio.sleep(INTER_API_SLEEP)
            else:
                break


@click.group()
def main() -> None:
    pass


@main.command()
@common_options
@click.option(
    "--limit", type=int, default=1000, help="Maximum number of items to download"
)
# arXiv category code
@click.argument("category")
def arxiv(repo: Path, category: str, limit: int, log_level: int) -> None:
    init_logging(log_level)
    ensure_annex_repo(repo)
    downloaded = trio.run(download, repo, arxiv_articles(category, limit))
    subprocess.run(
        ["git", "commit", "-m", f"Downloaded {downloaded} URLs"],
        cwd=repo,
        check=True,
    )


@main.command()
@common_options
# MTG set code as used by Scryfall
@click.argument("mtg-set")
def mtg(repo: Path, mtg_set: str, log_level: int) -> None:
    init_logging(log_level)
    ensure_annex_repo(repo)
    downloaded = trio.run(download, repo, mtgimages(mtg_set))
    subprocess.run(
        ["git", "commit", "-m", f"Downloaded {downloaded} URLs"],
        cwd=repo,
        check=True,
    )


if __name__ == "__main__":
    main()
