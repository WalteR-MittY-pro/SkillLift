from concurrent.futures import ProcessPoolExecutor
import multiprocessing
from types import SimpleNamespace

import pytest

from experiments.revision2026 import concurrency


def _request(root):
    return concurrency.wait_for_api_request("deepseek-flash", root=root, interval=0.05)


def test_pacing_is_shared_across_processes(tmp_path):
    with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context("spawn")) as pool:
        starts = sorted(pool.map(_request, [str(tmp_path)] * 8))
    assert all(b - a >= 0.049 for a, b in zip(starts, starts[1:]))


def test_default_gap_idle_requests_and_separate_provider_pools(tmp_path, monkeypatch):
    clock, sleeps = [100.0], []
    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    monkeypatch.setattr(concurrency, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep))
    assert concurrency.wait_for_api_request("deepseek", tmp_path) == 100
    assert concurrency.wait_for_api_request("deepseek-flash", tmp_path) == 100.5
    assert sleeps == [0.5]
    assert concurrency.wait_for_api_request("glm", tmp_path) == 100.5
    clock[0] += 2
    assert concurrency.wait_for_api_request("deepseek", tmp_path) == 102.5
    assert sleeps == [0.5]


@pytest.mark.parametrize("stamp", ["broken", "999999999999", "nan"])
def test_invalid_or_future_timestamp_does_not_block(tmp_path, monkeypatch, stamp):
    folder = tmp_path / "deepseek"
    folder.mkdir()
    (folder / "last-start").write_text(stamp)
    monkeypatch.setattr(concurrency, "time", SimpleNamespace(monotonic=lambda: 100.0,
                       sleep=lambda _: pytest.fail("stale clock must not delay requests")))
    assert concurrency.wait_for_api_request("deepseek", tmp_path) == 100
