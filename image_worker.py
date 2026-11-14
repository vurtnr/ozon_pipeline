#!/usr/bin/env python3
"""AI 生图 worker：1688 货源主图 → qwen-image-3.0 I2I → Ozon 套图（8 slot）。

消费 content_agent 的 spec JSON 逐 slot 出图；文字双轨制——大标题/徽章/说明带
由 text_overlay 本地贴字（AI 只画场景+小字图标）。
slot7 尺寸图条件生成：尺寸数字必须能在 1688 标题等来源找到（数字溯源），
拿不到真实尺寸数据就跳过（[user-decided] 2026-11-09）。

用法:
    python3 image_worker.py --spec data/specs/soap.json --image <1688图url> \
        --out-dir data/gen/soap            # 按 spec 出整套
    python3 image_worker.py --spec ... --image ... --out-dir ... --only slot7,slot1
                                             # 只重生指定 slot（不打乱其他已审图）
    python3 image_worker.py --image <url或路径> --out /tmp/x.png   # 单张白底测试
输出:
    <out-dir>/slot{1..8}.png  +  slots-report.json（每 slot 用量/成本/跳过原因）
"""
import base64
import io
import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from config import BAILIAN_API_KEY, BAILIAN_BASE_URL
from text_overlay import (overlay_caption, overlay_chips, overlay_chips_bottom,
                          overlay_icon_labels, overlay_title)

ROOT = Path(__file__).parent
GEN = ROOT / "data/gen"
SIZE = "928x1232"  # 3:4，Ozon 官方推荐比例
PRICE_PER_IMAGE = 0.033  # USD（$0.03 + 参考图 $0.003）

SLOT_TEMPLATES = {  # slot2 白底改用供应商原图（见下）；slot7 条件生成，不在此表
    "slot1": "slot1_cover_hook_v3",
    "slot3": "slot3_arguments_v3",
    "slot4": "slot4_usage_v3",
    "slot5": "slot5_scene_v3",
    "slot6": "slot6_detail_v3",
    "slot8": "slot8_scenarios_v3",
}

# 风格参考图机制已停用（2026-11-10）：模版图自带文字/拼贴版式，I2I 会连文字
# 一起迁移（slot4 连续 4 次失败的根因）；v3 提示词已自带精确版式，不再需要模版。
# 机制代码保留，找到无文字的干净模版后可逐 slot 恢复。
STYLE_REFS: dict = {}

# 官方 negative_prompt 参数：放反复出现的失败模式（docs/research/ozon-image-set-hot-logic.md §3）
NEG_BASE = ("text, letters, words, numbers, watermark, logo, deformed product, "
            "extra objects, cardboard box, packaging, book, magazine, newspaper, signage")
NEG_SLOT7 = "watermark, logo, deformed product, garbled text, extra objects"

# 设计师人格（[user-provided] 2026-11-10 系统提示词）：前置到每个 slot prompt
DESIGNER_PREAMBLE = (
    "You are a senior product photographer for Russian e-commerce, and you "
    "deeply understand Russian marketplace buyers' habits. You work in 3:4 "
    "vertical format and communicate only through photorealistic photography, "
    "composition and empty space — every word is added later by a separate "
    "typesetting tool, so you never render any text, letters, numbers, brand "
    "names, logos or trademarks. You never alter the product itself: its shape, "
    "color, structure, materials and quantity stay exactly as in the reference "
    "photo.\n")

# 确定性闸门：VL 检测到 AI 偷画文字时重试一次（加强措辞），仍带字则判失败
STRICT_NO_TEXT = (
    "\nSTRICT REQUIREMENT: the output image must contain ZERO text — no letters, "
    "no words, no numbers, no characters of any alphabet, no typographic "
    "decorations, no signage. Pure photography and empty background only.")


def _as_image(src: str) -> str:
    if src.startswith(("http://", "https://")):
        return src
    p = Path(src)
    mime = "image/webp" if p.suffix == ".webp" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


def call_api(prompt: str, source_image: str, out_path: Path,
             style_ref: str | None = None,
             negative_prompt: str = NEG_BASE) -> dict:
    payload: dict = {"model": "qwen-image-3.0", "prompt": prompt, "size": SIZE,
                     "negative_prompt": negative_prompt,
                     # 精确构图（留白区/网格）不能被智能改写填坑，官方建议关
                     "prompt_extend": False}
    if style_ref:
        payload["image"] = [_as_image(source_image),
                            _as_image(str(ROOT / "prompts/style_ref" / style_ref))]
    else:
        payload["image"] = _as_image(source_image)
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


def _vl_has_text(image: str, question: str = "图中是否含有任何文字、字母或数字标识"
                 "（包括包装/标签/水印上的）？只回答：有 或 无") -> bool:
    """VL 检测图中是否带文字/标识（供应商营销图常带中文，直通 slot2 会流向 Ozon）。
    异常时按有文字处理（回退 AI 生成，保守方向）。"""
    try:
        if image.startswith(("http://", "https://")):
            req = urllib.request.Request(image,
                                         headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=60).read()
        else:
            raw = Path(image).read_bytes()
        b64 = base64.b64encode(raw).decode()
        body = json.dumps({
            "model": "qwen3-vl-plus",
            "messages": [{"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": question},
            ]}],
        }).encode()
        req = urllib.request.Request(
            BAILIAN_BASE_URL + "/chat/completions", data=body, method="POST",
            headers={"Authorization": f"Bearer {BAILIAN_API_KEY}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = json.load(resp)["choices"][0]["message"]["content"].strip()
        return text.startswith("有")
    except Exception as exc:
        print(f"[warn] slot2 文字检测异常（按有文字回退 AI 生成）: {exc}",
              file=sys.stderr)
        return True


def _supplier_white_bg(source_image: str, out_path: Path) -> None:
    """slot2：供应商原图居中贴上 928x1232 白底画布（不 AI 生成，零形变零成本）。"""
    if source_image.startswith(("http://", "https://")):
        req = urllib.request.Request(source_image,
                                     headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=60).read()
    else:
        raw = Path(source_image).read_bytes()
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = map(int, SIZE.split("x"))
    canvas = Image.new("RGB", (w, h), "white")
    im.thumbnail((int(w * 0.9), int(h * 0.9)), Image.LANCZOS)
    canvas.paste(im, ((w - im.width) // 2, (h - im.height) // 2))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)


def render(template: str, mapping: dict) -> str:
    body = "\n".join(l for l in template.splitlines()
                     if not l.startswith("#")).strip()
    for k, v in mapping.items():
        body = body.replace("{{" + k + "}}", v)
    leftover = re.findall(r"\{\{(\w+)\}\}", body)
    if leftover:
        raise ValueError(f"模板占位符未填充: {leftover}")
    return DESIGNER_PREAMBLE + body


# 成品文字检测问法：场景内任何载体（含包装盒/纸张/背景道具）上的可读文字都算
# 违规（2026-11-10 slot4 道具盒乱码+OZON 侵权 logo 事故），仅产品本体丝印豁免
_GEN_TEXT_Q = ("图中任何地方——包括包装盒、纸张、标签、背景物体上——是否有"
               "可读的文字、字母、数字、品牌标识或水印（仅产品本体上的丝印/"
               "刻字/按钮标识不算）？只回答：有 或 无")


def _slot_spec(spec: dict, name: str) -> dict | None:
    return next((s for s in spec.get("slots", []) if s.get("slot") == name), None)


def _measurements_traceable(measurements: str, sources: list[str]) -> bool:
    """slot7 闸：每个数字都必须有出处（与 content_agent 数字溯源同规则）。
    逗号/句点小数点归一（俄语 17,2 vs 来源 17.2）。"""
    src = " ".join(sources).replace(",", ".")
    nums = [n.replace(",", ".")
            for n in re.findall(r"\d+(?:[.,]\d+)?", measurements)]
    return bool(nums) and all(n in src for n in nums)


def generate_slots(spec_path: str, source_image: str, out_dir: Path,
                   only: set | None = None) -> list[dict]:
    spec = json.load(open(spec_path, encoding="utf-8"))
    meta = spec["_meta"]
    sources = [meta.get("title1688", ""), spec.get("category_path_ru", ""),
               meta.get("query", "")]
    # slot7 溯源增强：供应商营销图 OCR 提取真实尺寸标注（商家自填级采信），
    # 拿不到则空串，闸行为不变
    from dimensions import extract_dimensions
    ocr = extract_dimensions(source_image)
    if ocr:
        sources.append(ocr)
        print(f"[slot7] OCR 尺寸出处: {ocr.splitlines()}", file=sys.stderr)
    records = []

    def run(slot: str, template: str, mapping: dict, overlays: list) -> None:
        prompt = render((ROOT / f"prompts/{template}.txt").read_text("utf-8"),
                        mapping)
        # 防文字继承：供应商图里的中文营销字/水印不许带进成品（Ozon 俄语市场）
        prompt += ("\nRemove any text, letters, numbers or logos visible in the "
                   "reference photo (packaging, labels, props, watermarks); never "
                   "carry them into the generated image. Only render text "
                   "explicitly requested in the instructions above.")
        style = STYLE_REFS.get(slot)
        if style:
            prompt += ("\nThe second reference image is a layout/style reference "
                       "only: match its composition, feature-card grid and "
                       "typography style; the product itself must come from the "
                       "first image, unchanged.")
        raw = out_dir / f"{slot}_raw.png"
        final = out_dir / f"{slot}.png"
        try:
            neg = NEG_SLOT7 if slot == "slot7" else NEG_BASE
            rec = call_api(prompt, source_image, raw, style_ref=style,
                           negative_prompt=neg)
            # 确定性闸门（2026-11-10 乱码事故）：slot7 以外的成品必须无叠加文字，
            # AI 偷画文字→加强措辞重试一次→仍带字判失败（不交付乱码图）
            if slot != "slot7" and _vl_has_text(str(raw), _GEN_TEXT_Q):
                print(f"[retry] {slot}: 检测到 AI 偷画文字，加强措辞重试",
                      file=sys.stderr)
                rec = call_api(prompt + STRICT_NO_TEXT, source_image, raw,
                               style_ref=style, negative_prompt=neg)
                if _vl_has_text(str(raw), _GEN_TEXT_Q):
                    raise RuntimeError("AI 两次渲染文字，本 slot 放弃"
                                       "（可人工 --only 重试）")
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

    # slot2 白底：优先供应商原图（零成本零形变）；原图带文字则回退 AI 生成
    # （[user-decided] 2026-11-10 白底不 AI 生成 + 同日补中文闸）
    if not only or "slot2" in only:
        if _vl_has_text(source_image):
            print("[slot2] 供应商原图含文字，回退 AI 白底生成", file=sys.stderr)
            run("slot2", "slot2_white_bg_v1", {}, [])
        else:
            try:
                final2 = out_dir / "slot2.png"
                _supplier_white_bg(source_image, final2)
                records.append({"slot": "slot2", "ok": True,
                                "template": "supplier_original", "out": str(final2),
                                "cost_usd": 0})
                print("[ok] slot2 (供应商原图)", file=sys.stderr)
            except Exception as exc:
                records.append({"slot": "slot2", "ok": False, "error": str(exc)[:200]})
                print(f"[fail] slot2: {exc}", file=sys.stderr)
    # 其余 slot 按 spec 编排
    for name in ("slot1", "slot3", "slot4", "slot5", "slot6", "slot8"):
        if only and name not in only:
            continue
        s = _slot_spec(spec, name)
        if not s:
            records.append({"slot": name, "ok": False,
                            "error": "spec 未编排该 slot"})
            continue
        if name == "slot1":
            # v4 兑现时刻：spec 带 cover_action_en 时启用（A/B 实验 B 版，v3 模板保留）
            tpl = SLOT_TEMPLATES[name]
            mapping = {"SCENE": s["scene"]}
            if s.get("cover_action_en"):
                tpl = "slot1_payoff_v4"
                mapping["ACTION"] = s["cover_action_en"]
            run(name, tpl, mapping,
                [(overlay_title, s["copy_title"]),
                 (overlay_chips, s["copy_icons"])])
        elif name == "slot3":
            run(name, SLOT_TEMPLATES[name],
                {"SCENE_HINT": s.get("scene_hint") or
                 "a clean light studio backdrop with a subtle warm gradient"},
                [(overlay_title, s["copy_headline"]),
                 (overlay_chips_bottom, s["args"])])
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
            run(name, SLOT_TEMPLATES[name],
                {"SCENARIO_SCENES": ", ".join(s["scenarios_en"])},
                [(overlay_icon_labels, s["scenarios"])])
    # slot7 尺寸图：数字溯源闸——信任级：manual（人工输入）> OCR（商家自填）
    s7 = _slot_spec(spec, "slot7")
    if only and "slot7" not in only:
        pass
    elif s7 and (s7.get("source") == "manual"
                 or _measurements_traceable(s7.get("measurements", ""), sources)):
        # shortcut: 暂用 v1（AI 渲染标注数字，图审必须重点核对），
        # 首个有真实尺寸的品出现时升级 v2 双轨（PIL 贴标注）
        run("slot7", "slot7_size_v3", {"MEASUREMENTS": s7["measurements"]}, [])
    else:
        reason = ("spec 无 slot7" if not s7 else
                  f"尺寸数字无出处，跳过: {s7.get('measurements', '')!r}")
        records.append({"slot": "slot7", "ok": False, "skipped": True,
                        "error": reason})
        print(f"[skip] slot7: {reason}", file=sys.stderr)

    if only:  # 局部重生不覆盖整卡报告
        ok = sum(1 for r in records if r.get("ok"))
        cost = sum(r.get("cost_usd", 0) for r in records if r.get("ok"))
        print(f"局部重生 {sorted(only)}: {ok} 张，成本 ${cost:.2f}")
        return records
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
        only = (set(get("--only").split(",")) if "--only" in args else None)
        generate_slots(get("--spec"), get("--image"),
                       Path(get("--out-dir")) if "--out-dir" in args
                       else GEN / "unnamed", only=only)
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
