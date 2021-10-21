from functools import wraps
import logging
import os
from pathlib import Path
import subprocess
from typing import Any, AsyncIterator, Callable
import click
from click_loglevel import LogLevel
import trio
from .consts import DEFAULT_JOBS
from .core import Downloadable, download


def ensure_annex_repo(repo: Path) -> None:
    if not (repo / ".git").exists():
        subprocess.run(["git", "init", repo], check=True)
    if not (repo / ".git" / "annex").exists():
        subprocess.run(["git-annex", "init"], cwd=repo, check=True)


def init_logging(log_level: int = logging.INFO) -> None:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)-8s] %(name)s %(message)s",
        datefmt="%H:%M:%S%z",
        level=log_level,
    )


def common_options(func: Callable) -> Callable:
    @click.option(
        "-C",
        "--chdir",
        "repo",
        type=click.Path(file_okay=False, path_type=Path),
        default=os.curdir,
        help="Git Annex repository to operate in",
    )
    @click.option(
        "-J",
        "--jobs",
        type=int,
        default=DEFAULT_JOBS,
        help="Number of jobs for `git-annex addurl` to use",
        show_default=True,
    )
    @click.option(
        "-l",
        "--log-level",
        type=LogLevel(),
        default=logging.INFO,
        help="Set logging level  [default: INFO]",
    )
    @wraps(func)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    return wrapped


def download_to_repo(
    objects: AsyncIterator[Downloadable], repo: Path, jobs: int = DEFAULT_JOBS
) -> None:
    ensure_annex_repo(repo)
    downloaded = trio.run(download, repo, objects, jobs)
    subprocess.run(
        ["git", "commit", "-m", f"Downloaded {downloaded} URLs"],
        cwd=repo,
        check=True,
    )
