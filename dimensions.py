#!/usr/bin/env python3
"""从 1688 供应商营销图 OCR 提取真实尺寸标注（qwen3-vl-plus）。

1688 详情 API（productShippingInfo，含 pkgSizeSource 溯源）需 open.1688.com
企业入驻，现有 Claw AK 拿不到（见 docs/research/1688-real-dimensions.md）。
过渡方案：供应商营销图普遍自带尺寸标注（如挂篮主图标 36-60/25/17.2cm），
VL 提取后作为 slot7 数字溯源闸的出处来源——采信级别=商家自填。

用法: python3 dimensions.py <图片url或本地路径>
输出: 提取到的标注原文（每行一条）；无标注输出 NONE，异常输出空（best-effort）
"""
import base64
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import BAILIAN_API_KEY, BAILIAN_BASE_URL

PROMPT = ("提取图片中所有尺寸/规格/容量标注（数字+单位），原样逐行输出，"
          "例如：36-60cm、25cm、300ml。只输出标注本身，不要解释。"
          "图中没有任何尺寸标注则只输出 NONE")


def extract_dimensions(image: str) -> str:
    """返回图中尺寸标注原文；失败或无标注返回空串（调用方按无数据降级）。"""
    try:
        if image.startswith(("http://", "https://")):
            req = urllib.request.Request(image, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=60).read()
        else:
            raw = Path(image).read_bytes()
        b64 = base64.b64encode(raw).decode()
        body = json.dumps({
            "model": "qwen3-vl-plus",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": PROMPT},
                ],
            }],
        }).encode()
        req = urllib.request.Request(
            BAILIAN_BASE_URL + "/chat/completions", data=body, method="POST",
            headers={"Authorization": f"Bearer {BAILIAN_API_KEY}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            r = json.load(resp)
        text = r["choices"][0]["message"]["content"].strip()
        return "" if text.upper() == "NONE" else text
    except Exception as exc:
        print(f"[dimensions] 提取失败（按无数据降级）: {exc}", file=sys.stderr)
        return ""


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    out = extract_dimensions(sys.argv[1])
    print(out or "(无标注)")
