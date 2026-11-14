#!/usr/bin/env python3
"""品级选品模块：Seerfar 商品池 → 硬过滤 → 去重 → Run 目录落盘。

输入是「目标叶子类目 id 切片」（由上层计划/agent 给定），不是全量白名单——
全量 6354 叶子分批需上百次调用，违背每 Run 预算 [user-decided Q3]。

链路：build_body 下推（weight/price/reviewCount + categoryIds，PoC 已实测 100% 命中）
→ check_record 兜底（crossBorder/禁售词/upDays/退货率）
→ sku 去重（run 内 + products 表已有 ozon_id）
→ data/runs/<run_id>/ 落盘 candidates.json + image-search-tasks.json + shortlist.md。

Run 目录隔离 [plan-review A2]，不再用全局日期文件。下游 sourcing_agent 吃 {query,imageUrl,ozonId}。
LLM 排序是独立后续切片；本模块输出按 revenue DESC 的确定性候选清单。
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pipeline as P
import seerfar_client as SF
from selection_policy import check_record, WEIGHT_MAX_G, PRICE_MIN_RUB, PRICE_MAX_RUB, MIN_REVIEW_COUNT

ROOT = Path(__file__).parent
BATCH = 50          # categoryIds 每批叶子数（数组上限未实测，保守 50，可下调）
PAGE_SIZE = 100


def build_body(leaf_ids: list[str], page_number: int = 1) -> dict:
    """组装请求体：全部硬约束尽量下推服务端（过滤不额外计费）。"""
    return {
        "weight": {"max": WEIGHT_MAX_G},
        "price": {"min": PRICE_MIN_RUB, "max": PRICE_MAX_RUB},
        "reviewCount": {"min": MIN_REVIEW_COUNT},
        "categoryIds": list(leaf_ids),
        "variationsMerge": 1,
        "page": {"pageNumber": page_number, "pageSize": PAGE_SIZE,
                 "orders": [{"field": "revenue", "direction": "DESC"}]},
    }


def batches(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def to_candidate(rec: dict) -> dict:
    """品级记录 → 候选契约（含下游 sourcing 所需 query/imageUrl/ozonId）。"""
    return {
        "ozonId": str(rec["sku"]),
        "query": rec.get("title") or "",
        "title": rec.get("title"),
        "imageUrl": rec.get("imageUrl"),
        "productUrl": rec.get("productUrl"),
        "price": rec.get("price"),
        "weight": rec.get("weight"),
        "sales": rec.get("sales"),
        "revenue": rec.get("revenue"),
        "reviewRating": rec.get("reviewRating"),
        "sellerType": rec.get("sellerType"),
        "categoryPath": (rec.get("categoryInfo") or {}).get("cnTitlePath") or "",
    }


def filter_records(recs: list[dict]) -> list[dict]:
    """check_record 兜底 + run 内 sku 去重。"""
    seen: set[str] = set()
    out: list[dict] = []
    for r in recs:
        if not check_record(r).ok:
            continue
        sku = str(r["sku"])
        if sku in seen:
            continue
        seen.add(sku)
        out.append(to_candidate(r))
    return out


def _existing_ozon_ids() -> set[str]:
    with P.conn() as c:
        P._migrate(c)
        return {row["ozon_id"] for row in
                c.execute("SELECT ozon_id FROM products WHERE ozon_id IS NOT NULL")}


def run(leaf_ids: list[str], run_id: str | None = None, max_calls: int = 10) -> dict:
    """对叶子切片分批拉池，过滤去重后落 Run 目录。max_calls=Run 调用硬上限。"""
    run_id = run_id or "run-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = ROOT / "data/runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    candidates: list[dict] = []
    calls = 0
    for batch in batches(list(leaf_ids), BATCH):
        if calls >= max_calls:
            break
        data = SF.product_report(build_body(batch), run_id=run_id)
        calls += 1
        candidates.extend(filter_records(data.get("records") or []))

    existing = _existing_ozon_ids()
    fresh = [c for c in candidates if c["ozonId"] not in existing]
    fresh.sort(key=lambda c: c.get("revenue") or 0, reverse=True)

    (run_dir / "candidates.json").write_text(json.dumps(
        {"run_id": run_id, "source": "seerfar productReport/search/ozon",
         "fetched_at": datetime.now().isoformat(timespec="seconds"),
         "candidates": fresh}, ensure_ascii=False, indent=1), encoding="utf-8")
    tasks = [{"query": c["query"], "imageUrl": c["imageUrl"], "ozonId": c["ozonId"]}
             for c in fresh]
    (run_dir / "image-search-tasks.json").write_text(
        json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = [f"# 选品短名单 {run_id}", "",
             f"候选 {len(fresh)} 条（拉取 {calls} 次调用，去重后）", "",
             "| ozonId | 重量g | 价格₽ | 月销量 | 评分 | 类目 | 标题 |",
             "|---|---|---|---|---|---|---|"]
    for c in fresh:
        lines.append(f"| {c['ozonId']} | {c['weight']} | {c['price']} | {c['sales']} "
                     f"| {c['reviewRating']} | {c['categoryPath']} | {(c['title'] or '')[:40]} |")
    (run_dir / "shortlist.md").write_text("\n".join(lines), encoding="utf-8")

    return {"run_id": run_id, "calls": calls, "kept": len(fresh),
            "run_dir": str(run_dir)}


def approve(run_id: str) -> dict:
    """人工确认短名单后：去重闸录入 products 并推进到 scored，生成正式图搜任务。

    复用 sync_to_db 去重语义：① 同 ozon_id 活跃行已存在→跳过；② 毙过不复活。
    品级无「来源关键词」，products.query 用 title（UNIQUE(query,ozon_id) 不冲突）。
    """
    run_dir = ROOT / "data/runs" / run_id
    cands = json.loads((run_dir / "candidates.json").read_text(encoding="utf-8"))["candidates"]
    inserted: list[dict] = []
    with P.conn() as c:
        P._migrate(c)
        for cand in cands:
            oz = cand["ozonId"]
            active = c.execute("SELECT 1 FROM products WHERE ozon_id=? AND state != 'dead'",
                               (oz,)).fetchone()
            if active:
                continue  # ① 活跃去重
            dead = c.execute("SELECT 1 FROM products WHERE ozon_id=?", (oz,)).fetchone()
            if dead:
                continue  # ② 毙过不复活
            metrics = {"selection": {k: cand.get(k) for k in
                                     ("price", "weight", "sales", "revenue",
                                      "reviewRating", "categoryPath", "productUrl")},
                       "run_id": run_id}
            cur = c.execute(
                "INSERT INTO products(query,ozon_id,state,metrics_json,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?)",
                (cand["query"], oz, "discovered",
                 json.dumps(metrics, ensure_ascii=False), P.now(), P.now()))
            pid = cur.lastrowid
            P._event(c, pid, None, "discovered", f"approve 入库 run={run_id}")
            c.execute("UPDATE products SET state='scored', updated_at=? WHERE id=?",
                      (P.now(), pid))
            P._event(c, pid, "discovered", "scored", "品级选品确认")
            inserted.append(cand)
    tasks = [{"query": c["query"], "imageUrl": c["imageUrl"], "ozonId": c["ozonId"]}
             for c in inserted]
    (run_dir / "sourcing-approved.json").write_text(
        json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"run_id": run_id, "inserted": len(inserted), "skipped": len(cands) - len(inserted)}


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    if args[0] == "approve":
        print(json.dumps(approve(args[1]), ensure_ascii=False, indent=1))
        sys.exit(0)
    wl = json.loads((ROOT / "data/selection-whitelist.json").read_text(encoding="utf-8"))
    ids = wl["leaf_ids"]
    n = int(args[0]) if args else 50
    print(json.dumps(run(ids[:n]), ensure_ascii=False, indent=1))
