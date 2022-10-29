import logging
from operator import attrgetter
from pathlib import Path
from click.testing import CliRunner
import pytest
from gamdam import Downloadable
from gamdam.__main__ import main

DATA_DIR = Path(__file__).with_name("data")


def test_main_mixed_failures(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    r = CliRunner().invoke(
        main,
        [
            "-F",
            str(tmp_path / "failures.txt"),
            "-C",
            str(repo),
            str(DATA_DIR / "mixed.jsonl"),
        ],
    )
    assert r.exit_code == 1
    # assert "2 files failed to download" in r.output
    assert (
        "gamdam",
        logging.ERROR,
        "2 files failed to download",
    ) in caplog.record_tuples
    assert sorted(
        (
            Downloadable.parse_raw(line)
            for line in (tmp_path / "failures.txt").read_text().splitlines()
        ),
        key=attrgetter("path"),
    ) == [
        Downloadable(
            path=Path("errors", "not-found.dat"),
            url="https://httpbin.org/status/404",
            extra_urls=["https://www.example.com/invalid-path"],
        ),
        Downloadable(
            path=Path("errors", "server-error.dat"),
            url="https://httpbin.org/status/500",
            metadata={"foo": ["bar"]},
        ),
    ]
