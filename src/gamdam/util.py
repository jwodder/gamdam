from functools import partial, wraps
import logging
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, TextIO
import click
from click_loglevel import LogLevel
import trio
from .core import Downloadable, DownloadResult, Report, download, log

if sys.version_info[:2] >= (3, 10):
    from contextlib import aclosing
else:
    from async_generator import aclosing


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


def init_logging(log_level: int = logging.INFO) -> None:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)-8s] %(name)s %(message)s",
        datefmt="%H:%M:%S%z",
        level=log_level,
    )


def formattable(s: str) -> str:
    s.format(downloaded=42)  # Raises a ValueError if not formattable
    return s


def download_to_repo(func: Callable[..., AsyncIterator[Downloadable]]) -> Callable:
    @click.option(
        "-C",
        "--chdir",
        "repo",
        type=click.Path(file_okay=False, path_type=Path),
        default=os.curdir,
        help="Git Annex repository to operate in",
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
        help="Number of jobs for `git-annex addurl` to use",
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
    @click.pass_context
    @wraps(func)
    def wrapped(
        ctx: click.Context,
        repo: Path,
        log_level: int,
        jobs: Optional[int],
        save: bool,
        message: str,
        no_save_on_fail: bool,
        failures: Optional[TextIO],
        output: Optional[TextIO] = None,
        **kwargs: Any
    ) -> None:
        init_logging(log_level)
        objects = func(**kwargs)
        if output is not None:
            trio.run(write_items, objects, output)
        else:
            ensure_annex_repo(repo)
            dlfunc: Callable[..., Awaitable[Report]] = download
            if failures is not None:
                dlfunc = partial(dlfunc, subscriber=partial(write_failures, failures))
            report = trio.run(dlfunc, repo, objects, jobs)
            if report.downloaded and save and not (no_save_on_fail and report.failed):
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
            if report.failed:
                ctx.exit(1)

    return wrapped


async def write_items(objects: AsyncIterator[Downloadable], output: TextIO) -> None:
    with output:
        async with trio.wrap_file(output) as afp:
            async with aclosing(objects):  # type: ignore[type-var]
                async for obj in objects:
                    await afp.write(obj.json() + "\n")


async def write_failures(
    output: TextIO, receiver: trio.abc.ReceiveChannel[DownloadResult]
) -> None:
    with output:
        async with trio.wrap_file(output) as afp:
            async with receiver:
                async for r in receiver:
                    if not r.success:
                        await afp.write(r.downloadable.json() + "\n")
