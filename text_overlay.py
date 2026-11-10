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


def _fit_single(draw, text, max_w, start):
    """单行自适应缩字号，返回 font；放不下返回 None。"""
    for size in range(start, 20, -2):
        font = _font(size)
        if draw.textlength(text, font=font) <= max_w:
            return font
    return None


def overlay_badge(src: str | Path, text: str, out: str | Path,
                  color: tuple = (47, 111, 237)) -> dict:
    """slot3 数字徽章：底部 18% 区域内画圆角胶囊 + 白粗字。"""
    img = Image.open(src).convert("RGB")
    w, h = img.size
    draw = ImageDraw.Draw(img)
    font = _fit_single(draw, text, w * 0.7, int(h * 0.05))
    if font is None:
        raise ValueError(f"徽章文字过长: {text!r}")
    tw = draw.textlength(text, font=font)
    pad_x, pad_y = font.size * 0.9, font.size * 0.45
    bw, bh = tw + 2 * pad_x, font.size + 2 * pad_y
    x, y = (w - bw) / 2, h * 0.845 - bh / 2     # 底部 18% 预留区垂直居中
    draw.rounded_rectangle([x, y, x + bw, y + bh], radius=bh / 2, fill=color)
    draw.text((w / 2, y + bh / 2), text, font=font, fill=(255, 255, 255), anchor="mm")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=95)
    return {"out": str(out), "badge": text, "font_size": font.size}


def _uniform_fit(draw, texts, max_w, start):
    """多条文本统一字号：从 start 往下缩，直到全部放得进 max_w。"""
    for size in range(start, 14, -2):
        font = _font(size)
        if all(draw.textlength(t, font=font) <= max_w for t in texts):
            return font
    return None


CHIP_BG = (20, 20, 20, 155)    # 半透明深底芯片（Ozon 信息图常见样式）
CHIP_FG = (255, 255, 255)


def _draw_chip(draw, cx, cy, text, font):
    """以 (cx,cy) 为中心画半透明圆角芯片+白粗字，返回芯片高。"""
    tw = draw.textlength(text, font=font)
    pad_x, pad_y = font.size * 0.8, font.size * 0.42
    bw, bh = tw + 2 * pad_x, font.size + 2 * pad_y
    draw.rounded_rectangle([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2],
                           radius=bh / 2, fill=CHIP_BG)
    draw.text((cx, cy - font.size * 0.06), text, font=font,
              fill=CHIP_FG, anchor="mm")
    return bh


def _composite(img: Image.Image, draw_fn) -> Image.Image:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(layer))
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def overlay_chips(src: str | Path, texts: list, out: str | Path) -> dict:
    """slot1 利益点芯片列：左中带（与 slot1 v3 模板预留区对齐）竖排半透明芯片。"""
    img = Image.open(src).convert("RGB")
    w, h = img.size
    probe = ImageDraw.Draw(img)
    font = _uniform_fit(probe, texts, w * 0.28, int(h * 0.038))
    if font is None:
        raise ValueError(f"芯片文字放不进左中带: {texts!r}")
    pad_y, gap = font.size * 0.42, h * 0.022
    chip_h = font.size + 2 * pad_y
    y = h * 0.42

    def fn(d):
        nonlocal y
        for t in texts:
            _draw_chip(d, w * 0.25, y + chip_h / 2, t, font)
            y += chip_h + gap
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _composite(img, fn).save(out, quality=95)
    return {"out": str(out), "chips": texts, "font_size": font.size}


def overlay_chips_bottom(src: str | Path, texts: list, out: str | Path) -> dict:
    """slot3 论据芯片行：底部 30% 预留区内 n 枚芯片水平均分（与 slot3 v3 对齐）。"""
    img = Image.open(src).convert("RGB")
    w, h = img.size
    n = len(texts)
    probe = ImageDraw.Draw(img)
    font = _uniform_fit(probe, texts, w * 0.88 / n, int(h * 0.036))
    if font is None:
        raise ValueError(f"论据芯片放不进底部行: {texts!r}")

    def fn(d):
        for i, t in enumerate(texts):
            _draw_chip(d, w * (i + 0.5) / n, h * 0.85, t, font)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _composite(img, fn).save(out, quality=95)
    return {"out": str(out), "chips": texts, "font_size": font.size}


def overlay_icon_labels(src: str | Path, labels: list, out: str | Path,
                        color: tuple = COLOR) -> dict:
    """slot8 场景格标签：每列中心正下方贴俄语标签（与 slot8 v3 的 3 列网格对齐）。"""
    img = Image.open(src).convert("RGB")
    w, h = img.size
    n = len(labels)
    probe = ImageDraw.Draw(img)
    font = _uniform_fit(probe, labels, w * 0.88 / n, int(h * 0.04))
    if font is None:
        raise ValueError(f"场景标签放不下: {labels!r}")

    def fn(d):
        for i, t in enumerate(labels):
            d.text((w * (i + 0.5) / n, h * 0.80), t, font=font,
                   fill=color, anchor="mm")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _composite(img, fn).save(out, quality=95)
    return {"out": str(out), "labels": labels, "font_size": font.size}


def overlay_caption(src: str | Path, text: str, out: str | Path,
                    color: tuple = COLOR) -> dict:
    """slot8 底部固定格式说明带（如 Подходит для: A / B / C），单行居中。"""
    img = Image.open(src).convert("RGB")
    w, h = img.size
    draw = ImageDraw.Draw(img)
    font = _fit_single(draw, text, w * (1 - 2 * MARGIN_RATIO), int(h * 0.045))
    if font is None:
        raise ValueError(f"说明带过長: {text!r}")
    draw.text((w / 2, h * 0.91), text, font=font, fill=color, anchor="mm")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=95)
    return {"out": str(out), "caption": text, "font_size": font.size}


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
    # 徽章 + 说明带
    rec_b = overlay_badge(src, "300 мл", "/tmp/text_overlay_badge.png")
    rec_c = overlay_caption(src, "Подходит для: Кухня / Ванная / Офис",
                            "/tmp/text_overlay_caption.png")
    # 芯片（竖列+底行）+ 场景标签
    rec_ch = overlay_chips(src, ["350 мл", "USB-зарядка", "Без сверления"],
                           "/tmp/text_overlay_chips.png")
    rec_cb = overlay_chips_bottom(src, ["350 мл", "USB", "3 режима"],
                                  "/tmp/text_overlay_chips_bottom.png")
    rec_il = overlay_icon_labels(src, ["Кухня", "Ванная", "Офис"],
                                 "/tmp/text_overlay_labels.png")
    print("selftest ok:", rec, rec_b, rec_c, rec_ch, rec_cb, rec_il)


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
