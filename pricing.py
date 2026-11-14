#!/usr/bin/env python3
"""Ozon 跨境定价计算器（毛子ERP 公式，docs/research/ozon-pricing-formula.md）。

公式（已对拍验证到分）：
    物流费 = base_price + per_gram × 包裹重量(g)
    售价P  = (采购价 + 国内运费 + 物流费)
             ÷ (1 − 佣金率 − 广告率 − 其他率 − 0.33% − 2% − 利润率)
    划线价 = P ÷ (1 − 划线折扣率)
    ₽价    = P × CNYRUB 汇率

两个必须处理的迭代/匹配：
- 佣金按售价₽三档（≤1500/≤5000/>5000），档差可达 6 点 → 低档起算、跨档重算至收敛；
- 物流按采购价货值选尺寸档（extra_small/small/premium_small），查本地费率库。

本店币种 = CNY：计算器输出的 ¥ 售价/划线价直接作为 import 的 price/old_price。
"""
import json
from pathlib import Path

DATA = Path(__file__).parent / "data"
ITO_RATE = 0.0033   # 国际运输组织合同聘用服务（Ozon 官方固定扣点）
WC_RATE = 0.02      # 尾程（Ozon 官方固定扣点）

# 物流尺寸档按采购价¥货值。探测锚点：25→extra_small，200→small，≥800→premium_small。
# shortcut: small→premium_small 边界未精确标定（135<x≤800），暂取 400；越档服务端拒算
def logistics_class(purchase_cny: float) -> str:
    if purchase_cny <= 135:
        return "extra_small"
    if purchase_cny <= 400:
        return "small"
    return "premium_small"


def _load(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def commission_tiers(category_id: int) -> dict:
    """返回 {1: 低档%, 2: 中档%, 3: 高档%}；未命中抛 KeyError（调用方需人工选类目）。"""
    for c in _load("ozon-commission.json")["commission"]:
        if c.get("cate_id") == category_id:
            return {int(k): v for k, v in c["tiers"].items()}
    raise KeyError(f"类目 {category_id} 不在佣金库，需在界面手动选类目")


def shipping_options(cls: str, weight_g: float) -> list[dict]:
    """该尺寸档下所有可用物流方案（含算好的运费），按运费升序。"""
    out = []
    for r in _load("ozon-logistics.json")["classes"].get(cls, []):
        fee = r["base_price_cny"] + r["price_per_gram_cny"] * weight_g
        out.append({**r, "fee": round(fee, 2)})
    return sorted(out, key=lambda x: x["fee"])


def _price_tier(price_rub: float) -> int:
    return 1 if price_rub <= 1500 else (2 if price_rub <= 5000 else 3)


def calc(purchase_cny: float, weight_g: float, category_id: int, *,
         rate_cnyrub: float, china_fee: float = 0.0, ad_rate: float = 0.0,
         other_rate: float = 0.01, profit_rate: float = 0.20,
         old_price_rate: float = 0.50, cls: str | None = None,
         provider: str | None = None, mode: str | None = None,
         commission_tier: int | None = None) -> dict:
    """定价计算，返回售价/划线价/逐项明细。provider/mode 指定物流方案，默认最便宜。
    commission_tier 人工指定佣金档（1/2/3），指定后跳过自动迭代。"""
    tiers = commission_tiers(category_id)
    if commission_tier is not None and commission_tier not in tiers:
        raise ValueError(f"佣金档 {commission_tier} 无效（可选: {sorted(tiers)}）")
    cls = cls or logistics_class(purchase_cny)
    opts = shipping_options(cls, weight_g)
    if provider or mode:
        opts = [o for o in opts
                if (not provider or o["provider"] == provider)
                and (not mode or o["mode"] == mode)]
    if not opts:
        raise ValueError(f"档 {cls} 无符合条件的物流方案（provider={provider}, mode={mode}）")
    ship = opts[0]
    logi_fee = ship["fee"]

    # 佣金档迭代：低档起算，售价₽跨档则用新档重算至收敛（≤3 档，最多 3 次）；
    # 人工指定档位时跳过迭代
    tier = commission_tier or 1
    for _ in range(4):
        comm = tiers[tier] / 100
        denom = 1 - comm - ad_rate - other_rate - ITO_RATE - WC_RATE - profit_rate
        if denom <= 0:
            raise ValueError(f"费率合计≥100%（denom={denom:.3f}），无解")
        price = (purchase_cny + china_fee + logi_fee) / denom
        new_tier = _price_tier(price * rate_cnyrub)
        if commission_tier or new_tier == tier:
            break
        tier = new_tier

    return {
        "price_cny": round(price, 2),
        "old_price_cny": round(price / (1 - old_price_rate), 2),
        "price_rub": round(price * rate_cnyrub, 2),
        "currency": "CNY",
        "commission_tier": tier,
        "commission_pct": tiers[tier],
        "cate_fee": round(price * comm, 2),
        "logistics": {"class": cls, "provider": ship["provider"],
                      "mode": ship["mode"], "delivery_days": ship["delivery_days"],
                      "fee": logi_fee},
        "purchase_cny": purchase_cny,
        "china_fee": china_fee,
        "ad_fee": round(price * ad_rate, 2),
        "other_fee": round(price * other_rate, 2),
        "ito_fee": round(price * ITO_RATE, 2),
        "wc_fee": round(price * WC_RATE, 2),
        "profit_cny": round(price * profit_rate, 2),
        "rate_cnyrub": rate_cnyrub,
    }
