#!/usr/bin/env python3
"""ozon_client 属性拉取：与 Ozon 后台「特征」页对齐（含可选/多值属性）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import ozon_client as OC

FAKE = {"result": [
    {"id": 6383, "name": "Материал", "is_required": False,
     "is_aspect": False, "is_collection": True, "dictionary_id": 1503,
     "type": "String"},
    {"id": 85, "name": "Бренд", "is_required": True,
     "is_aspect": False, "is_collection": False, "dictionary_id": 1,
     "type": "String"},
    {"id": 10097, "name": "Название цвета", "is_required": False,
     "is_aspect": True, "is_collection": False, "dictionary_id": 0,
     "type": "String"},
]}


def test_get_attributes_includes_optional_and_collection(monkeypatch, tmp_path):
    """可选属性与多值属性都保留（后台展示），排序：必填→筛选→其他。"""
    monkeypatch.setattr(OC, "CACHE", tmp_path)
    monkeypatch.setattr(OC, "_post", lambda path, body: FAKE)
    attrs = OC.get_attributes(1, 2)
    assert [a["id"] for a in attrs] == [85, 10097, 6383]
    assert attrs[2]["is_collection"] is True
    # 二次调用走磁盘缓存（不再请求）
    monkeypatch.setattr(OC, "_post",
                        lambda *a: pytest.fail("不应再次请求"))
    assert OC.get_attributes(1, 2) == attrs
