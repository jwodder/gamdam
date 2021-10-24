from pathlib import Path
import subprocess
from gamdam.__main__ import ensure_annex_repo


def test_ensure_annex_repo_new_repo(tmp_path: Path) -> None:
    ensure_annex_repo(tmp_path)
    assert (tmp_path / ".git").exists()
    assert (tmp_path / ".git" / "annex").exists()


def test_ensure_annex_repo_git_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init", tmp_path], check=True)
    assert (tmp_path / ".git").exists()
    ensure_annex_repo(tmp_path)
    assert (tmp_path / ".git").exists()
    assert (tmp_path / ".git" / "annex").exists()


def test_ensure_annex_repo_git_annex_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init", tmp_path], check=True)
    subprocess.run(["git-annex", "init"], cwd=tmp_path, check=True)
    assert (tmp_path / ".git").exists()
    assert (tmp_path / ".git" / "annex").exists()
    ensure_annex_repo(tmp_path)
    assert (tmp_path / ".git").exists()
    assert (tmp_path / ".git" / "annex").exists()


def test_ensure_annex_repo_multidir(tmp_path: Path) -> None:
    repo = tmp_path / "foo" / "bar" / "baz"
    ensure_annex_repo(repo)
    assert not (tmp_path / ".git").exists()
    assert (repo / ".git").exists()
    assert (repo / ".git" / "annex").exists()


def test_ensure_annex_repo_git_subdir(tmp_path: Path) -> None:
    subprocess.run(["git", "init", tmp_path], check=True)
    assert (tmp_path / ".git").exists()
    repo = tmp_path / "foo" / "bar" / "baz"
    ensure_annex_repo(repo)
    assert (tmp_path / ".git").exists()
    assert (tmp_path / ".git" / "annex").exists()
    assert not (repo / ".git").exists()


def test_ensure_annex_repo_git_annex_subdir(tmp_path: Path) -> None:
    subprocess.run(["git", "init", tmp_path], check=True)
    subprocess.run(["git-annex", "init"], cwd=tmp_path, check=True)
    assert (tmp_path / ".git").exists()
    assert (tmp_path / ".git" / "annex").exists()
    repo = tmp_path / "foo" / "bar" / "baz"
    ensure_annex_repo(repo)
    assert (tmp_path / ".git").exists()
    assert (tmp_path / ".git" / "annex").exists()
    assert not (repo / ".git").exists()
