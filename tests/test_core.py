import time

from core import (
    parse_extra_pids,
    parse_extra_names,
    build_targets,
    aggregate_latest_by_name,
    dedup_process_items,
    filter_process_items,
    parse_targets_text,
)


def test_parse_extra_pids_dedup_and_order():
    assert parse_extra_pids("1, 2,2, 003, x, 4") == [1, 2, 3, 4]


def test_parse_extra_names_dedup_case_insensitive():
    assert parse_extra_names("Chrome, chrome, FireFox ,  ,EDGE") == ["Chrome", "FireFox", "EDGE"]


def test_build_targets_combines_primary_and_extras():
    targets = build_targets(123, "python", "7,nginx", "5,6,5", "nginx,python")
    # order preserved, duplicates removed
    assert {"pid": 123, "name": "python"} in targets
    assert {"pid": 5, "name": ""} in targets
    assert {"pid": 6, "name": ""} in targets
    assert {"pid": None, "name": "nginx"} in targets
    # duplicate name should be removed
    assert len([t for t in targets if t["name"] == "python"]) == 1


def test_aggregate_latest_by_name_averages_values():
    now = time.time()
    latest = {
        1: (now, 10.0, 100.0, "worker"),
        2: (now - 1, 30.0, 300.0, "worker"),
        3: (now, 50.0, 50.0, "scheduler"),
    }
    agg = aggregate_latest_by_name(latest)
    assert "worker" in agg
    ts, cpu, mem = agg["worker"]
    assert abs(cpu - 20.0) < 0.01
    assert abs(mem - 200.0) < 0.01
    assert ts == now  # latest timestamp across worker group
    assert agg["scheduler"][1] == 50.0


def test_dedup_process_items():
    items = [(1, "a"), (1, "a"), (2, "b")]
    assert dedup_process_items(items) == [(1, "a"), (2, "b")]


def test_filter_process_items_by_name_and_pid():
    items = [(12, "Chrome"), (34, "Python"), (56, "chrome-helper")]
    assert filter_process_items(items, "chrome") == [(12, "Chrome"), (56, "chrome-helper")]
    assert filter_process_items(items, "34") == [(34, "Python")]
    assert filter_process_items(items, "xyz") == []


def test_parse_targets_text_mixes_pids_and_names():
    pids, names = parse_targets_text("12, chrome,12,python")
    assert pids == [12]
    assert names == ["chrome", "python"]
