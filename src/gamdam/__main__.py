from pathlib import Path
from typing import AsyncIterator, TextIO
import click
import trio
from .core import Downloadable, log
from .util import common_options, download_to_repo, init_logging


@click.command()
@common_options
@click.argument("infile", type=click.File("r"), default="-")
@click.pass_context
def main(
    ctx: click.Context,
    repo: Path,
    infile: TextIO,
    log_level: int,
    jobs: int,
    save: bool,
    message: str,
) -> None:
    init_logging(log_level)
    download_to_repo(ctx, readfile(infile), repo, jobs=jobs, message=message, save=save)


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
