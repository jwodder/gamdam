from typing import AsyncIterator, TextIO
import click
import trio
from .core import Downloadable, log
from .util import download_to_repo


@click.command()
@download_to_repo
@click.argument("infile", type=click.File("r"), default="-")
def main(infile: TextIO) -> AsyncIterator[Downloadable]:
    return readfile(infile)


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
