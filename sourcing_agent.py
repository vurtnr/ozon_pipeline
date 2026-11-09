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

sys.path.insert(0, str(Path(__file__).parent))
from config import BAILIAN_API_KEY, BAILIAN_BASE_URL

ROOT = Path(__file__).parent
SKILL = ROOT / "vendor/1688-product-find/cli.py"
DATA = ROOT / "data"

# 供应商筛选（序数判断，阈值随样本校准）
MIN_SALES = 30          # 1688 sold_count 下限
SIM_AUTO = 0.90         # 混合图搜 sim≥0.90 直接过
SIM_REVIEW = 0.80       # 0.80–0.90 进人工确认；<0.80 降级文字搜
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


def pick_suppliers(products: list[dict], min_sim: float = SIM_REVIEW) -> list[dict]:
    def num(v, default=0.0):
        try:
            return float(str(v).replace(",", "").rstrip("%+")) or default
        except (TypeError, ValueError):
            return default
    good = [p for p in products
            if num(p.get("sold_count") or p.get("sales")) >= MIN_SALES
            and num(p.get("similarity_score")) >= min_sim]
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


def search_text(query_cn: str) -> dict:
    """文字搜：中文关键词直接找货（图搜降级路径）"""
    result = run_skill(["text_search", "-q", query_cn, "-l", "10"])
    if not result.get("success"):
        return {"ok": False, "error": result.get("markdown", "unknown")}
    data = result.get("data") or {}
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    products = data.get("similar_products") or data.get("products") or []
    # 文字搜的 similarity_score 是文本匹配分，可能 <0.80，不能套图搜阈值
    # 改用宽松阈值：销量>=30 即保留，sim 仅作展示
    kept = [{
        "title": p.get("title"),
        "price": p.get("price"),
        "soldCount": p.get("sold_count"),
        "yxIndex": p.get("yx_index"),
        "similarity": p.get("similarity_score"),
        "supplier": p.get("supplier"),
        "quantityBegin": p.get("quantity_begin"),
        "url": p.get("detail_url"),
        "imageUrl": p.get("image_url"),
    } for p in products if float(p.get("sold_count") or 0) >= MIN_SALES]
    kept.sort(key=lambda p: float(p.get("soldCount") or 0), reverse=True)
    return {"ok": True, "mode": "text", "suppliers": kept[:TOP_KEEP],
            "raw_count": len(products)}


def vl_describe(image: str, tmp: Path) -> dict:
    """qwen3-vl-plus 识别 Ozon 图 → 中文商品描述 + 采购关键词"""
    import base64
    local = localize(image, tmp)
    b64 = base64.b64encode(Path(local).read_bytes()).decode()
    body = json.dumps({
        "model": "qwen3-vl-plus",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": "识别图片中的商品。输出：1) 商品中文名称 2) 核心功能（3个以内）3) 使用场景 4) 1688采购关键词（2-6字，精准描述商品本体+功能）"}
            ]
        }]
    }).encode()
    req = urllib.request.Request(
        BAILIAN_BASE_URL + "/chat/completions", data=body, method="POST",
        headers={"Authorization": f"Bearer {BAILIAN_API_KEY}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            r = json.load(resp)
        text = r['choices'][0]['message']['content']
        # 提取采购关键词：找"采购关键词"行，取同行或下一行的第一个词
        kw = ""
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if "采购关键词" in line or "关键词" in line:
                # 先取同行
                kw = line.split("：")[-1].split(":")[-1].split("、")[0].split(",")[0].strip()
                if not kw and i + 1 < len(lines):  # 同行空则取下一行
                    kw = lines[i + 1].split("、")[0].split(",")[0].strip()
                kw = kw.strip("* ")  # 去 markdown 加粗
                break
        return {"ok": True, "description": text, "keyword": kw}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def search_with_fallback(image: str, tmp: Path, query_cn: str | None) -> dict:
    """图搜优先，sim 不过闸自动降级文字搜"""
    r = search_one(image, tmp)
    if r.get("ok") and r.get("suppliers"):
        return r  # 图搜直接命中
    if not query_cn:
        r["error"] = "图搜无合格货源且缺 queryCn，无法文字搜降级"
        return r
    print(f"  图搜 sim 不过闸，降级文字搜: {query_cn}", file=sys.stderr)
    tr = search_text(query_cn)
    if tr.get("ok"):
        tr["fallback_from"] = "image"  # 标记降级来源
        return tr
    return r  # 文字搜也失败，返回图搜原始结果


def search_vl(image: str, tmp: Path) -> dict:
    """qwen-vl 识别 → 图搜+文字描述混合（默认模式）
    
    三级阈值：
      sim≥0.90 直接过（auto）
      0.80–0.90 进人工确认（review）
      <0.80 降级文字搜（text）
    """
    print(f"  qwen-vl 识别中...", file=sys.stderr)
    vl = vl_describe(image, tmp)
    if not vl.get("ok") or not vl.get("keyword"):
        return {"ok": False, "error": f"vl识别失败: {vl.get('error', '无关键词')}"}
    kw = vl["keyword"].strip("* ")
    print(f"  识别关键词: {kw}", file=sys.stderr)

    import base64
    local = localize(image, tmp)
    b64 = base64.b64encode(Path(local).read_bytes()).decode()
    sys.path.insert(0, str(SKILL.parent / "scripts"))
    from _http import search_products
    try:
        products = search_products({
            "imgBase64": b64, "imageUrl": None,
            "pageSize": 10, "purchaseAmount": 1,
            "query": kw,  # vl 关键词注入图搜
        })
    except Exception as e:
        return {"ok": False, "error": f"混合图搜失败: {e}"}
    
    kept = pick_suppliers(products, min_sim=SIM_REVIEW)
    top_sim = kept[0]["similarity"] if kept else 0
    
    if top_sim >= SIM_AUTO:
        status = "auto"
    elif top_sim >= SIM_REVIEW:
        status = "review"
    else:
        # 降级文字搜
        print(f"  混合图搜 sim={top_sim:.3f}<0.80，降级文字搜: {kw}", file=sys.stderr)
        tr = search_text(kw)
        if tr.get("ok"):
            tr["fallback_from"] = "vl+image"
            tr["vl_keyword"] = kw
            tr["vl_description"] = vl["description"]
            return tr
        status = "no_source"
    
    return {"ok": True, "mode": "vl+image", "status": status,
            "suppliers": kept, "raw_count": len(products),
            "vl_keyword": kw, "vl_description": vl["description"]}


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
        text_qs = args[args.index("--text") + 1].split(",") if "--text" in args else None
        auto_fallback = "--auto" in args  # 图搜不过闸自动降级文字搜
        use_vl = "--vl" in args  # qwen-vl 识别→图搜混合（默认推荐）
        use_raw = "--raw" in args  # 纯图搜（旧模式，测试用）

        report = []
        for i, t in enumerate(items, 1):
            print(f"[{i}/{len(items)}] {t['query']} ...", file=sys.stderr)
            if text_qs:  # 强制文字搜模式
                r = search_text(t["queryCn"])
            elif use_raw:
                r = search_one(t["imageUrl"], tmp)
            elif auto_fallback:
                r = search_with_fallback(t["imageUrl"], tmp, t.get("queryCn"))
            else:
                r = search_vl(t["imageUrl"], tmp)  # 默认：混合模式
            report.append({"query": t["query"], "ozonId": t.get("ozonId"),
                           "ozonImage": t.get("imageUrl", ""), **r})
        out = DATA / f"sourcing-{date.today().isoformat()}.json"
        json.dump(report, open(out, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        ok = sum(1 for r in report if r.get("ok") and r["suppliers"])
        print(f"完成：{ok}/{len(report)} 个候选词找到合格货源 -> {out}")


if __name__ == "__main__":
    main()
