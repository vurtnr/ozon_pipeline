#!/usr/bin/env python3
"""上架编辑持久化测试：spec 回写 + need_confirm 清除 + event（临时 DB，不碰真库）。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import pipeline as P
import listing_edit as LE


@pytest.fixture
def env(tmp_path, monkeypatch):
    """临时 DB + 临时 spec 文件。"""
    monkeypatch.setattr(P, "DB", tmp_path / "test.db")
    monkeypatch.setattr(LE, "ROOT", tmp_path)
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "slots": [], "_meta": {"query": "q"},
        "ozon_title": "Old title", "need_confirm": True,
    }), encoding="utf-8")
    P.cmd_init()
    with P.conn() as c:
        c.execute("INSERT INTO products(query,ozon_id,state,metrics_json,"
                  "created_at,updated_at) VALUES('q','1','review_pending',?,?,?)",
                  (json.dumps({"spec_path": "spec.json", "need_confirm": True,
                               "ozon_title": "Old title"}),
                   P.now(), P.now()))
    return tmp_path


def _edits():
    return {"ozon_title": "Новый титул", "description_ru": "Описание",
            "dimensions_cm": {"length": 20.0, "width": 15.0, "height": 8.0},
            "weight_g": 500.0,
            "attributes": [{"id": 8229, "complex_id": 0,
                            "values": [{"dictionary_value_id": 594158428,
                                        "value": "Органайзер"}]}],
            "pricing": {"price_cny": 83.31, "currency": "CNY"}}


def test_save_writes_spec_and_clears_need_confirm(env):
    assert LE.save(1, _edits()) is None
    spec = json.loads((env / "spec.json").read_text(encoding="utf-8"))
    le = spec["listing_edits"]
    assert le["ozon_title"] == "Новый титул"
    assert le["source"] == "manual"
    assert le["weight_g"] == 500.0
    assert le["pricing"]["price_cny"] == 83.31
    with P.conn() as c:
        row = c.execute("SELECT metrics_json FROM products WHERE id=1").fetchone()
        m = json.loads(row["metrics_json"])
        assert m["need_confirm"] is False
        assert m["ozon_title"] == "Новый титул"
        assert m["listing_edited"] is True
        ev = c.execute("SELECT note FROM events WHERE product_id=1"
                       " ORDER BY id DESC").fetchone()
        assert "上架编辑已保存" in ev["note"]


def test_title_required(env):
    e = _edits()
    e["ozon_title"] = "  "
    assert "标题" in LE.save(1, e)


def test_title_too_long(env):
    e = _edits()
    e["ozon_title"] = "x" * 201
    assert "超长" in LE.save(1, e)


def test_negative_weight_rejected(env):
    e = _edits()
    e["weight_g"] = -5
    assert "重量" in LE.save(1, e)


def test_get_edits_roundtrip(env):
    LE.save(1, _edits())
    with P.conn() as c:
        m = json.loads(c.execute("SELECT metrics_json FROM products WHERE id=1")
                       .fetchone()["metrics_json"])
    got = LE.get_edits(m)
    assert got["ozon_title"] == "Новый титул"
    assert got["attributes"][0]["id"] == 8229
