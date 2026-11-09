#!/usr/bin/env python3
"""AI 生图 worker：1688 货源主图 → qwen-image-3.0 I2I → Ozon 套图（8 slot）。

消费 content_agent 的 spec JSON 逐 slot 出图；文字双轨制——大标题/徽章/说明带
由 text_overlay 本地贴字（AI 只画场景+小字图标）。
slot7 尺寸图条件生成：尺寸数字必须能在 1688 标题等来源找到（数字溯源），
拿不到真实尺寸数据就跳过（[user-decided] 2026-11-09）。

用法:
    python3 image_worker.py --spec data/specs/soap.json --image <1688图url> \
        --out-dir data/gen/soap            # 按 spec 出整套
    python3 image_worker.py --image <url或路径> --out /tmp/x.png   # 单张白底测试
输出:
    <out-dir>/slot{1..8}.png  +  slots-report.json（每 slot 用量/成本/跳过原因）
"""
import base64
import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import BAILIAN_API_KEY, BAILIAN_BASE_URL
from text_overlay import overlay_badge, overlay_caption, overlay_title

ROOT = Path(__file__).parent
GEN = ROOT / "data/gen"
SIZE = "928x1232"  # 3:4，Ozon 官方推荐比例
PRICE_PER_IMAGE = 0.033  # USD（$0.03 + 参考图 $0.003）

SLOT_TEMPLATES = {  # slot2 白底无条件必出；slot7 条件生成，不在此表
    "slot1": "slot1_main_infographic_v2",
    "slot2": "slot2_white_bg_v1",
    "slot3": "slot3_selling_point_v2",
    "slot4": "slot4_usage_v1",
    "slot5": "slot5_scene_v1",
    "slot6": "slot6_detail_v2",
    "slot8": "slot8_scenarios_v1",
}


def call_api(prompt: str, source_image: str, out_path: Path) -> dict:
    payload: dict = {"model": "qwen-image-3.0", "prompt": prompt, "size": SIZE}
    if source_image.startswith(("http://", "https://")):
        payload["image"] = source_image
    else:
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
    return {"usage": result.get("usage")}


def render(template: str, mapping: dict) -> str:
    body = "\n".join(l for l in template.splitlines()
                     if not l.startswith("#")).strip()
    for k, v in mapping.items():
        body = body.replace("{{" + k + "}}", v)
    leftover = re.findall(r"\{\{(\w+)\}\}", body)
    if leftover:
        raise ValueError(f"模板占位符未填充: {leftover}")
    return body


def _slot_spec(spec: dict, name: str) -> dict | None:
    return next((s for s in spec.get("slots", []) if s.get("slot") == name), None)


def _measurements_traceable(measurements: str, sources: list[str]) -> bool:
    """slot7 闸：每个数字都必须有出处（与 content_agent 数字溯源同规则）。"""
    src = " ".join(sources)
    nums = re.findall(r"\d+(?:[.,]\d+)?", measurements)
    return bool(nums) and all(n in src for n in nums)


def generate_slots(spec_path: str, source_image: str, out_dir: Path) -> list[dict]:
    spec = json.load(open(spec_path, encoding="utf-8"))
    meta = spec["_meta"]
    sources = [meta.get("title1688", ""), spec.get("category_path_ru", ""),
               meta.get("query", "")]
    records = []

    def run(slot: str, template: str, mapping: dict, overlays: list) -> None:
        prompt = render((ROOT / f"prompts/{template}.txt").read_text("utf-8"),
                        mapping)
        raw = out_dir / f"{slot}_raw.png"
        final = out_dir / f"{slot}.png"
        try:
            rec = call_api(prompt, source_image, raw)
            applied = []
            if overlays:
                src = raw
                for fn, text in overlays:
                    fn(src, text, final)
                    applied.append(text)
                    src = final
            else:
                raw.rename(final)
            records.append({"slot": slot, "ok": True, "template": template,
                            "overlays": applied, "out": str(final),
                            "cost_usd": PRICE_PER_IMAGE, **rec})
            print(f"[ok] {slot}", file=sys.stderr)
        except Exception as exc:
            records.append({"slot": slot, "ok": False, "template": template,
                            "error": str(exc)[:200]})
            print(f"[fail] {slot}: {exc}", file=sys.stderr)

    # slot2 白底：无条件
    run("slot2", SLOT_TEMPLATES["slot2"], {}, [])
    # 其余 slot 按 spec 编排
    for name in ("slot1", "slot3", "slot4", "slot5", "slot6", "slot8"):
        s = _slot_spec(spec, name)
        if not s:
            records.append({"slot": name, "ok": False,
                            "error": "spec 未编排该 slot"})
            continue
        if name == "slot1":
            run(name, SLOT_TEMPLATES[name],
                {"SCENE": s["scene"], "COPY_ICONS": "; ".join(s["copy_icons"])},
                [(overlay_title, s["copy_title"])])
        elif name == "slot3":
            run(name, SLOT_TEMPLATES[name], {},
                [(overlay_title, s["copy_headline"]),
                 (overlay_badge, s["copy_badge"])])
        elif name == "slot4":
            run(name, SLOT_TEMPLATES[name],
                {"ACTION": s["action_en"], "COPY_LINE": s.get("copy_line", "")}, [])
        elif name == "slot5":
            run(name, SLOT_TEMPLATES[name],
                {"SCENE": s["scene"], "COPY_LINE": s.get("copy_line", "")}, [])
        elif name == "slot6":
            run(name, SLOT_TEMPLATES[name],
                {"DETAIL": s["detail_en"]},
                [(overlay_caption, s["copy_word"])] if s.get("copy_word") else [])
        elif name == "slot8":
            scen = s["scenarios"]
            run(name, SLOT_TEMPLATES[name],
                {"SCENARIO_ICONS": ", ".join(scen)},
                [(overlay_caption, "Подходит для: " + " / ".join(scen))])
    # slot7 尺寸图：数字溯源闸——拿不到真实尺寸就跳过
    s7 = _slot_spec(spec, "slot7")
    if s7 and _measurements_traceable(s7.get("measurements", ""), sources):
        # shortcut: 暂用 v1（AI 渲染标注数字，图审必须重点核对），
        # 首个有真实尺寸的品出现时升级 v2 双轨（PIL 贴标注）
        run("slot7", "slot7_size_v1", {"MEASUREMENTS": s7["measurements"]}, [])
    else:
        reason = ("spec 无 slot7" if not s7 else
                  f"尺寸数字无出处，跳过: {s7.get('measurements', '')!r}")
        records.append({"slot": "slot7", "ok": False, "skipped": True,
                        "error": reason})
        print(f"[skip] slot7: {reason}", file=sys.stderr)

    report = out_dir / "slots-report.json"
    json.dump({"spec": spec_path, "source_image": source_image[:120],
               "generated_at": datetime.now().isoformat(timespec="seconds"),
               "slots": records},
              open(report, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    ok = sum(1 for r in records if r.get("ok"))
    cost = sum(r.get("cost_usd", 0) for r in records if r.get("ok"))
    print(f"套图 {ok}/8 张，成本 ${cost:.2f} -> {out_dir}")


def main() -> None:
    args = sys.argv[1:]
    if "--spec" in args:
        get = lambda k: args[args.index(k) + 1]
        generate_slots(get("--spec"), get("--image"),
                       Path(get("--out-dir")) if "--out-dir" in args
                       else GEN / "unnamed")
        return
    if "--image" in args:  # 单张白底测试
        img = args[args.index("--image") + 1]
        out = Path(args[args.index("--out") + 1]) if "--out" in args \
            else GEN / "test/main.png"
        prompt = (ROOT / "prompts/slot2_white_bg_v1.txt").read_text("utf-8")
        rec = call_api(render(prompt, {}), img, out)
        print(json.dumps({"out": str(out), **rec}, ensure_ascii=False, indent=2))
        return
    sys.exit(__doc__)


if __name__ == "__main__":
    main()
