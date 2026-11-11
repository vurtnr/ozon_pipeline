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

SYSTEM_PROMPT = """你是一位俄罗斯本土电商设计师，深谙 Ozon 平台买家习惯与热卖商品卡的视觉语言，擅长从产品信息中提炼卖点、写出地道俄语电商文案，并规划白底图/场景图/参数图/套装图。根据商品信息和目标类目，产出商品图的"内容规格"JSON。
设计原则（Ozon 热卖逻辑，不可违反）：
1. 套图是销售漏斗：每张图一个信息点；每个卖点必须有事实/数字背书，禁止"质量好"式空话。
2. 所有俄语文案必须母语级地道；芯片/论据每条 1-3 个词（如 "350 мл"、"Без сверления"），数字优先但只能写来源里真实存在的数字。
3. 禁用词（出现过审即失败）：хит/лучший/новинка/скидка/цена/акция/купить/бесплатно/%。
4. 只能从我给的场景列表里选场景，不得发明。
5. 卖点必须来自商品真实信息（供应商标题/竞品标题）；信息不足时写通用体验点（如"Быстрая установка"），并在 need_confirm 标记 true。
输出严格 JSON（不要任何额外文字）：
{
  "category_path_ru": "...", "style_note": "一句话风格判断",
  "ozon_title": "俄语商品标题(≤200字符,核心词前置)",
  "description_ru": "俄语详情介绍(2-4个短句+3-5条卖点短横线列表,≤1000字符,不含价格/联系方式,与标题不重复)",
  "slots": [
    {"slot": "slot1", "scene": "...", "copy_title": "封面大标题(2-5词,主打一个利益点)", "copy_icons": ["3条利益点芯片,各1-3词"]},
    {"slot": "slot3", "scene_hint": "棚拍底色描述(英文)", "copy_headline": "卖点大标题(3-6词)", "args": ["3条论据芯片,各1-3词,数字优先"]},
    {"slot": "slot4", "action_en": "英文动作描述——必须是产品在使用场景中的样子（如在抽屉里/台面上），不要演示机构、不要开箱场景", "copy_line": "..."},
    {"slot": "slot5", "scene": "第二个使用场景(从场景列表选,与slot1不同)", "copy_line": "≤6词"},
    {"slot": "slot6", "detail_en": "英文特写部位", "copy_word": "材质关键词(1-3词)"},
    {"slot": "slot7", "measurements": "..."},
    {"slot": "slot8", "scenarios": ["恰好3个俄语场景短名词,如 Кухня"], "scenarios_en": ["恰好3个对应的英文场景画面描述,同序"]}
  ],
  "need_confirm": false
}"""


def lookup_category(category_id: str) -> dict | None:
    for leaf in LEAVES:
        if leaf["categoryId"] == category_id:
            return leaf
    return None


def llm_spec(query: str, title1688: str, cat: dict,
             competitor_title: str = "") -> dict:
    top = cat["pathRu"].split("/")[0]
    guide = STYLE_GUIDE.get(top, STYLE_GUIDE["_default"])
    user = json.dumps({
        "ozon_keyword": query,
        "supplier_title_cn": title1688,
        "competitor_ozon_title": competitor_title,
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
    desc = spec.get("description_ru", "")
    if not desc:
        problems.append("缺少 description_ru（俄语详情介绍）")
    elif len(desc) > 1000:
        problems.append(f"详情介绍过长({len(desc)}字符>1000)")
    low = desc.lower()
    for w in BANNED:
        if w in low:
            problems.append(f"禁忌词 '{w}' in description_ru")
    # 详情介绍里的数字同样要溯源（标题/卖点的长度词数规则不适用于长文案）
    src_text0 = " ".join(sources)
    for num in re.findall(r"\d+(?:[.,]\d+)?", desc):
        if num not in src_text0:
            problems.append(f"数字无出处(疑似编造): {num} in description_ru")
    for s in spec.get("slots", []):
        texts += [v for k, v in s.items() if k.startswith("copy") and isinstance(v, str)]
        texts += [x for x in s.get("copy_icons", []) if isinstance(x, str)]
        texts += [x for x in s.get("args", []) if isinstance(x, str)]
        texts += [x for x in s.get("scenarios", []) if isinstance(x, str)]
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
    s8 = next((s for s in spec.get("slots", []) if s.get("slot") == "slot8"), None)
    if s8 and not (len(s8.get("scenarios", [])) == 3
                   and len(s8.get("scenarios_en", [])) == 3):
        problems.append("slot8 需要恰好 3 个场景（scenarios 与 scenarios_en 同序等长）")
    s3 = next((s for s in spec.get("slots", []) if s.get("slot") == "slot3"), None)
    if s3 and len(s3.get("args", [])) != 3:
        problems.append("slot3 需要恰好 3 条论据（args）")
    return problems


def main() -> None:
    args = sys.argv[1:]
    get = lambda k: args[args.index(k) + 1]
    query, title1688, cid = get("--query"), get("--title1688"), get("--category-id")
    out = Path(get("--out")) if "--out" in args else None
    competitor = (args[args.index("--competitor-title") + 1]
                  if "--competitor-title" in args else "")

    cat = lookup_category(cid)
    if not cat:
        sys.exit(f"categoryId {cid} 不在官方类目树里")
    print(f"类目: {cat['pathRu']}", file=sys.stderr)

    spec = llm_spec(query, title1688, cat, competitor)
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
