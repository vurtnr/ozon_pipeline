#!/usr/bin/env python3
"""同一性闸 + slot2 中文闸测试（kovrik→冰箱除味盒 事故的回归锁）。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import sourcing_agent as SA
import image_worker as IW


def _fake_text_search(products):
    return {"success": True,
            "data": {"data": {"similar_products": products}}}


def _prod(title, sold, img):
    return {"title": title, "price": 10, "sold_count": sold,
            "similarity_score": 0.5, "detail_url": f"https://detail.1688.com/offer/{sold}.html",
            "image_url": img}


def test_identity_gate_skips_wrong_product(tmp_path, monkeypatch):
    """除味盒被闸拦下，排到后面的真垫子入选（kovrik 事故复现）。"""
    products = [_prod("悬挂式冰箱除味盒", 90000, "https://img/deodorizer.jpg"),
                _prod("冰箱保鲜垫防滑", 500, "https://img/mat.jpg")]
    monkeypatch.setattr(SA, "run_skill", lambda a: _fake_text_search(products))
    monkeypatch.setattr(SA, "vl_same_product",
                        lambda oz, url, tmp: "mat" in url)
    r = SA.search_text("冰箱保鲜垫", ozon_image="ozon.jpg", tmp=tmp_path)
    assert r["ok"] and [s["title"] for s in r["suppliers"]] == ["冰箱保鲜垫防滑"]


def test_identity_gate_all_rejected_means_no_source(tmp_path, monkeypatch):
    monkeypatch.setattr(SA, "run_skill",
                        lambda a: _fake_text_search([_prod("除味盒", 90000, "https://img/a.jpg")]))
    monkeypatch.setattr(SA, "vl_same_product", lambda *a: False)
    r = SA.search_text("冰箱保鲜垫", ozon_image="ozon.jpg", tmp=tmp_path)
    assert r["ok"] is False


def test_no_ozon_image_keeps_legacy_behavior(tmp_path, monkeypatch):
    """不传 ozon_image 时不过闸（--raw 等旧路径行为不变）。"""
    monkeypatch.setattr(SA, "run_skill",
                        lambda a: _fake_text_search([_prod("除味盒", 90000, "https://img/a.jpg")]))
    monkeypatch.setattr(SA, "vl_same_product",
                        lambda *a: pytest.fail("不应触发同一性闸"))
    r = SA.search_text("冰箱保鲜垫")
    assert r["ok"] and len(r["suppliers"]) == 1


def _minimal_spec(tmp_path):
    p = tmp_path / "spec.json"
    json.dump({"_meta": {"title1688": "", "query": ""}, "slots": []},
              open(p, "w", encoding="utf-8"))
    return str(p)


@pytest.fixture()
def _mock_vl_dims(monkeypatch):
    import dimensions
    monkeypatch.setattr(dimensions, "extract_dimensions", lambda img: "")


def test_slot2_supplier_image_with_text_falls_back_to_ai(tmp_path, monkeypatch, _mock_vl_dims):
    """供应商原图含文字 → slot2 回退 AI 白底生成。"""
    monkeypatch.setattr(IW, "_vl_has_text", lambda img: True)
    def fake_api(prompt, src, out_path, style_ref=None):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"png")
        return {"usage": None}
    monkeypatch.setattr(IW, "call_api", fake_api)
    recs = IW.generate_slots(_minimal_spec(tmp_path), "x.jpg",
                             tmp_path / "gen", only={"slot2"})
    assert recs[0]["ok"] and recs[0]["template"] == "slot2_white_bg_v1"
    assert (tmp_path / "gen/slot2.png").exists()


def test_slot2_clean_supplier_image_used_directly(tmp_path, monkeypatch, _mock_vl_dims):
    """无文字的干净原图 → 直接贴白底画布，零成本。"""
    from PIL import Image
    src = tmp_path / "clean.jpg"
    Image.new("RGB", (800, 800), "white").save(src)
    monkeypatch.setattr(IW, "_vl_has_text", lambda img: False)
    monkeypatch.setattr(IW, "call_api",
                        lambda *a, **k: pytest.fail("不应调 AI 生图"))
    recs = IW.generate_slots(_minimal_spec(tmp_path), str(src),
                             tmp_path / "gen", only={"slot2"})
    assert recs[0]["ok"] and recs[0]["template"] == "supplier_original"
    assert recs[0]["cost_usd"] == 0
    assert (tmp_path / "gen/slot2.png").exists()
