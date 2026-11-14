#!/usr/bin/env python3
"""select_from_pool 品级选品测试：mock seerfar_client，零积分。

契约：白名单切片下推 → check_record 兜底 → sku 去重（DB+run内）→ Run 目录落盘。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import pipeline as P
import select_from_pool as SFP


def rec(sku, weight=300.0, price=1000.0, path="Одежда/Носки/Мужские", **kw):
    base = {"sku": sku, "title": f"商品{sku}", "imageUrl": f"https://img/{sku}.jpg",
            "productUrl": f"https://ozon/p/{sku}", "price": price, "weight": weight,
            "sales": 1000, "revenue": 1000000, "reviewRating": 4.8, "reviewCount": 50,
            "upDays": 100, "sellerType": 0, "returnCancellationRate": None,
            "categoryInfo": {"category": {"crossBorderSellable": True},
                             "cnTitlePath": "服装 > 内衣 > 袜子"}}
    base.update(kw)
    return base


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "DB", tmp_path / "test.db")
    with P.conn() as c:
        c.executescript(P.SCHEMA)
        P._migrate(c)
    return tmp_path


def test_build_body_pushes_filters_down():
    body = SFP.build_body(["1_2_3"], page_number=1)
    assert body["weight"]["max"] == 500
    assert body["price"] == {"min": 300, "max": 5000}
    assert body["reviewCount"]["min"] == 10
    assert body["categoryIds"] == ["1_2_3"]
    assert body["page"]["pageSize"] == 100
    assert body["variationsMerge"] == 1


def test_batches_split():
    assert list(SFP.batches(list(range(120)), 50)) == [list(range(50)), list(range(50, 100)), list(range(100, 120))]


def test_filter_records_policy_and_run_dedup():
    recs = [rec(1), rec(1), rec(2, weight=9000), rec(3)]  # 1重复、2超重
    out = SFP.filter_records(recs)
    skus = [c["ozonId"] for c in out]
    assert skus == ["1", "3"]


def test_to_candidate_contract():
    c = SFP.to_candidate(rec(42))
    assert c["ozonId"] == "42"
    assert c["imageUrl"] == "https://img/42.jpg"
    assert c["query"]  # 下游 sourcing 需要
    assert c["categoryPath"] == "服装 > 内衣 > 袜子"


def test_run_dedups_existing_products_and_writes_run_dir(db, tmp_path, monkeypatch):
    with P.conn() as c:  # 已存在 ozon_id=1，应被去重
        c.execute("INSERT INTO products(query,ozon_id,state,created_at,updated_at)"
                  " VALUES('q','1','listed','2026-01-01','2026-01-01')")
    monkeypatch.setattr(SFP, "ROOT", tmp_path)
    calls = []

    def fake_report(body, run_id=None):
        calls.append(body)
        return {"total": 2, "records": [rec(1), rec(2)]}
    monkeypatch.setattr(SFP.SF, "product_report", fake_report)

    out = SFP.run(["1_2_3"], run_id="test-run")
    assert len(calls) == 1
    run_dir = tmp_path / "data/runs/test-run"
    cand = json.load(open(run_dir / "candidates.json"))
    assert [c["ozonId"] for c in cand["candidates"]] == ["2"]  # 1 被 DB 去重
    tasks = json.load(open(run_dir / "image-search-tasks.json"))
    assert tasks[0]["imageUrl"] == "https://img/2.jpg"
    assert tasks[0]["ozonId"] == "2"
    assert (run_dir / "shortlist.md").exists()
    assert out["kept"] == 1 and out["run_id"] == "test-run"


def test_run_respects_max_calls(db, tmp_path, monkeypatch):
    monkeypatch.setattr(SFP, "ROOT", tmp_path)
    calls = []

    def fake_report(body, run_id=None):
        calls.append(body)
        return {"total": 1, "records": [rec(len(calls))]}
    monkeypatch.setattr(SFP.SF, "product_report", fake_report)
    leaf_ids = [f"b{i}" for i in range(200)]  # 4 批(50/批)，但 max_calls=2
    SFP.run(leaf_ids, run_id="r", max_calls=2)
    assert len(calls) == 2


def _write_run(tmp_path, run_id, candidates):
    d = tmp_path / "data/runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "candidates.json").write_text(json.dumps({"candidates": candidates}))
    return d


def test_approve_inserts_and_advances_to_scored(db, tmp_path, monkeypatch):
    monkeypatch.setattr(SFP, "ROOT", tmp_path)
    _write_run(tmp_path, "r1", [SFP.to_candidate(rec(1)), SFP.to_candidate(rec(2))])
    out = SFP.approve("r1")
    assert out["inserted"] == 2
    with P.conn() as c:
        rows = c.execute("SELECT ozon_id,state,metrics_json FROM products ORDER BY ozon_id").fetchall()
    assert [r["ozon_id"] for r in rows] == ["1", "2"]
    assert all(r["state"] == "scored" for r in rows)
    m = json.loads(rows[0]["metrics_json"])
    assert m["selection"]["categoryPath"] == "服装 > 内衣 > 袜子"


def test_approve_skips_existing_active_and_dead(db, tmp_path, monkeypatch):
    monkeypatch.setattr(SFP, "ROOT", tmp_path)
    with P.conn() as c:
        c.execute("INSERT INTO products(query,ozon_id,state,created_at,updated_at)"
                  " VALUES('q','1','listed','2026-01-01','2026-01-01')")  # 活跃
        c.execute("INSERT INTO products(query,ozon_id,state,created_at,updated_at)"
                  " VALUES('q','2','dead','2026-01-01','2026-01-01')")    # 毙过不复活
    _write_run(tmp_path, "r2", [SFP.to_candidate(rec(1)), SFP.to_candidate(rec(2)),
                                SFP.to_candidate(rec(3))])
    out = SFP.approve("r2")
    assert out["inserted"] == 1  # 只有 3 入库
    with P.conn() as c:
        n = c.execute("SELECT COUNT(*) n FROM products").fetchone()["n"]
    assert n == 3  # 原有2 + 新增1


def test_approve_generates_sourcing_tasks_for_inserted(db, tmp_path, monkeypatch):
    monkeypatch.setattr(SFP, "ROOT", tmp_path)
    _write_run(tmp_path, "r3", [SFP.to_candidate(rec(7))])
    SFP.approve("r3")
    tasks = json.load(open(tmp_path / "data/runs/r3/sourcing-approved.json"))
    assert tasks[0]["ozonId"] == "7" and tasks[0]["imageUrl"] == "https://img/7.jpg"
