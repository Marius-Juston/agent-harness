# ruff: noqa: S101
import multiprocessing as mp

from journal import append_entry


def _writer(path, k):
    for i in range(50):
        append_entry(path, f"{k}-{i}-" + "x" * 2000)


def test_concurrent_entries_stay_whole(tmp_path):
    p = tmp_path / "journal.txt"
    procs = [mp.get_context("spawn").Process(target=_writer, args=(p, k)) for k in range(4)]
    for pr in procs:
        pr.start()
    for pr in procs:
        pr.join()
    lines = p.read_text().splitlines()
    assert len(lines) == 200
    assert all(line.endswith("x" * 2000) for line in lines)
