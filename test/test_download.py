from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import stat
import subprocess
from typing import AsyncIterator, Dict, Iterator, List, cast
import anyio
from iterpath import iterpath
import pytest
from gamdam import Downloadable, DownloadResult, Report, download
from gamdam.__main__ import readfile

DATA_DIR = Path(__file__).with_name("data")


@pytest.fixture
def annex_path(tmp_path: Path) -> Iterator[Path]:
    subprocess.run(["git", "init", tmp_path], check=True)
    subprocess.run(["git-annex", "init"], cwd=tmp_path, check=True)
    try:
        yield tmp_path
    finally:
        if subprocess.run(["git-annex", "uninit"], cwd=tmp_path).returncode != 0:
            for p in iterpath(tmp_path, dirs=False):
                os.chmod(p, os.stat(p).st_mode | stat.S_IWUSR)


@dataclass
class ResultSorter:
    successful: Dict[Path, Downloadable] = field(default_factory=dict)
    failed: Dict[Path, Downloadable] = field(default_factory=dict)

    async def subscriber(
        self, receiver: anyio.abc.ObjectReceiveStream[DownloadResult]
    ) -> None:
        async with receiver:
            async for r in receiver:
                if r.success:
                    self.successful[r.downloadable.path] = r.downloadable
                else:
                    self.failed[r.downloadable.path] = r.downloadable


def get_annex_metadata(repo: Path, fpath: Path) -> dict:
    r = subprocess.run(
        ["git-annex", "metadata", "--json", fpath],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
    )
    return cast(dict, json.loads(r.stdout)["fields"])


def get_annex_urls(repo: Path, fpath: Path) -> List[str]:
    r = subprocess.run(
        ["git-annex", "whereis", "--json", fpath],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
    )
    (web,) = [
        src for src in json.loads(r.stdout)["whereis"] if src["description"] == "web"
    ]
    return cast(List[str], web["urls"])


def test_download_successful(annex_path: Path) -> None:
    sorter = ResultSorter()

    async def runner(repo: Path, objects: AsyncIterator[Downloadable]) -> Report:
        async with anyio.create_task_group() as tg:
            sender, receiver = anyio.create_memory_object_stream(0)
            tg.start_soon(sorter.subscriber, receiver)
            return await download(repo, objects, subscriber=sender)

    with (DATA_DIR / "successful.jsonl").open() as fp:
        items = [Downloadable.parse_raw(line) for line in fp]
        fp.seek(0)
        report = anyio.run(runner, annex_path, readfile(fp))
    assert report.downloaded == len(items)
    assert report.failed == 0
    assert sorter.successful == {dl.path: dl for dl in items}
    assert sorter.failed == {}
    for dl in items:
        assert (annex_path / dl.path).exists()
        md = get_annex_metadata(annex_path, dl.path)
        for k, v in (dl.metadata or {}).items():
            assert md.get(k) == v
        expected_urls = [dl.url] + (dl.extra_urls or [])
        assert get_annex_urls(annex_path, dl.path) == expected_urls


def test_download_mixed(annex_path: Path) -> None:
    sorter = ResultSorter()
    items: List[Downloadable] = []
    expected = ResultSorter()

    with (DATA_DIR / "mixed.jsonl").open() as fp:
        for line in fp:
            data = json.loads(line)
            item = Downloadable.parse_obj(data["item"])
            items.append(item)
            if data["success"]:
                expected.successful[item.path] = item
            else:
                expected.failed[item.path] = item

    async def ayielder() -> AsyncIterator[Downloadable]:
        for i in items:
            yield i

    async def runner(repo: Path, objects: AsyncIterator[Downloadable]) -> Report:
        async with anyio.create_task_group() as tg:
            sender, receiver = anyio.create_memory_object_stream(0)
            tg.start_soon(sorter.subscriber, receiver)
            return await download(repo, objects, subscriber=sender)

    report = anyio.run(runner, annex_path, ayielder())
    assert sorter == expected
    assert report.downloaded == len(expected.successful)
    assert report.failed == len(expected.failed)
    for dl in expected.successful.values():
        assert (annex_path / dl.path).exists()
        md = get_annex_metadata(annex_path, dl.path)
        for k, v in (dl.metadata or {}).items():
            assert md.get(k) == v
        expected_urls = [dl.url] + (dl.extra_urls or [])
        assert get_annex_urls(annex_path, dl.path) == expected_urls
    for dl in expected.failed.values():
        assert not (annex_path / dl.path).exists()
