#!/usr/bin/env python3
"""Ozon Seller API 薄封装：审核台编辑器取类目上架属性与字典值。

只覆盖编辑器所需三个方法（其余见 docs/research/ozon-listing-api.md）：
- /v1/description-category/attribute        类目属性定义（is_required/is_aspect）
- /v1/description-category/attribute/values 字典值（dictionary_value_id）
属性定义按类目磁盘缓存（data/attr-cache/），字典值按需实时拉取。
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import OZON_API_KEY, OZON_CLIENT_ID

ROOT = Path(__file__).parent
CACHE = ROOT / "data/attr-cache"
API = "https://api-seller.ozon.ru"
HEADERS = {"Client-Id": OZON_CLIENT_ID, "Api-Key": OZON_API_KEY,
           "Content-Type": "application/json"}


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(),
                                 method="POST", headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Ozon {path} → {e.code}: {e.read().decode()[:300]}")


def parse_category_ids(category_id_path: str) -> tuple[int, int]:
    """spec _meta.categoryId 下划线路径 → (description_category_id, type_id)。
    与 category_tree.py 的拼接规则一致：倒数第二段=description_category_id，末段=type_id。"""
    parts = [p for p in category_id_path.split("_") if p]
    if len(parts) < 2:
        raise ValueError(f"categoryId 无法解析: {category_id_path!r}")
    return int(parts[-2]), int(parts[-1])


def get_attributes(desc_id: int, type_id: int, refresh: bool = False) -> list[dict]:
    """类目上架属性全量（与后台「特征」页对齐，含可选/多值），按类目缓存 v2。"""
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE / f"{desc_id}_{type_id}.json"
    if cache_file.is_file() and not refresh:
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        if isinstance(cached, dict) and cached.get("version") == 2:
            return cached["attributes"]
        # 旧缓存（list，仅必填+筛选）→ 重拉
    r = _post("/v1/description-category/attribute",
              {"description_category_id": desc_id, "type_id": type_id,
               "language": "RU"})
    rz = _post("/v1/description-category/attribute",
               {"description_category_id": desc_id, "type_id": type_id,
                "language": "ZH_HANS"})  # 中文名，仅用于审核台展示
    zh = {a["id"]: a.get("name", "") for a in rz.get("result", [])}
    attrs = []
    for a in r.get("result", []):
        attrs.append({
            "id": a["id"],
            "name": a.get("name", ""),
            "name_cn": zh.get(a["id"], ""),
            "type": a.get("type", ""),
            "is_required": bool(a.get("is_required")),
            "is_aspect": bool(a.get("is_aspect")),
            "is_collection": bool(a.get("is_collection")),
            "dictionary_id": a.get("dictionary_id", 0),
        })
    attrs.sort(key=lambda x: (not x["is_required"], not x["is_aspect"], x["id"]))
    cache_file.write_text(
        json.dumps({"version": 2, "attributes": attrs},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    return attrs


def get_attr_values(desc_id: int, type_id: int, attr_id: int,
                    query: str = "", limit: int = 50) -> list[dict]:
    """字典属性的可选值 [{id, value}]。query 过滤（品牌等上千条需搜索）。"""
    body = {"description_category_id": desc_id, "type_id": type_id,
            "attribute_id": attr_id, "limit": limit}
    if query:
        body["query"] = query
    r = _post("/v1/description-category/attribute/values", body)
    return [{"id": v["id"], "value": v.get("value", "")}
            for v in r.get("result", [])]
