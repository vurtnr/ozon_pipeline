#!/usr/bin/env python3
"""离线白名单构建测试：排除禁售一级类目 + disabled，零积分。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from build_whitelist import build, BANNED_TOP_RU


def leaf(cid, path, disabled=False):
    return {"categoryId": cid, "typeId": 1, "pathRu": path, "disabled": disabled, "depth": 3}


def test_excludes_banned_top_level():
    leaves = [
        leaf("1_2_3", "Одежда/Носки/Мужские"),          # 服装：允许
        leaf("4_5_6", "Электроника/Телефоны/Смартфоны"),  # 电子产品：禁
        leaf("7_8_9", "Обувь/Женская/Сапоги"),          # 鞋类：禁
        leaf("a_b_c", "Аптека/Витамины/Магний"),        # 药店：禁
    ]
    assert build(leaves) == ["1_2_3"]


def test_excludes_all_nine_user_banned():
    paths = ["Электроника/x/y", "Обувь/x/y", "Продукты питания/x/y", "Аптека/x/y",
             "Книги/x/y", "Кино, музыка, видеоигры, софт/x/y",
             "Продукты питания Ozon Fresh/x/y", "Товары для курения и аксессуары/x/y"]
    leaves = [leaf(f"id{i}", p) for i, p in enumerate(paths)]
    assert build(leaves) == []


def test_excludes_disabled():
    leaves = [leaf("1_2_3", "Одежда/Носки/Мужские", disabled=True)]
    assert build(leaves) == []


def test_banned_set_covers_expected():
    for name in ("Электроника", "Обувь", "Продукты питания", "Аптека", "Книги",
                 "Продукты питания Ozon Fresh"):
        assert name in BANNED_TOP_RU


def test_keeps_allowed_apparel_beauty():
    leaves = [leaf("1_2_3", "Одежда/Нижнее бельё/Термобельё"),
              leaf("4_5_6", "Красота и гигиена/Маски/Тканевые")]
    assert build(leaves) == ["1_2_3", "4_5_6"]
