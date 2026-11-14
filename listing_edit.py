#!/usr/bin/env python3
"""上架编辑持久化：审核台编辑器的唯一写入通道（Q1/Q2 决策）。

- 所有人工编辑写回 spec JSON 的 `listing_edits` 区块（slot7 先例：spec 是资产之源，
  sync_to_db 重跑不丢；DB metrics 只是镜像）；
- 编辑即人工确认：清除 need_confirm，落 event（信任分级 manual > LLM）；
- approved（未 listed）也可编辑，不回退状态，仅落 event。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pipeline as P

ROOT = Path(__file__).parent


def _validate(edits: dict) -> str | None:
    title = (edits.get("ozon_title") or "").strip()
    if not title:
        return "商品标题不能为空"
    if len(title) > 200:
        return f"标题超长（{len(title)}>200 字符）"
    desc = (edits.get("description_ru") or "").strip()
    if len(desc) > 1000:
        return f"详情超长（{len(desc)}>1000 字符）"
    for k in ("length", "width", "height"):
        v = (edits.get("dimensions_cm") or {}).get(k)
        if v is not None and v <= 0:
            return f"尺寸 {k} 必须为正数"
    w = edits.get("weight_g")
    if w is not None and w <= 0:
        return "重量必须为正数"
    return None


def save(pid: int, edits: dict) -> str | None:
    """保存上架编辑。返回 None=成功，否则错误信息。"""
    err = _validate(edits)
    if err:
        return err
    with P.conn() as c:
        row = c.execute("SELECT metrics_json, state FROM products WHERE id=?",
                        (pid,)).fetchone()
    if not row:
        return "产品不存在"
    m = json.loads(row["metrics_json"] or "{}")
    sp = m.get("spec_path")
    if not sp or not (ROOT / sp).is_file():
        return "缺 spec 文件，无法保存"
    spec_file = ROOT / sp
    spec = json.loads(spec_file.read_text(encoding="utf-8"))

    edits = {**edits, "edited_at": P.now(), "source": "manual"}
    spec["listing_edits"] = edits
    spec_file.write_text(json.dumps(spec, ensure_ascii=False, indent=1),
                         encoding="utf-8")

    # 镜像回 DB + 编辑即人工确认（清 need_confirm）
    m["ozon_title"] = edits["ozon_title"].strip()
    if edits.get("description_ru"):
        m["description_ru"] = edits["description_ru"].strip()
    m["need_confirm"] = False
    m["listing_edited"] = True
    with P.conn() as c:
        c.execute("UPDATE products SET metrics_json=?, updated_at=? WHERE id=?",
                  (json.dumps(m, ensure_ascii=False), P.now(), pid))
        n_attr = len(edits.get("attributes") or [])
        price = (edits.get("pricing") or {}).get("price_cny")
        P._event(c, pid, None, row["state"],
                 f"上架编辑已保存（属性 {n_attr} 项"
                 + (f"，售价 ¥{price}" if price else "")
                 + "，need_confirm 已人工确认清除）")
    return None


def get_edits(metrics: dict) -> dict:
    """读该卡的 listing_edits（无则 {}）。"""
    sp = metrics.get("spec_path")
    if not sp or not (ROOT / sp).is_file():
        return {}
    try:
        spec = json.loads((ROOT / sp).read_text(encoding="utf-8"))
        return spec.get("listing_edits") or {}
    except (json.JSONDecodeError, OSError):
        return {}
