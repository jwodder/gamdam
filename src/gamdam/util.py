from pathlib import Path
import subprocess


def ensure_annex_repo(repo: Path) -> None:
    if not (repo / ".git").exists():
        subprocess.run(["git", "init", repo], check=True)
    if not (repo / ".git" / "annex").exists():
        subprocess.run(["git-annex", "init"], cwd=repo, check=True)
