from pathlib import Path
import subprocess
from typing import AsyncIterator, TextIO
import click
import trio
from .core import Downloadable, download, log
from .util import common_options, ensure_annex_repo, init_logging


@click.command()
@common_options
@click.argument("infile", type=click.File("r"), default="-")
def main(repo: Path, infile: TextIO, log_level: int) -> None:
    init_logging(log_level)
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
