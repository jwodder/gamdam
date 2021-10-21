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
from .core import Downloadable, download, log


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
    ctx: click.Context,
    objects: AsyncIterator[Downloadable],
    repo: Path,
    jobs: int = DEFAULT_JOBS,
) -> None:
    ensure_annex_repo(repo)
    report = trio.run(download, repo, objects, jobs)
    if report.downloaded:
        subprocess.run(
            ["git", "commit", "-m", f"Downloaded {report.downloaded} URLs"],
            cwd=repo,
            check=True,
        )
    if report.failed:
        ctx.exit(1)
