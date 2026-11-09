#!/usr/bin/env python3
"""找货 agent：把 Ozon 爆款主图批量喂给 1688 图搜 skill，输出货源报告。

定位：流水线 Layer 3.5（趋势核验过后、单位经济之前）的确定性 worker。
skill 本体在 vendor/1688-product-find（官方 Ocean Gateway，AK 走 OAuth 本地 keyring）。

用法:
    python3 sourcing_agent.py                                # 跑全部待办任务
    python3 sourcing_agent.py --image /path/or/url.jpg       # 单图测试
    python3 sourcing_agent.py --limit 5                      # 只跑前 N 个候选词
输出:
    data/sourcing-YYYYMMDD.json
"""
import json
import subprocess
import sys
import tempfile
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
SKILL = ROOT / "vendor/1688-product-find/cli.py"
DATA = ROOT / "data"

# 供应商筛选（序数判断，阈值随样本校准）
MIN_SALES = 30          # 1688 sold_count 下限
MIN_SIMILARITY = 0.80   # 图搜相似度下限；实测好货源≥0.90、错位货源≤0.73（2026-11-08 样本），500 样本后重校准
TOP_KEEP = 5            # 每张图保留的货源数


def run_skill(args: list[str], timeout: int = 60) -> dict:
    proc = subprocess.run(
        ["python3", str(SKILL), *args],
        capture_output=True, text=True, timeout=timeout, cwd=SKILL.parent)
    # skill 约定输出 JSON：{"success": bool, "markdown": str, "data": {...}}
    # 输出可能是多行 pretty-print，且前面可能带日志行——从第一个 '{' 起 raw_decode
    start = proc.stdout.find("{")
    if start >= 0:
        try:
            obj, _ = json.JSONDecoder().raw_decode(proc.stdout[start:])
            return obj
        except json.JSONDecodeError:
            pass
    return {"success": False,
            "markdown": f"无法解析 skill 输出: {proc.stdout[-300:]} {proc.stderr[-300:]}"}


def localize(image: str, tmp: Path) -> str:
    """URL 图片先下载到本地（1688 网关对境外 URL 的抓取能力未知，本地化最稳）"""
    if not image.startswith(("http://", "https://")):
        return image
    dest = tmp / f"img_{abs(hash(image))}.jpg"
    req = urllib.request.Request(image, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
        f.write(r.read())
    return str(dest)


def pick_suppliers(products: list[dict]) -> list[dict]:
    def num(v, default=0.0):
        try:
            return float(str(v).replace(",", "").rstrip("%+")) or default
        except (TypeError, ValueError):
            return default
    good = [p for p in products
            if num(p.get("sold_count") or p.get("sales")) >= MIN_SALES
            and num(p.get("similarity_score")) >= MIN_SIMILARITY]
    good.sort(key=lambda p: num(p.get("sold_count") or p.get("sales")), reverse=True)
    return [{
        "title": p.get("title"),
        "price": p.get("price"),
        "soldCount": p.get("sold_count"),
        "yxIndex": p.get("yx_index"),
        "similarity": p.get("similarity_score"),
        "supplier": p.get("supplier"),
        "quantityBegin": p.get("quantity_begin"),
        "url": p.get("detail_url"),
        "imageUrl": p.get("image_url"),
    } for p in good[:TOP_KEEP]]


def search_one(image: str, tmp: Path) -> dict:
    local = localize(image, tmp)
    result = run_skill(["image_search", "-i", local, "-l", "10", "-s", "sold_desc"])
    if not result.get("success"):
        return {"ok": False, "error": result.get("markdown", "unknown")}
    data = result.get("data") or {}
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]  # skill 实际结构是 data.data.similar_products
    products = (data.get("similar_products") or data.get("products")
                or (data if isinstance(data, list) else []))
    return {"ok": True, "suppliers": pick_suppliers(products),
            "raw_count": len(products)}


def main() -> None:
    args = sys.argv[1:]
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        if "--image" in args:  # 单图测试模式
            img = args[args.index("--image") + 1]
            print(json.dumps(search_one(img, tmp), ensure_ascii=False, indent=2))
            return

        limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None
        tasks_file = sorted(DATA.glob("image-search-tasks-*.json"))[-1]
        tasks = json.load(open(tasks_file, encoding="utf-8"))
        # 按候选词去重：一个词只测它的第一张爆款图（省钱，先验证链路）
        by_query: dict[str, dict] = {}
        for t in tasks:
            by_query.setdefault(t["query"], t)
        items = list(by_query.values())[:limit]

        report = []
        for i, t in enumerate(items, 1):
            print(f"[{i}/{len(items)}] {t['query']} ...", file=sys.stderr)
            r = search_one(t["imageUrl"], tmp)
            report.append({"query": t["query"], "ozonId": t.get("ozonId"),
                           "ozonImage": t["imageUrl"], **r})
        out = DATA / f"sourcing-{date.today().isoformat()}.json"
        json.dump(report, open(out, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        ok = sum(1 for r in report if r.get("ok") and r["suppliers"])
        print(f"完成：{ok}/{len(report)} 个候选词找到合格货源 -> {out}")


if __name__ == "__main__":
    main()
