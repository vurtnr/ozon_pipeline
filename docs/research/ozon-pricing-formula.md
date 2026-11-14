# Ozon 跨境定价公式 · 逆向自毛子ERP定价工具

> 调研日期：2026-11-11。数据源：毛子ERP 定价工具（https://ozon.maozierp.com/#/tool/calc，登录态 ego-browser 实测）。
> 服务端接口：`GET api.maozierp.com/api.tool/calc_logistics`（计算在服务端，请求参数即公式全部输入）。
> 配套本地库：`data/ozon-commission.json`（542 叶子类目佣金）、`data/ozon-logistics.json`（60 条物流费率）。

## 1. 定价公式（已验证到分）

**实测案例**：收纳类目 12% 档，采购 ¥25，500g，20×20×10cm，利润 20%，划线折扣 50%，GUOO Express Extra Small
→ 售价 ¥83.31（₽1038.83 @12.4649），划线价 ¥166.62，明细逐项复核吻合。

```
物流费 = base_price + price_per_gram × 包裹重量(g)          # 查 data/ozon-logistics.json

售价P = (采购价 + 国内运费/代贴单 + 物流费)
        ÷ (1 − 佣金率 − 广告费率 − 其他费率 − 0.33% − 2% − 利润率)

划线价 = P ÷ (1 − 划线折扣率)                                # 50% → P×2
₽售价  = P × CNYRUB 汇率                                    # 页面汇率 12.4694（动态）

# 本店币种 = CNY [user-decided 2026-11-11]：计算器输出的 ¥ 售价/划线价直接作为
# /v3/product/import 的 price/old_price（currency_code="CNY"），无需二次换算；
# ₽ 价仅用于佣金分档判定（≤1500₽/≤5000₽ 档）与前台展示口径。
```

**固定扣点**（Ozon 官方，所有卖家适用）：
- `ito_fee`（国际运输组织合同聘用服务）= P × **0.33%**
- `wc_fee`（尾程）= P × **2%**

**可变费率**（界面可调）：佣金率（按类目档）、广告费率（默认 0，ROADMAP 推广准备金 8-10% 对应此项）、其他（提现/货损，默认 1%）、利润率（默认 20%）、划线折扣（默认 50%）。

## 2. 佣金按售价分档（实现必须迭代）

佣金库每叶子类目三档：`1: ≤1500₽ / 2: 1500-5000₽ / 3: >5000₽`，档差可达 6 个点。
**工具服务端会校验档位**：算出的 ₽售价落在所选档之外 → 直接拒算（"该物品计算出来的售价不符合类目佣金规则"）。
→ 我们的计算器实现：先按最低档算 → 若售价跨档 → 用新档费率重算，直至收敛（最多 3 次）。

## 3. 物流尺寸档（按货值匹配）

| 档 | 货值范围（探测值，未精确标定） |
|---|---|
| extra_small | ≤135 元（官方提示 0.01-135） |
| small | 135 < x ≤ ?（200 元实测命中） |
| premium_small | ≥800 元（800/2000/6000 元实测命中；上界未知，20000 元/5kg 返回"没有符合的物流方式"） |

- 运费公式统一 `base + per_gram × g`；每档 11 个供应商（GUOO/OGL/CEL/Ural/ZTO…）× express(空运5-14天)/standard(陆空7-19天)/economy(陆运17-29天)。
- `is_battery`/`is_liquid` 标记带电/液体可承运性——选品含液体（如皂液器场景）时需过滤。
- 默认到取货点价；`_home` 字段为送上门价。
- small→premium_small 边界未精确探测（135<x≤800），实现时按"就近探测+服务端校验失败降级"处理。

## 4. 与 ROADMAP 任务 4（unit_econ）的关系 [user-decided 2026-11-11]

任务 4 拆为两步：先做**审核台人工定价计算器**（本公式 + 本地库），积累 10-20 个真实定价样本后，
再把参数固化成自动判负闸门。计算器输入：采购价（`metrics.sourcing.price_cny` 已有）、
重量/尺寸（图审人工录入）、类目佣金（cate_id join commission 库）、物流档（按货值+重量查 logistics 库）。

## 5. 数据来源与维护

- 佣金：`api.config/get_ozon_cate_commission`（一次性全量返回树）
- 物流：`api.tool/calc_logistics` 响应内 `logistics_fee_list`（需多组货值/重量探测覆盖各尺寸档）
- 汇率：`api.exchange_rate/index?currency_from=CNY`（CNYRUB，每日变动，实现时手动输入或定时抓）
- 刷新方式：重新登录毛子ERP → ego-browser 抓取 → 重跑转换（本次为一次性脚本，未落盘；刷新频率建议每月或定价异常时）
