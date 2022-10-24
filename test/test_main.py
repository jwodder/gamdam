import logging
from pathlib import Path
from click.testing import CliRunner
import pytest
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
    assert sorted((tmp_path / "failures.txt").read_text().splitlines()) == [
        '{"path": "errors/not-found.dat", "url": "https://httpbin.org/status/404", "metadata": null, "extra_urls": ["https://www.example.com/invalid-path"]}',
        '{"path": "errors/server-error.dat", "url": "https://httpbin.org/status/500", "metadata": {"foo": ["bar"]}, "extra_urls": null}',
    ]
