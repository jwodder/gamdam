import os.path
from typing import AsyncIterator, Dict, Union
from urllib.parse import quote, urlparse
import click
import feedparser
import httpx
import trio
from .core import Downloadable, log
from .util import download_to_repo


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
                metadata = {
                    "scryfall_id": [c["id"]],
                    "oracle_id": [c["oracle_id"]],
                    "name": [c["name"]],
                    "set": [c["set"]],
                    "collector_number": [c["collector_number"]],
                    "scryfall_uris": [
                        c["uri"],
                        c["scryfall_uri"],
                        c["scryfall_set_uri"],
                    ],
                }
                try:
                    metadata["gatherer_uri"] = [c["related_uris"]["gatherer"]]
                except KeyError:
                    pass
                yield Downloadable(
                    path=f"{c['collector_number']}-{quote(c['name'], safe='')}{ext}",
                    url=image_url,
                    metadata=metadata,
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
@download_to_repo
@click.option(
    "--limit", type=int, default=1000, help="Maximum number of items to download"
)
@click.option(
    "-o",
    "--output",
    type=click.File("w"),
    help="Write items to file instead of downloading",
)
@click.argument("category")  # arXiv category code
def arxiv(category: str, limit: int) -> AsyncIterator[Downloadable]:
    return arxiv_articles(category, limit)


@main.command()
@download_to_repo
@click.option(
    "-o",
    "--output",
    type=click.File("w"),
    help="Write items to file instead of downloading",
)
@click.argument("mtg-set")  # MTG set code as used by Scryfall
def mtg(mtg_set: str) -> AsyncIterator[Downloadable]:
    return mtgimages(mtg_set)


if __name__ == "__main__":
    main()
