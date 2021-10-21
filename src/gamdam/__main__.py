import logging
from pathlib import Path
import subprocess
from typing import AsyncIterator, TextIO
import click
import trio
from .core import Downloadable, download, log


@click.command()
@click.argument("repo", type=click.Path(file_okay=False, path_type=Path))
@click.argument("downloads-file", type=click.File("r"), default="-")
def main(repo: Path, downloads_file: TextIO) -> None:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)-8s] %(name)s %(message)s",
        datefmt="%H:%M:%S%z",
        level=logging.DEBUG,
    )
    if not repo.exists():
        subprocess.run(["git", "init", repo], check=True)
        subprocess.run(["git-annex", "init"], cwd=repo, check=True)
    downloaded = trio.run(download, repo, readfile(downloads_file))
    subprocess.run(
        ["git", "commit", "-m", f"Downloaded {downloaded} URLs"],
        cwd=repo,
        check=True,
    )


async def readfile(fp: TextIO) -> AsyncIterator[Downloadable]:
    async with trio.wrap_file(fp) as afp:
        async for line in afp:
            try:
                dl = Downloadable.parse_raw(line)
            except ValueError:
                log.error("Invalid input line: %r; discarding", line)
            else:
                yield dl


if __name__ == "__main__":
    main()
