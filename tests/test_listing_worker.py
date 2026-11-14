#!/usr/bin/env python3
"""listing_worker 组装 import payload 的纯函数测试（不调 Ozon/OSS）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import listing_worker as LW

M = {"sourcing": {"offer_id": "1024361947021"}}
SPEC = {"_meta": {"categoryId": "17000000_17028714_594158428"}}
EDITS = {
    "ozon_title": "Органайзер раздвижной",
    "description_ru": "Описание",
    "dimensions_cm": {"length": 35.0, "width": 25.0, "height": 6.0},
    "weight_g": 300.0,
    "attributes": [
        {"id": 9048, "complex_id": 0, "values": [{"value": "Модель X"}]},
    ],
    "pricing": {"price_cny": 31.54, "old_price_cny": 63.09},
}
IMAGES = ["https://oss.example/slot1.png", "https://oss.example/slot2.png"]


def test_build_item_core_fields():
    item = LW.build_item(2, M, SPEC, EDITS, IMAGES)
    assert item["offer_id"] == "ozp-2"  # 稳定幂等：重测=更新而非新建
    assert item["name"] == "Органайзер раздвижной"
    assert item["description"] == "Описание"
    assert item["description_category_id"] == 17028714
    assert item["type_id"] == 594158428
    assert item["currency_code"] == "CNY"
    assert item["vat"] == "0"
    assert item["price"] == "31.54"
    assert item["old_price"] == "63.09"


def test_dimensions_cm_to_mm_and_weight():
    item = LW.build_item(2, M, SPEC, EDITS, IMAGES)
    assert item["depth"] == 350 and item["width"] == 250 and item["height"] == 60
    assert item["dimension_unit"] == "mm"
    assert item["weight"] == 300 and item["weight_unit"] == "g"


def test_brand_defaults_to_no_brand():
    item = LW.build_item(2, M, SPEC, EDITS, IMAGES)
    brand = next(a for a in item["attributes"] if a["id"] == 85)
    assert brand["values"][0]["dictionary_value_id"] == 126745801
    assert brand["values"][0]["value"] == "Нет бренда"


def test_existing_brand_not_overwritten():
    edits = {**EDITS, "attributes": EDITS["attributes"] + [
        {"id": 85, "complex_id": 0,
         "values": [{"dictionary_value_id": 5058867, "value": "LEGO"}]}]}
    item = LW.build_item(2, M, SPEC, edits, IMAGES)
    brands = [a for a in item["attributes"] if a["id"] == 85]
    assert len(brands) == 1
    assert brands[0]["values"][0]["value"] == "LEGO"


def test_first_image_is_primary():
    item = LW.build_item(2, M, SPEC, EDITS, IMAGES)
    assert item["images"][0].endswith("slot1.png")  # 首图=主图，无需 primary_image


def test_missing_required_raises():
    with pytest.raises(ValueError):
        LW.build_item(2, M, SPEC, {**EDITS, "weight_g": None}, IMAGES)
    with pytest.raises(ValueError):
        LW.build_item(2, M, SPEC, EDITS, [])  # 无图不上架
