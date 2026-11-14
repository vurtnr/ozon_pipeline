#!/usr/bin/env python3
"""selection_policy 选品约束边界测试（契约先于实现）。

fixture 形状对齐 Seerfar productReport/search/ozon records（2026-11-13 PoC 实测）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from selection_policy import check_record


def rec(**kw) -> dict:
    """合法基线：轻件、跨境可售、价格/上架天数/评论数全部达标。"""
    base = {
        "sku": 1, "weight": 300.0, "price": 1000.0, "upDays": 100,
        "reviewCount": 50, "returnCancellationRate": None,  # 缺失按通过 [user-decided Q11]
        "categoryInfo": {"category": {"crossBorderSellable": True},
                         "cnTitlePath": "服装 > 内衣 > 保暖内衣套装"},
    }
    base.update(kw)
    return base


def cat(path: str) -> dict:
    return {"categoryInfo": {"category": {"crossBorderSellable": True},
                             "cnTitlePath": path}}


# ---- 硬约束边界 ----

def test_weight_boundary():
    assert check_record(rec(weight=500.0)).ok
    assert not check_record(rec(weight=500.01)).ok
    assert not check_record(rec(weight=None)).ok   # 重量未知 = 不可验证，fail closed


def test_price_boundary():
    assert check_record(rec(price=300.0)).ok
    assert check_record(rec(price=5000.0)).ok
    assert not check_record(rec(price=299.99)).ok
    assert not check_record(rec(price=5000.01)).ok
    assert not check_record(rec(price=None)).ok


def test_updays_and_reviewcount():
    assert check_record(rec(upDays=30)).ok
    assert not check_record(rec(upDays=29)).ok
    assert check_record(rec(reviewCount=10)).ok
    assert not check_record(rec(reviewCount=9)).ok


def test_cross_border_required():
    assert not check_record(rec(categoryInfo=cat("服装")["categoryInfo"] | {
        "category": {"crossBorderSellable": False}})).ok
    r = rec()
    r["categoryInfo"]["category"]["crossBorderSellable"] = None
    assert not check_record(r).ok


def test_return_rate_missing_passes():
    assert check_record(rec(returnCancellationRate=None)).ok


# ---- 禁售类目黑名单（docs/m.docx [user-decided]）----

def test_banned_supplements():  # PoC 实测：药店 > 膳食补充剂和维生素 > 维生素
    d = check_record(rec(**cat("药店 > 膳食补充剂和维生素 > 维生素")))
    assert not d.ok and any("三" in r or "七" in r for r in d.reasons)


def test_banned_sports_nutrition():  # PoC 实测：药店 > 运动补剂 > 肌酸
    assert not check_record(rec(**cat("药店 > 运动补剂 > 肌酸"))).ok


def test_banned_tobacco():
    assert not check_record(rec(**cat("烟草制品 > 电子烟 > 一次性电子烟"))).ok


def test_banned_weapon():
    assert not check_record(rec(**cat("运动和旅游 > 冷兵器 > 刀具"))).ok


def test_banned_battery():  # 组八：物流高风险
    assert not check_record(rec(**cat("电子产品 > 电池 > 锂电池"))).ok


def test_banned_liquid_perfume():
    assert not check_record(rec(**cat("美容和卫生 > 香水 > 女士香水"))).ok


def test_banned_infant_food():  # 组七：需资质
    assert not check_record(rec(**cat("食品 > 婴幼儿食品 > 果泥"))).ok


def test_banned_fireworks():
    assert not check_record(rec(**cat("节日用品 > 烟花爆竹 > 烟花"))).ok


def test_banned_live_and_digital():
    assert not check_record(rec(**cat("宠物用品 > 活体动物 > 观赏鱼"))).ok
    assert not check_record(rec(**cat("数字商品 > 游戏订阅 > 激活码"))).ok


# ---- 存活品类（PoC 实测存活明细，必须放行）----

def test_allowed_apparel_beauty():
    for path in ("服装 > 内衣 > 保暖内衣套装", "服装 > 内衣 > 袜子",
                 "服装 > 服装 > 打底裤", "美容和卫生 > 面膜 > 面膜"):
        d = check_record(rec(**cat(path)))
        assert d.ok, f"{path} 被误杀: {d.reasons}"


def test_banned_top_level_with_subcategories():  # [user-decided] 9 个一级类目，自动含子类目
    for path in (
        "电子产品 > 智能手机 > 手机",
        "电子产品 > 配件 > 数据线",
        "鞋类 > 女鞋 > 靴子",
        "食品 > 果汁、水、饮料 > 速溶咖啡",  # PoC 实测存活品，现被一级禁令覆盖
        "药店 > 膳食补充剂和维生素 > 维生素",
        "珠宝 > 首饰 > 项链",
        "书籍 > 小说 > 言情小说",
        "电影、音乐、视频游戏、软件 > 视频游戏 > 主机游戏",
        "Ozon Fresh食品 > 生鲜 > 水果",
        "Ozon Fresh 食品 > 乳制品",  # 空格变体须同样命中
        "慈善 > 慈善捐赠",
    ):
        d = check_record(rec(**cat(path)))
        assert not d.ok, f"{path} 未被一级类目禁令拦截"


def test_reasons_empty_on_pass_nonempty_on_fail():
    assert check_record(rec()).reasons == []
    assert len(check_record(rec(weight=9000)).reasons) > 0
