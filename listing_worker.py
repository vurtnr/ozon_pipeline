#!/usr/bin/env python3
"""listing_worker：approved → listed，最小上架链路（ROADMAP V1 任务 7）。

    python3 listing_worker.py --pid 2 [--dry-run]

流程：读 spec + listing_edits → 组 /v3/product/import payload
（尺寸 cm→mm、首图=主图、缺品牌注入「Нет бренда」）
→ 本地 slot 图传阿里云 OSS（公网 URL，Ozon 主动来拉）
→ import → 轮询 task → 落 ozon_product_id + 状态 listed。

offer_id = ozp-{pid} 稳定幂等：重跑=更新同一商品，不会重复建卡。
"""
import argparse
import json
import sys
import time
from pathlib import Path

import oss2

sys.path.insert(0, str(Path(__file__).parent))
import pipeline as P
import listing_edit as LE
import ozon_client as OC
from config import (OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET,
                    OSS_BUCKET, OSS_ENDPOINT)

ROOT = Path(__file__).parent
NO_BRAND = {"dictionary_value_id": 126745801, "value": "Нет бренда"}  # 全类目通用无品牌值


def build_item(pid: int, metrics: dict, spec: dict, edits: dict,
               image_urls: list[str]) -> dict:
    """组 import item（纯函数，便于测试）。缺必填直接 ValueError，不带病上架。"""
    if not image_urls:
        raise ValueError("无公网图片，不上架")
    dims = edits.get("dimensions_cm") or {}
    weight = edits.get("weight_g")
    if not all(dims.get(k) for k in ("length", "width", "height")) or not weight:
        raise ValueError("尺寸/重量必填且非零（编辑器②）")
    pricing = edits.get("pricing") or {}
    if not pricing.get("price_cny"):
        raise ValueError("缺定价（编辑器③先计算建议价）")
    desc_id, type_id = OC.parse_category_ids(spec.get("_meta", {}).get("categoryId", ""))
    attrs = [dict(a) for a in edits.get("attributes") or []]
    if not any(a["id"] == 85 for a in attrs):
        attrs.append({"id": 85, "complex_id": 0, "values": [dict(NO_BRAND)]})
    return {
        "offer_id": f"ozp-{pid}",
        "name": edits["ozon_title"],
        "description": edits.get("description_ru", ""),
        "description_category_id": desc_id,
        "type_id": type_id,
        "currency_code": "CNY",
        "vat": "0",
        "price": str(pricing["price_cny"]),
        "old_price": str(pricing.get("old_price_cny") or ""),
        "depth": round(dims["length"] * 10),
        "width": round(dims["width"] * 10),
        "height": round(dims["height"] * 10),
        "dimension_unit": "mm",
        "weight": round(weight),
        "weight_unit": "g",
        "attributes": attrs,
        "images": image_urls,  # slot1 在首位 = 主图（官方：未指定 primary_image 时首图为主）
    }


def upload_images(pid: int, metrics: dict) -> list[str]:
    """本地 slot 图 → OSS，返回按 slot 序号排序的公网 URL。"""
    assets = metrics.get("assets_dir")
    if not assets:
        raise ValueError("无 assets_dir，套图未生成")
    files = sorted((ROOT / assets).glob("slot[0-9].png"),
                   key=lambda p: int(p.stem[4:]))
    if not files:
        raise ValueError(f"{assets} 下无 slot 图")
    auth = oss2.Auth(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET)
    bucket = oss2.Bucket(auth, f"https://{OSS_ENDPOINT}", OSS_BUCKET)
    urls = []
    for f in files:
        key = f"ozon-listing/{pid}/{f.name}"
        bucket.put_object_from_file(key, str(f))
        urls.append(f"https://{OSS_BUCKET}.{OSS_ENDPOINT}/{key}")
    return urls


def list_product(pid: int, dry_run: bool = False) -> dict:
    with P.conn() as c:
        row = c.execute("SELECT state, metrics_json FROM products WHERE id=?",
                        (pid,)).fetchone()
    if not row:
        raise ValueError(f"产品 {pid} 不存在")
    if row["state"] not in ("approved", "listed"):
        raise ValueError(f"状态 {row['state']} 不可上架（需先过审 approved）")
    m = json.loads(row["metrics_json"] or "{}")
    spec = json.loads((ROOT / m["spec_path"]).read_text(encoding="utf-8"))
    edits = LE.get_edits(m)
    if not edits:
        raise ValueError("未做上架编辑（编辑器保存后才可上架）")

    if dry_run:
        item = build_item(pid, m, spec, edits,
                          [f"https://example/slot{i}.png" for i in (1, 2)])
        print(json.dumps(item, ensure_ascii=False, indent=1))
        return {"dry_run": True}

    urls = upload_images(pid, m)
    item = build_item(pid, m, spec, edits, urls)
    r = OC._post("/v3/product/import", {"items": [item]})
    task_id = r["result"]["task_id"]
    print(f"import task_id={task_id}，轮询…")
    for _ in range(24):  # 最多 2 分钟
        time.sleep(5)
        info = OC._post("/v1/product/import/info", {"task_id": task_id})
        items = info["result"]["items"]
        if not items:
            continue
        it = items[0]
        if it.get("status") == "imported":
            ozon_pid = it["product_id"]
            m["ozon_product_id"] = ozon_pid
            with P.conn() as c:
                c.execute("UPDATE products SET metrics_json=?, updated_at=? WHERE id=?",
                          (json.dumps(m, ensure_ascii=False), P.now(), pid))
                if row["state"] == "approved":
                    P._event(c, pid, "approved", "listed",
                             f"listing_worker 上架成功 product_id={ozon_pid}")
                c.execute("UPDATE products SET state='listed' WHERE id=?", (pid,))
            print(f"✅ 已上架 product_id={ozon_pid}（审核通过后仍需设库存才在售）")
            return {"product_id": ozon_pid}
        if it.get("status") == "failed":
            raise RuntimeError(f"import 失败: {json.dumps(it.get('errors'), ensure_ascii=False)}")
    raise TimeoutError("import 轮询超时（2 分钟），task_id=" + str(task_id))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    list_product(args.pid, args.dry_run)
