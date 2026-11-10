#!/usr/bin/env python3
"""选品：从关键词库 pop 合格词 → 候选报告 + 1688 图搜任务清单。

词库模式（2026-11-10 重构）：不再每次实时调 Nexscope——词指标是慢变量，
由 keyword_library 建库/刷新；本脚本只消费库内未消费词，日常零积分。
库空时先跑: python3 keyword_library.py build

用法:
    python3 select_products.py [--n 5]
输出:
    data/candidates-YYYYMMDD.json  (通过筛选的候选词+爆款商品)
    data/image-search-tasks-YYYYMMDD.json  (1688 图搜输入: 爆款主图 URL)
"""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import keyword_library as lib

ROOT = Path(__file__).parent
LEAVES = json.load(open(ROOT / "data/categories-leaf.json", encoding="utf-8"))
# 叶类目名 → categoryId（content_agent 入参；候选词的类目用其来源种子近似，
# shortcut: 相关词未必同类目，首批人工抽查看类目路径是否离谱，离谱再按词查实类目）
LEAF_BY_NAME = {c["pathRu"].split("/")[-1]: c["categoryId"] for c in LEAVES}


def main() -> None:
    args = sys.argv[1:]
    n = int(args[args.index("--n") + 1]) if "--n" in args else 5
    rows = lib.consume(n)
    if not rows:
        sys.exit("词库无合格未消费词，先 python3 keyword_library.py build")

    stamp = date.today().isoformat()
    out = {
        "generated_at": stamp,
        "source": "keyword_library（Nexscope 指标本地资产化）",
        "candidates": [{
            "query": r["query"],
            "queryCn": r["query_cn"] or "",
            "searchVolume": r["search_volume"],
            "growth30d": r["growth30d"],
            "competingProducts": r["competing_products"],
            "avgPrice": r["avg_price"],
            "ca": None,
            "returnRate": r["return_rate"],
            "categoryId": LEAF_BY_NAME.get(r["category_seed"], ""),
            "topProducts": json.loads(r["top_products"] or "[]"),
        } for r in rows],
    }
    (ROOT / "data").mkdir(exist_ok=True)
    path = ROOT / f"data/candidates-{stamp}.json"
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    tasks = [{"query": c["query"], "imageUrl": p["imageUrl"], "ozonId": p["ozonId"]}
             for c in out["candidates"] for p in c["topProducts"] if p.get("imageUrl")]
    tpath = ROOT / f"data/image-search-tasks-{stamp}.json"
    json.dump(tasks, open(tpath, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"候选词 {len(out['candidates'])} 个 -> {path.name}")
    print(f"图搜任务 {len(tasks)} 条 -> {tpath.name}")
    for c in out["candidates"]:
        print(f"  {c['query']}（{c['queryCn']}）月搜{c['searchVolume']}"
              f" 竞争{c['competingProducts']} 均价{c['avgPrice']}₽")


if __name__ == "__main__":
    main()
