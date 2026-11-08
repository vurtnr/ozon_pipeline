#!/usr/bin/env python3
"""AI 生图 worker：1688 货源原图 → qwen-image-3.0 图生图 → Ozon 合规主图。

用法:
    python3 image_worker.py                          # 处理 sourcing 报告里所有货源
    python3 image_worker.py --image <url或路径> --out /tmp/x.png   # 单张测试
输出:
    data/gen/<productId>/main.png          生成图（928×1232，3:4）
    data/gen/report-YYYYMMDD.json          每张的用量/成本/prompt版本记录
"""
import base64
import json
import sys
import tempfile
import urllib.request
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import BAILIAN_API_KEY, BAILIAN_BASE_URL

ROOT = Path(__file__).parent
GEN = ROOT / "data/gen"
SIZE = "928x1232"  # 3:4，Ozon 官方推荐比例
PROMPT_VERSION = "main_image_v1"
PRICE_PER_IMAGE = 0.03  # USD，qwen-image-3.0 标准版（输入图 +$0.003）
COST_PER_IMAGE = PRICE_PER_IMAGE + 0.003


def generate(source_image: str, out_path: Path) -> dict:
    """图生图：保持商品本体，换白底。返回用量记录。"""
    prompt = (ROOT / f"prompts/{PROMPT_VERSION}.txt").read_text(encoding="utf-8").strip()
    payload: dict = {"model": "qwen-image-3.0", "prompt": prompt, "size": SIZE}
    if source_image.startswith(("http://", "https://")):
        payload["image"] = source_image
    else:  # 本地文件转 data URI
        b64 = base64.b64encode(Path(source_image).read_bytes()).decode()
        payload["image"] = f"data:image/jpeg;base64,{b64}"

    req = urllib.request.Request(
        f"{BAILIAN_BASE_URL}/images/generations",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {BAILIAN_API_KEY}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        result = json.load(resp)

    url = result["data"][0]["url"]  # 临时 URL（~24h），必须立即下载
    out_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        out_path.write_bytes(r.read())

    return {"usage": result.get("usage"), "cost_usd": COST_PER_IMAGE,
            "prompt_version": PROMPT_VERSION,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "out": str(out_path)}


def main() -> None:
    args = sys.argv[1:]
    if "--image" in args:  # 单张测试
        img = args[args.index("--image") + 1]
        out = Path(args[args.index("--out") + 1]) if "--out" in args \
            else GEN / "test/main.png"
        rec = generate(img, out)
        print(json.dumps(rec, ensure_ascii=False, indent=2))
        return

    # 批量：sourcing 报告中每个候选的第一个货源
    report = sorted((ROOT / "data").glob("sourcing-*.json"))[-1]
    items = json.load(open(report, encoding="utf-8"))
    records = []
    for item in items:
        if not item.get("ok") or not item.get("suppliers"):
            continue
        supplier = item["suppliers"][0]
        pid = supplier["url"].split("/offer/")[-1].split(".")[0]
        try:
            rec = generate(supplier["imageUrl"], GEN / pid / "main.png")
            records.append({"query": item["query"], "productId": pid, "ok": True, **rec})
            print(f"[ok] {item['query']} -> {pid}", file=sys.stderr)
        except Exception as exc:
            records.append({"query": item["query"], "productId": pid,
                            "ok": False, "error": str(exc)[:200]})
            print(f"[fail] {item['query']}: {exc}", file=sys.stderr)

    out = GEN / f"report-{date.today().isoformat()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    json.dump(records, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    ok = sum(1 for r in records if r["ok"])
    cost = sum(r.get("cost_usd", 0) for r in records if r["ok"])
    print(f"生成 {ok}/{len(records)} 张，成本 ${cost:.2f} -> {out}")


if __name__ == "__main__":
    main()
