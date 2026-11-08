#!/usr/bin/env python3
"""Ozon 选品脚本：关键词 -> Nexscope 指标 -> 筛选漏斗 -> 1688 图搜任务清单。

用法:
    NEXSCOPE_API_KEY=nk-xxx python3 select.py seeds.txt
输出:
    data/candidates-YYYYMMDD.json  (通过筛选的候选词+爆款商品)
    data/image-search-tasks-YYYYMMDD.json  (1688 图搜输入: 爆款主图 URL)
"""
import json
import os
import sys
import time
import urllib.request
from datetime import date

API = "https://api.nexscope.ai/sr/ozon/marketKeywordSearch"
KEY = os.environ["NEXSCOPE_API_KEY"]

# 筛选漏斗阈值（按需调整）
MIN_SEARCH_VOLUME = 5000      # 月搜热度下限
MIN_GROWTH_RATE = -5.0        # 30 天增长率下限（允许微跌）
MAX_COMPETING = 3000          # 竞争商品数上限
PRICE_BAND = (300.0, 5000.0)  # 均价带（₽）：太低没利润，太高动销慢
MAX_RETURN_RATE = 20.0        # 退货取消率上限 %
TOP_PRODUCTS = 3              # 每个候选词保留的爆款数（喂 1688 图搜）


def query_keywords(keyword: str, page_size: int = 50) -> list[dict]:
    body = json.dumps({
        "keywords": [keyword],
        "page": {"page": 1, "pageSize": page_size},
    }).encode()
    req = urllib.request.Request(
        API, data=body, method="POST",
        headers={"Authorization": f"Bearer {KEY}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    if payload.get("code") != 0:
        raise RuntimeError(f"API error: {payload}")
    return payload["data"]["data"]


def passes(row: dict) -> bool:
    price = row.get("avgPrice")
    return (
        (row.get("searchVolume") or 0) >= MIN_SEARCH_VOLUME
        and (row.get("count30GrowthRate") or 0) >= MIN_GROWTH_RATE
        and (row.get("competingProducts") or 10**9) <= MAX_COMPETING
        and price is not None and PRICE_BAND[0] <= price <= PRICE_BAND[1]
        and (row.get("returnCancellationRate") or 0) <= MAX_RETURN_RATE
    )


def main() -> None:
    seeds = [line.strip() for line in open(sys.argv[1], encoding="utf-8")
             if line.strip() and not line.startswith("#")]
    seen, candidates = set(), []
    for seed in seeds:
        try:
            rows = query_keywords(seed)
        except Exception as exc:  # 网络/配额错误不中断整批
            print(f"[warn] {seed}: {exc}", file=sys.stderr)
            continue
        for row in rows:
            q = row.get("query")
            if not q or q in seen or not passes(row):
                continue
            seen.add(q)
            candidates.append(row)
        time.sleep(0.4)  # 免费档 1 req/s，付费档 3 req/s，保守限速
        print(f"[ok] {seed}: {len(rows)} 词，累计候选 {len(candidates)}", file=sys.stderr)

    stamp = date.today().isoformat()
    out = {
        "generated_at": stamp,
        "filters": vars().get("__filters__", "见脚本常量"),
        "candidates": [{
            "query": r["query"],
            "queryCn": r.get("queryCn"),
            "searchVolume": r.get("searchVolume"),
            "growth30d": r.get("count30GrowthRate"),
            "competingProducts": r.get("competingProducts"),
            "avgPrice": r.get("avgPrice"),
            "ca": r.get("ca"),
            "returnRate": r.get("returnCancellationRate"),
            "topProducts": [
                {"ozonId": p.get("ozonId"), "sku": p.get("sku"),
                 "title": p.get("title"), "imageUrl": p.get("imageUrl")}
                for p in (r.get("products") or [])[:TOP_PRODUCTS]
            ],
        } for r in sorted(candidates,
                          key=lambda r: r.get("searchVolume") or 0,
                          reverse=True)],
    }
    path = f"data/candidates-{stamp}.json"
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    tasks = [{"query": c["query"], "imageUrl": p["imageUrl"], "ozonId": p["ozonId"]}
             for c in out["candidates"] for p in c["topProducts"] if p.get("imageUrl")]
    tpath = f"data/image-search-tasks-{stamp}.json"
    json.dump(tasks, open(tpath, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"候选词 {len(out['candidates'])} 个 -> {path}")
    print(f"图搜任务 {len(tasks)} 条 -> {tpath}")


if __name__ == "__main__":
    main()
