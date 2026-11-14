#!/usr/bin/env python3
"""定价计算器测试：毛子ERP 公式对拍 + 佣金档迭代 + 物流档匹配。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import pricing


def test_maozi_reference_case():
    """毛子ERP 实测案例：收纳 12% 档，采购¥25，500g，GUOO Express Extra Small。
    官方返回售价 ¥83.31、划线 ¥166.62、佣金 ¥10、物流 ¥28.87、利润 ¥16.66。"""
    r = pricing.calc(25, 500, 17027937, rate_cnyrub=12.4649,
                     profit_rate=0.20, other_rate=0.01, ad_rate=0.0,
                     china_fee=0.0, old_price_rate=0.50,
                     provider="GUOO", mode="express")
    assert r["price_cny"] == pytest.approx(83.31, abs=0.02)
    assert r["old_price_cny"] == pytest.approx(166.62, abs=0.03)
    assert r["logistics"]["fee"] == pytest.approx(28.87, abs=0.01)
    assert r["cate_fee"] == pytest.approx(10.0, abs=0.02)
    assert r["profit_cny"] == pytest.approx(16.66, abs=0.02)
    assert r["commission_tier"] == 1
    assert r["currency"] == "CNY"
    # ₽ 1038.83 落在 ≤1500 档 → 不迭代
    assert r["price_rub"] == pytest.approx(1038.8, abs=0.5)


def test_default_shipping_is_cheapest():
    """不指定 provider/mode 时默认选该档最便宜方案。"""
    opts = pricing.shipping_options("extra_small", 500)
    assert opts == sorted(opts, key=lambda x: x["fee"])
    r = pricing.calc(25, 500, 17027937, rate_cnyrub=12.4649)
    assert r["logistics"]["fee"] == opts[0]["fee"]


def test_commission_tier_iterates_up():
    """高成本品售价₽跨档 → 佣金档应迭代收敛到正确档（费率变高）。"""
    low = pricing.calc(25, 500, 17027937, rate_cnyrub=12.4649)
    high = pricing.calc(300, 500, 17027937, rate_cnyrub=12.4649)
    # 低价卡 ₽≈1039 → 档1；高价卡 ₽ 应 >1500 → 档≥2，佣金率更高
    assert low["commission_tier"] == 1
    assert high["commission_tier"] >= 2
    assert high["commission_pct"] > low["commission_pct"]


def test_commission_tier_manual_override():
    """人工指定佣金档：跳过迭代，直接用指定档费率（费率更高→同成本售价更高）。"""
    auto = pricing.calc(25, 500, 17027937, rate_cnyrub=12.4649)
    forced = pricing.calc(25, 500, 17027937, rate_cnyrub=12.4649,
                          commission_tier=3)
    assert auto["commission_tier"] == 1
    assert forced["commission_tier"] == 3
    assert forced["commission_pct"] == pricing.commission_tiers(17027937)[3]
    assert forced["price_cny"] > auto["price_cny"]


def test_commission_tier_invalid_raises():
    with pytest.raises(ValueError):
        pricing.calc(25, 500, 17027937, rate_cnyrub=12.4649, commission_tier=9)


def test_editor_attrs_whitelist():
    """编辑器属性区=固定白名单（必填+筛选+运营必填），其余一律不显示。"""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    import review_server as RS
    attrs = [{"id": i} for i in (4180, 6403, 85, 23171, 22232, 4389, 11650)]
    out = RS._editor_attrs(attrs)
    ids = [a["id"] for a in out]
    assert 4180 not in ids and 6403 not in ids  # 更多属性里的字段被删除
    assert ids == [85, 23171, 4389, 11650, 22232]  # 保留且按后台顺序
    """编辑器佣金档下拉必须展示当前类目的真实费率，而非通用占位。"""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    import review_server as RS
    opts = RS._commission_options(17028714)
    assert "12%" in opts and "14%" in opts and "18%" in opts  # 家居用品三档
    assert "自动" in opts
    assert "1500" in opts and "5000" in opts


def test_logistics_class_thresholds():
    assert pricing.logistics_class(25) == "extra_small"
    assert pricing.logistics_class(200) == "small"
    assert pricing.logistics_class(800) == "premium_small"


def test_unknown_category_raises():
    with pytest.raises(KeyError):
        pricing.calc(25, 500, 99999999, rate_cnyrub=12.4649)


def test_negative_denominator_raises():
    """费率合计≥100% 必须报错而非算出负价。"""
    with pytest.raises(ValueError):
        pricing.calc(25, 500, 17027937, rate_cnyrub=12.4649,
                     profit_rate=0.95, other_rate=0.05, ad_rate=0.05)
