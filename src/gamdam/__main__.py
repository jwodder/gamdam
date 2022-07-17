from __future__ import annotations
from collections.abc import AsyncIterator, Callable, Coroutine
from functools import partial
import logging
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any, Optional, TextIO
import anyio
import click
from click_loglevel import LogLevel
from . import __version__
from .core import Downloadable, DownloadResult, Report, aiter, download, log

if sys.version_info[:2] >= (3, 10):
    from contextlib import aclosing
else:
    from async_generator import aclosing


def formattable(s: str) -> str:
    s.format(downloaded=42)  # Raises a ValueError if not formattable
    return s


@click.command()
@click.option(
    "--addurl-opts",
    type=shlex.split,
    help="Additional options to pass to `git-annex addurl`",
    metavar="OPTIONS",
)
@click.option(
    "-C",
    "--chdir",
    "repo",
    type=click.Path(file_okay=False, path_type=Path),
    default=os.curdir,
    help="git-annex repository to operate in",
)
@click.option(
    "-F",
    "--failures",
    type=click.File("w"),
    help="Write failed download items to the given file",
)
@click.option(
    "-J",
    "--jobs",
    type=int,
    default=None,
    help="Number of jobs for `git-annex addurl` to use  [default: one per CPU]",
)
@click.option(
    "-l",
    "--log-level",
    type=LogLevel(),
    default=logging.INFO,
    help="Set logging level  [default: INFO]",
)
@click.option(
    "-m",
    "--message",
    default="Downloaded {downloaded} URLs",
    type=formattable,
    help="The commit message to use when saving",
    metavar="TEXT",
)
@click.option(
    "--no-save-on-fail",
    is_flag=True,
    help="Don't commit if any files failed to download",
)
@click.option(
    "--save/--no-save",
    default=True,
    help="Whether to commit the downloaded files when done  [default: --save]",
)
@click.version_option(
    __version__,
    "-V",
    "--version",
    message="%(prog)s %(version)s",
)
@click.argument("infile", type=click.File("r"), default="-")
@click.pass_context
def main(
    ctx: click.Context,
    infile: TextIO,
    repo: Path,
    log_level: int,
    jobs: Optional[int],
    save: bool,
    message: str,
    no_save_on_fail: bool,
    failures: Optional[TextIO],
    addurl_opts: Optional[list[str]],
) -> None:
    """
    Git-Annex Mass Downloader and Metadata-er

    ``gamdam`` reads a series of JSON entries from a file (or from standard
    input if no file is specified) following the input format described in the
    README at <https://github.com/jwodder/gamdam>.  It feeds the URLs and
    output paths to ``git-annex addurl``, and once each file has finished
    downloading, it attaches any listed metadata and extra URLs using
    ``git-annex metadata`` and ``git-annex registerurl``, respectively.
    """
    logging.basicConfig(
        format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        level=log_level,
    )
    ensure_annex_repo(repo)
    dlfunc: Callable[..., Coroutine[Any, Any, Report]] = download
    if failures is not None:
        dlfunc = partial(dlfunc, subscriber=partial(write_failures, failures))
    report = anyio.run(dlfunc, repo, readfile(infile), jobs, addurl_opts)
    if report.downloaded and save and not (no_save_on_fail and report.failed):
        if (
            subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo).returncode
            != 0
        ):
            subprocess.run(
                [
                    "git",
                    "commit",
                    "-m",
                    message.format(downloaded=report.downloaded),
                ],
                cwd=repo,
                check=True,
            )
        else:
            # This can happen if we only downloaded files that were already
            # present in the repo.
            log.info("Nothing to commit")
    if report.failed:
        ctx.exit(1)


def ensure_annex_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=repo,
        stdout=subprocess.PIPE,
        text=True,
    )
    if r.returncode == 0:
        assert isinstance(r.stdout, str)
        repo = Path(r.stdout.strip())
    else:
        log.info("Directory is not a Git repository; initializing ...")
        subprocess.run(["git", "init", repo], check=True)
    r = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=repo,
        stdout=subprocess.PIPE,
        text=True,
        check=True,
    )
    assert isinstance(r.stdout, str)
    if not Path(r.stdout.strip(), "annex").exists():
        log.info("Repository is not a git-annex repository; initializing ...")
        subprocess.run(["git-annex", "init"], cwd=repo, check=True)


async def readfile(fp: TextIO) -> AsyncIterator[Downloadable]:
    async with anyio.wrap_file(fp) as afp:
        async with aclosing(aiter(afp)) as lineiter:  # type: ignore[type-var]
            async for line in lineiter:
                try:
                    dl = Downloadable.parse_raw(line)
                except ValueError:
                    log.exception("Invalid input line: %r; discarding", line)
                else:
                    yield dl


async def write_failures(
    output: TextIO, receiver: anyio.abc.ObjectReceiveStream[DownloadResult]
) -> None:
    with output:
        async with anyio.wrap_file(output) as afp:
            async with receiver:
                async for r in receiver:
                    if not r.success:
                        await afp.write(r.downloadable.json() + "\n")


if __name__ == "__main__":
    main()
