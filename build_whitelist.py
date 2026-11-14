#!/usr/bin/env python3
"""离线构建选品白名单：允许售卖的 Ozon 叶子类目 id 集合（零积分）。

用途：Seerfar productReport/search/ozon 的 categoryIds 下推（实测：必须传叶子 id，
一级 id 返回 0）。白名单把「明确禁售的一级类目」在服务端就排除，节省积分。

数据来源：data/categories-leaf.json（Ozon 官方树，7363 叶子，id 与 Seerfar 同源，已实测 5/5）。
排除：9 个用户禁售一级类目（俄语）+ 烟草一级（m.docx 组二）+ disabled。
细粒度中文禁售词（m.docx 各组）由运行时 selection_policy.check_record 用
Seerfar cnTitlePath 兜底——本离线树只有俄语 pathRu，无法做中文关键词匹配。

用法: python3 build_whitelist.py
输出: data/selection-whitelist.json {built_at, excluded_top_ru, count, leaf_ids}
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
SRC = ROOT / "data/categories-leaf.json"
OUT = ROOT / "data/selection-whitelist.json"

# 禁售一级类目（俄语精确名，取自 Ozon 官方树 26 个一级）
# 用户 9 个一级 [user-decided 2026-11-14] + 烟草（m.docx 组二）。慈善/珠宝无一级，运行时关键词兜底。
BANNED_TOP_RU: frozenset[str] = frozenset({
    "Электроника",                      # 电子产品
    "Обувь",                            # 鞋类
    "Продукты питания",                 # 食品
    "Аптека",                           # 药店
    "Книги",                            # 书籍
    "Кино, музыка, видеоигры, софт",    # 电影、音乐、视频游戏、软件
    "Продукты питания Ozon Fresh",      # Ozon Fresh食品
    "Товары для курения и аксессуары",  # 烟草（m.docx 组二）
})


def build(leaves: list[dict]) -> list[str]:
    """返回允许售卖的叶子 categoryId 列表（排除禁售一级 + disabled）。"""
    return [l["categoryId"] for l in leaves
            if not l.get("disabled")
            and l["pathRu"].split("/")[0] not in BANNED_TOP_RU]


def main() -> None:
    leaves = json.loads(SRC.read_text(encoding="utf-8"))
    ids = build(leaves)
    OUT.write_text(json.dumps({
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "source": "data/categories-leaf.json",
        "excluded_top_ru": sorted(BANNED_TOP_RU),
        "count": len(ids),
        "leaf_ids": ids,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"白名单叶子数: {len(ids)}/{len(leaves)} → {OUT}")
    kept = sorted({l["pathRu"].split("/")[0] for l in leaves
                   if l["pathRu"].split("/")[0] not in BANNED_TOP_RU and not l.get("disabled")})
    print(f"保留一级类目 {len(kept)} 个:")
    for t in kept:
        print(f"  {t}")


if __name__ == "__main__":
    sys.exit(main())
