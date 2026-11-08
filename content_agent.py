#!/usr/bin/env python3
"""内容 agent：商品信息 → LLM 理解类目 → 产出套图生成规格（content spec JSON）。

设计原则：类目不让 LLM 猜——用关键词 API 返回的 categoryId 在官方类目树里
机械查表得到俄语路径；LLM 只在"类目风格库 + 文案规则"约束下做创意。

流水线位置: checked(趋势过) 之后 → 产出 spec → image_worker 按 slot 消费。

用法:
    python3 content_agent.py --query "дозатор для жидкого мыла" \
        --title1688 "外贸爆款感应皂液器自动感应泡沫洗手机" \
        --category-id "17027494_17027905_92014" --out data/specs/soap.json
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import BAILIAN_API_KEY, BAILIAN_BASE_URL

ROOT = Path(__file__).parent
LEAVES = json.load(open(ROOT / "data/categories-leaf.json", encoding="utf-8"))

# 过审禁忌词（机械校验，不依赖 LLM 自觉）
BANNED = ["хит", "лучший", "новинка", "скидка", "цена", "акция",
          "купить", "бесплатно", "распродажа", "%"]

# 类目风格库（LLM 的场景/卖点锚点，按一级类目给，可按需扩）
STYLE_GUIDE = {
    "Дом и сад": {
        "scenes": ["modern bathroom with marble countertop",
                   "bright kitchen with wooden surface",
                   "minimal beige wall with green plant"],
        "copy_style": "安装便利与情感点优先（免打孔/无痕/3秒安装），参数数字其次",
    },
    "Электроника": {
        "scenes": ["clean desk setup with soft daylight",
                   "modern living room shelf"],
        "copy_style": "参数数字优先（mAh/Вт/Гц/秒数），一行一个硬指标",
    },
    "_default": {
        "scenes": ["neutral warm home interior, shallow depth of field"],
        "copy_style": "数字优先，2-5词短句",
    },
}

SYSTEM_PROMPT = """你是顶级 Ozon 电商运营。根据商品信息和目标类目，产出商品图的"内容规格"JSON。
严格规则：
1. 所有俄语文案必须母语级地道、2-5 个词、数字优先；禁止机翻长句。
2. 标题(COPY_TITLE) 2-5 词；图标卖点(ICONS) 3-4 条各 2-5 词；其余文案 ≤6 词。
3. 禁用词（出现过审即失败）：хит/лучший/новинка/скидка/цена/акция/купить/бесплатно/%。
4. 只能从我给的场景列表里选场景，不得发明。
5. 卖点必须来自商品真实信息，禁止编造参数；信息不足时写通用体验点（如"Быстрая установка"），并在 need_confirm 标记 true。
输出严格 JSON（不要任何额外文字）：
{
  "category_path_ru": "...", "style_note": "一句话风格判断",
  "ozon_title": "俄语商品标题(≤200字符,核心词前置)",
  "slots": [
    {"slot": "slot1", "scene": "...", "copy_title": "...", "copy_icons": ["...","...","..."]},
    {"slot": "slot3", "copy_headline": "...", "copy_badge": "..."},
    {"slot": "slot4", "action_en": "英文动作描述", "copy_line": "..."},
    {"slot": "slot6", "detail_en": "英文特写部位", "copy_word": "..."},
    {"slot": "slot7", "measurements": "..."}
  ],
  "need_confirm": false
}"""


def lookup_category(category_id: str) -> dict | None:
    for leaf in LEAVES:
        if leaf["categoryId"] == category_id:
            return leaf
    return None


def llm_spec(query: str, title1688: str, cat: dict) -> dict:
    top = cat["pathRu"].split("/")[0]
    guide = STYLE_GUIDE.get(top, STYLE_GUIDE["_default"])
    user = json.dumps({
        "ozon_keyword": query,
        "supplier_title_cn": title1688,
        "category_path_ru": cat["pathRu"],
        "allowed_scenes": guide["scenes"],
        "copy_style": guide["copy_style"],
    }, ensure_ascii=False)
    payload = {
        "model": "qwen-plus",
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "temperature": 0.4,
    }
    req = urllib.request.Request(
        f"{BAILIAN_BASE_URL}/chat/completions",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {BAILIAN_API_KEY}",
                 "Content-Type": "application/json"})
    result = json.load(urllib.request.urlopen(req, timeout=120))
    content = result["choices"][0]["message"]["content"]
    return json.loads(content)


def validate(spec: dict, sources: list[str]) -> list[str]:
    """机械校验：禁忌词、文案长度、slot 合法性、数字溯源。返回问题列表（空=通过）
    sources: 允许出现数字的原始信息（1688标题/竞品属性等）——LLM 写的每个数字
    必须能在 sources 里找到，否则视为编造参数（过审/侵权风险）。"""
    problems = []
    texts = [spec.get("ozon_title", "")]
    for s in spec.get("slots", []):
        texts += [v for k, v in s.items() if k.startswith("copy") and isinstance(v, str)]
        texts += [x for x in s.get("copy_icons", []) if isinstance(x, str)]
        if isinstance(s.get("measurements"), str):
            texts.append(s["measurements"])
    for t in texts:
        low = t.lower()
        for w in BANNED:
            if w in low:
                problems.append(f"禁忌词 '{w}' in: {t}")
        if len(t.split()) > 12:
            problems.append(f"文案过长({len(t.split())}词): {t}")
    # 数字溯源
    src_text = " ".join(sources)
    for t in texts:
        for num in re.findall(r"\d+(?:[.,]\d+)?", t):
            if num not in src_text:
                problems.append(f"数字无出处(疑似编造): {num} in '{t}'")
    slots = {s.get("slot") for s in spec.get("slots", [])}
    if "slot1" not in slots:
        problems.append("缺少 slot1 封面主图规格")
    return problems


def main() -> None:
    args = sys.argv[1:]
    get = lambda k: args[args.index(k) + 1]
    query, title1688, cid = get("--query"), get("--title1688"), get("--category-id")
    out = Path(get("--out")) if "--out" in args else None

    cat = lookup_category(cid)
    if not cat:
        sys.exit(f"categoryId {cid} 不在官方类目树里")
    print(f"类目: {cat['pathRu']}", file=sys.stderr)

    spec = llm_spec(query, title1688, cat)
    spec["_meta"] = {"query": query, "categoryId": cid,
                     "title1688": title1688, "llm": "qwen-plus"}
    problems = validate(spec, sources=[title1688, cat["pathRu"], query])
    spec["_meta"]["validation"] = problems or "pass"
    if problems:
        spec["need_confirm"] = True  # 有校验问题的规格强制人工确认

    text = json.dumps(spec, ensure_ascii=False, indent=2)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(text)
    if problems:
        print(f"⚠️ 校验问题: {problems}", file=sys.stderr)


if __name__ == "__main__":
    main()
