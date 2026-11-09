#!/usr/bin/env python3
"""PIL 大标题贴字：文字双轨制的本地轨（qwen-image-3.0 俄语大字有字母级错误，见 pipeline.html §3.5）。

AI 只画场景+图标，主图大标题由本模块叠加（Manrope Bold，西里尔零错误）。
用法:
    python3 text_overlay.py --image in.png --title "Дозатор для мыла" --out out.png
    python3 text_overlay.py --selftest      # 渲染西里尔测试卡自检
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent
FONT = ROOT / "fonts/Manrope.ttf"          # 变量字体，取 Bold 实例
FALLBACK = ROOT / "fonts/Inter.ttf"
AREA_RATIO = 0.22        # 顶部标题区高度占比（与 slot1 v2 prompt 的留白约定一致）
MARGIN_RATIO = 0.08      # Ozon 四边留白（平台角标）
COLOR = (38, 38, 38)     # dark charcoal，与 v1 prompt 约定一致
MAX_LINES = 2


def _font(size: int) -> ImageFont.FreeTypeFont:
    path = FONT if FONT.exists() else FALLBACK
    f = ImageFont.truetype(str(path), size)
    try:
        f.set_variation_by_name("Bold")
    except (ValueError, OSError):  # 非变量字体或无 Bold 命名实例
        pass
    return f


def _wrap(draw, words, font, max_w):
    """贪心折行；超过 MAX_LINES 返回 None（交给更小字号重试）。"""
    lines, cur = [], ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if draw.textlength(cand, font=font) <= max_w:
            cur = cand
        else:
            lines.append(cur)
            cur = w
            if len(lines) == MAX_LINES:      # 已经溢出
                return None
    if cur:
        lines.append(cur)
    return lines if len(lines) <= MAX_LINES else None


def overlay_title(src: str | Path, title: str, out: str | Path,
                  color: tuple = COLOR) -> dict:
    """在图片顶部 22% 区域居中叠加俄语大标题（≤2 行，字号自适应收缩）。"""
    img = Image.open(src).convert("RGB")
    w, h = img.size
    draw = ImageDraw.Draw(img)
    max_w = w * (1 - 2 * MARGIN_RATIO)
    area_h = h * AREA_RATIO

    placed = None
    for size in range(int(h * 0.11), 28, -2):   # 从大字往下缩
        font = _font(size)
        lines = _wrap(draw, title.split(), font, max_w)
        if not lines:
            continue
        asc, desc = font.getmetrics()
        block_h = len(lines) * (asc + desc)
        if block_h <= area_h * 0.9:
            placed = (font, lines, asc + desc, block_h)
            break
    if placed is None:
        raise ValueError(f"标题无法排入顶部区域（>{MAX_LINES}行或过长）: {title!r}")

    font, lines, line_h, block_h = placed
    y = (area_h - block_h) / 2
    for line in lines:
        draw.text((w / 2, y), line, font=font, fill=color, anchor="ma")
        y += line_h

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=95)
    return {"out": str(out), "size": f"{w}x{h}", "lines": lines,
            "font_size": font.size, "font": font.path or str(FONT)}


def _selftest() -> None:
    img = Image.new("RGB", (928, 1232), (210, 195, 180))  # 模拟场景底
    src = Path("/tmp/text_overlay_selftest_src.png")
    img.save(src)
    rec = overlay_title(src, "Дозатор для жидкого мыла",  # 含 й/ж/ю 的西里尔
                        "/tmp/text_overlay_selftest.png")
    assert rec["size"] == "928x1232", rec
    assert len(rec["lines"]) <= MAX_LINES, rec
    # 标题区确实被写入像素
    before = Image.open(src).crop((0, 0, 928, int(1232 * AREA_RATIO)))
    after = Image.open(rec["out"]).crop((0, 0, 928, int(1232 * AREA_RATIO)))
    assert list(before.getdata()) != list(after.getdata())
    print("selftest ok:", rec)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        sys.exit(0)
    args = sys.argv[1:]
    rec = overlay_title(args[args.index("--image") + 1],
                        args[args.index("--title") + 1],
                        args[args.index("--out") + 1])
    import json
    print(json.dumps(rec, ensure_ascii=False, indent=2))
