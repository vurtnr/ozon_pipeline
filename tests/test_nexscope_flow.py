#!/usr/bin/env python3
"""Nexscope 词库流程测试：fixture 驱动，零积分消耗。

运行: python3 -m pytest tests/test_nexscope_flow.py -q
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import pipeline as P
import nexscope_client as NX
import keyword_library as lib
import select_products as SP
import trend_check as TC


def api_row(query, volume=20000, growth=3.0, competing=500, price=800.0,
            returns=10.0, n_products=3, cn=""):
    return {"query": query, "queryCn": cn, "searchVolume": volume,
            "count30GrowthRate": growth,
            "competingProducts": competing, "avgPrice": price,
            "returnCancellationRate": returns,
            "products": [{"ozonId": f"100{i}", "sku": f"s{i}",
                          "title": f"{query} 商品{i}",
                          "imageUrl": f"https://img/{i}.jpg"}
                         for i in range(n_products)]}


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "DB", tmp_path / "test.db")
    with P.conn() as c:
        c.executescript(P.SCHEMA)
        P._migrate(c)
    return tmp_path


def test_build_and_cursor(db, monkeypatch):
    calls = []
    def fake_search(keyword, page_size=50):
        calls.append(keyword)
        return [api_row(f"{keyword}-词{i}") for i in range(3)]
    monkeypatch.setattr(NX, "market_keyword_search", fake_search)
    monkeypatch.setattr(lib, "whitelist", lambda: ["类目A", "类目B", "类目C"])
    lib.build(limit=2)
    with P.conn() as c:
        kws = c.execute("SELECT COUNT(*) n FROM keywords").fetchone()["n"]
        seeds = {r["seed"] for r in c.execute("SELECT seed FROM harvested_seeds")}
    assert kws == 6 and seeds == {"类目A", "类目B"}
    lib.build(limit=2)  # 游标推进，只 harvest 类目C
    assert calls[-1] == "类目C" and len(calls) == 3


def test_consume_funnel_and_no_double_emit(db, monkeypatch):
    rows = [api_row("合格词"),
            api_row("退货超标", returns=50.0),
            api_row("竞争超标", competing=99999),
            api_row("价格过低", price=100.0),
            api_row("量不足", volume=100)]
    monkeypatch.setattr(NX, "market_keyword_search", lambda k, page_size=50: rows)
    monkeypatch.setattr(lib, "whitelist", lambda: ["类目A"])
    lib.build(limit=1)
    picked = lib.consume(5)
    assert [r["query"] for r in picked] == ["合格词"]
    assert json.loads(picked[0]["top_products"])[0]["ozonId"] == "1000"
    assert lib.consume(5) == []  # 已消费不再出


def test_select_products_schema(db, tmp_path, monkeypatch):
    monkeypatch.setattr(NX, "market_keyword_search",
                        lambda k, page_size=50: [api_row("дозатор", cn="皂液器")])
    monkeypatch.setattr(lib, "whitelist", lambda: ["类目A"])
    lib.build(limit=1)
    monkeypatch.setattr(SP, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["select_products.py", "--n", "1"])
    SP.main()
    cand = json.load(open(tmp_path / f"data/candidates-{__import__('datetime').date.today()}.json"))
    c = cand["candidates"][0]
    # 与旧版 schema 键一致（下游 sync_to_db/sourcing_agent 契约）
    assert set(c) == {"query", "queryCn", "searchVolume", "growth30d",
                      "competingProducts", "avgPrice", "ca", "returnRate",
                      "categoryId", "topProducts"}
    assert c["queryCn"] == "皂液器"
    tasks = json.load(open(tmp_path / f"data/image-search-tasks-{__import__('datetime').date.today()}.json"))
    assert tasks[0]["imageUrl"] == "https://img/0.jpg"


def test_trend_check_alerts_collapse(db, monkeypatch):
    # 在监品 + 基线词
    with P.conn() as c:
        c.execute("INSERT INTO products(query,ozon_id,state,created_at,updated_at)"
                  " VALUES('дозатор','1','approved','2026-01-01','2026-01-01')")
        c.execute("INSERT INTO keywords(query,search_volume,avg_price,fetched_at)"
                  " VALUES('дозатор',20000,800.0,'2026-01-01')")
    # 重查：量跌 70%（促销退潮）→ 应告警
    monkeypatch.setattr(NX, "market_keyword_search",
                        lambda k, page_size=50: [api_row("дозатор", volume=6000)])
    alerts = TC.check()
    assert len(alerts) == 1 and "量 20000→6000" in alerts[0]
    with P.conn() as c:
        ev = c.execute("SELECT note FROM events WHERE to_state='trend_alert'").fetchone()
    assert ev and "trend 告警" in ev["note"]


def test_trend_check_healthy_no_alert(db, monkeypatch):
    with P.conn() as c:
        c.execute("INSERT INTO products(query,ozon_id,state,created_at,updated_at)"
                  " VALUES('дозатор','1','listed','2026-01-01','2026-01-01')")
        c.execute("INSERT INTO keywords(query,search_volume,avg_price,fetched_at)"
                  " VALUES('дозатор',20000,800.0,'2026-01-01')")
    monkeypatch.setattr(NX, "market_keyword_search",
                        lambda k, page_size=50: [api_row("дозатор", volume=21000)])
    assert TC.check() == []


def test_credit_fuse(db, monkeypatch):
    monkeypatch.setattr(NX, "spent_today", lambda: NX.DAILY_CREDIT_CAP)
    with pytest.raises(NX.CreditFuseError):
        NX.market_keyword_search("x")
