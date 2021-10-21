import logging
import os
from pathlib import Path
import subprocess
from typing import AsyncIterator, TextIO
import click
import trio
from .core import Downloadable, download, log
from .util import ensure_annex_repo


@click.command()
@click.option(
    "-C",
    "--chdir",
    "repo",
    type=click.Path(file_okay=False, path_type=Path),
    default=os.curdir,
    help="Git Annex repository to operate in",
)
@click.argument("infile", type=click.File("r"), default="-")
def main(repo: Path, infile: TextIO) -> None:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)-8s] %(name)s %(message)s",
        datefmt="%H:%M:%S%z",
        level=logging.DEBUG,
    )
    ensure_annex_repo(repo)
    downloaded = trio.run(download, repo, readfile(infile))
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
                log.exception("Invalid input line: %r; discarding", line)
            else:
                yield dl


if __name__ == "__main__":
    main()
