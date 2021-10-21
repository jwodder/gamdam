import logging
from pathlib import Path
import subprocess
import click
import trio
from .core import aiterarxiv, download


@click.command()
@click.option(
    "--limit", type=int, default=1000, help="Maximum number of items to download"
)
# Path to a git-annex repository; will be created if it does not already exist
@click.argument("repo", type=click.Path(file_okay=False, path_type=Path))
# arXiv category code
@click.argument("category")
def main(repo: Path, category: str, limit: int) -> None:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)-8s] %(name)s %(message)s",
        datefmt="%H:%M:%S%z",
        level=logging.DEBUG,
    )
    if not repo.exists():
        subprocess.run(["git", "init", repo], check=True)
        subprocess.run(["git-annex", "init"], cwd=repo, check=True)
    downloaded = trio.run(download, repo, aiterarxiv(category, limit))
    subprocess.run(
        ["git", "commit", "-m", f"Downloaded {downloaded} URLs"],
        cwd=repo,
        check=True,
    )


if __name__ == "__main__":
    main()
