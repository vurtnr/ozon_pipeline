# Research: Ozon 货源是否只能依赖 1688 · 多渠道货源评估

> 调研日期：2026-11-13。方法：先读 `sourcing_agent.py`、`sync_to_db.py`、`ROADMAP.md`、`docs/research/ozon-agent-harness-deep-research.md`、`docs/research/ozon-first-principles-review.md` 与 `vendor/1688-product-find/`（SKILL.md、cli.py、scripts/_http.py），再对外部渠道做一手来源核查。外部事实仅引平台官方文档、官方开放平台/网关、官方服务条款或官方 GitHub；第三方数据商（大吉/万邦）以**其官方自述**标注定位。未读密钥文件、未改业务代码、未执行真实采购或外部写操作。
> 本文件为 authoritative output；父会话归档目标：`docs/research/ozon-sourcing-alternatives.md`。

---

## 0. 结论摘要

**1688 不是唯一货源，但 V1 内它仍是唯一"官方 API 自动化图搜"的货源渠道，其他渠道在 V1 只做人工兜底，不写代码。** 1688 当前解决的是"发现（图搜/文字搜/比价）+ 结构化字段（价/MOQ/销量/库存）"；同一性、质量、库存时效、发货、发票/授权、履约这些风险属于供应链属性，换任何平台都不会消失，只能靠现有闸门 + 人工选源 + 经济硬闸管理。替代渠道中：大吉（第三方、自称阿里生态合作方）是 ROADMAP 里 Ocean skill 的正牌备胎（含 1688/淘宝/义乌购封装与下单），AliExpress 官方 Open Platform（Affiliate/Dropshipping API）适合"接单后采购"补位，Alibaba.com/MIC/Global Sources 只适合工厂级 RFQ，淘宝开放平台与第三方爬虫数据 API（万邦/凡邦类）不建议接入。

---

## 1. 1688 当前解决了什么（代码级现状）

| 能力 | 现状 | 位置 |
|---|---|---|
| 官方接入 | `vendor/1688-product-find` 走 1688 官方 Ocean 网关 `gateway.1688.com`，API 路径 `/api/alibaba.1688.find.product/1.0.0/{channel}`，OAuth AK + token keyring 管理 | `scripts/_http.py`、`cli.py`（一手代码） |
| 官方收录 | 1688 官方 Skill Hub（clawhub.1688.com）聚合"官方与社区 Skill"，选品找商是官方列出的核心场景；1688AI版服务协议明确"官方技能（官方认证标识）vs 社区技能"之别 | [clawhub.1688.com](https://clawhub.1688.com)、[1688AI版服务协议](https://terms.alicdn.com/legal-agreement/terms/b_platform_service_agreement/20260529174810515/20260529174810515.html) |
| 图搜/文字搜/链接搜 + 比价 | `search_one`（图搜 l=10、销量序）、`search_text`、`search_vl`（qwen-vl 识别→混合搜）、compare 补 SKU/库存（ROADMAP 实测"5/6 出货源"） | `sourcing_agent.py`、ROADMAP V0 |
| 结构化货源字段 | price / sold_count / yx_index / quantity_begin(MOQ) / stock_amount / supplier / similarity_score / detail_url / image_url，每图保留 TOP_KEEP=5 | `_http.py:_parse_product_item`、`pick_suppliers` |
| 同一性闸 | `vl_same_product`：qwen-vl 对拍 Ozon 原图 vs 候选图，异常**按不通过**（fail-closed）；三级阈值 sim≥0.90 auto / 0.80–0.90 review / <0.80 降级文字搜 | `sourcing_agent.py` |
| 人工选源 | top3–5 候选进报告；但 `sync_to_db.py` metrics 只写 `suppliers[0]`（top1 自动锁定，已被三路评审标 High：高销量≠可承接，MOQ/发货/断货未审） | `sync_to_db.py`、`ozon-first-principles-review.md` |
| 单位经济边界 | 经济硬闸公式：佣金+物流+汇率+国内运费+推广准备金(8–10%)+风险参数 → 定价快照；佣金分档 ≤1500₽/≤5000₽ 按卢布判档；皂液器案例 ¥23 vs 350₽ 判负；realFBS 卖家自发、需真实体积重量（官方 import 非零硬要求） | ROADMAP 任务4、harness §3 行7、`ozon-listing-api.md` §1/§3 |

**1688 真正解决的只有"发现+初筛"**：把 Ozon 爆款图变成可点开的候选供应商清单。它**不解决**质量、稳定供货、售后凭证。

## 2. 换平台解决不了的风险（供应链固有属性）

1. **同一性**：任何渠道搜出来的候选都可能是"同功能不同物"（冰箱垫≠冰箱除味盒）——qwen-vl 视觉对拍闸必须对**每个渠道**的候选执行，与渠道无关。
2. **质量**：销量/yx_index 是序数信号，不是质检。轻备货模式下的实际办法是先买样品（1–2 件），与平台无关。
3. **库存时效**：1688 有 `stock_amount` 但只是快照；换平台同样只是快照。断货→清零（V2 库存同步）才是闭环。
4. **MOQ**：工厂货源 MOQ 是物理约束（常 ≥100 件），轻备货模式下 1688/淘宝/义乌购的"一件代发"标签才是匹配项；MOQ 不会因为换平台消失。
5. **发货**：国内段（供应商→中转仓）时效仍需人工确认；国际段（realFBS 自发 vs Ozon Global/FBP）是物流方案选择，与货源平台无关。
6. **发票/授权**：采购凭证（国内账）、供应商图片授权留档（ROADMAP 风险：AI 图侵权/信息不实）、品牌授权（Ozon 端 IP 投诉）都要在**供应商层**留档，不存在"换平台就合规"。
7. **履约**：Ozon 端 import→moderation→设价→设 realFBS 库存→在售，这条链与货源平台完全解耦（官方文档：审核通过≠在售）。

## 3. 渠道系统比较

图例：✅官方证实 / ⚠️部分或未证实 / ❌无。目标商品可得性以本项目品类（家居小商品/日百）为准。

| 渠道 | 目标商品可得性 | 同款/图搜 | 官方 API/自动化 | 低 MOQ | 价格 | 质量/供应商验证 | 交付中国仓/俄 | 凭证与合规 | 许可/数据限制 |
|---|---|---|---|---|---|---|---|---|---|
| 1688（Ocean skill，现用） | ✅ 高（源头厂货） | ✅ 官方图搜+链接同款+compare | ✅ 官方网关 OAuth；另有 open.1688.com ISV 线（企业认证+权限包） | ✅ 一件代发标签 | ✅ 最低档 | ⚠️ 销量/诚信通仅序数 | ✅ 国内任意 | ⚠️ 图片授权需留档 | 免费额度收紧=ROADMAP 中风险；AI版协议约束 skill 用途 |
| 大吉 Daji（dajisaas.com） | ✅ 高（封装 1688/淘宝/义乌购/得物） | ✅ 自述：上传图取 imageId→图搜、关键词搜、店铺搜、榜单 | ✅ 自有 OpenAPI（签名+appKey），**含 1688 下单/支付/订单管理**，多语言含俄语 | ✅（随上游） | 同上游 | ⚠️ 同上游 | ✅（同上游+代采） | ⚠️ "官方认证生态合作伙伴/聚石塔"为**自述**，需商务核验 | 商业服务，按量/签约收费；限额规则来自上游平台考核 |
| 万邦 onebound / 凡邦 fan-b | ⚠️ 中（淘宝/1688/MIC/义乌购等聚合） | ⚠️ 有图搜类接口（数据中转） | ❌ 非官方，爬虫来源数据中转 | ✅ | 同源 | ❌ 不可验证 | ❌ 仅数据 | ❌ 高风险 | 与上游平台服务协议冲突 |
| Alibaba.com 国际站 | ✅ 高（工厂/外贸款，价格高） | ⚠️ 网页有图搜；官方 API 列表只有供应商侧/采购协同类（icbu 商品发布、属性、采购协同），**买家侧图搜 API 未证实** | ❌（买家侧） | ⚠️ 工厂 MOQ 高 | 高 | ✅ Trade Assurance/Audited | ⚠️ 外贸港交 | ✅ 出口凭证齐全 | RFQ 官方流程适合询价 |
| 淘宝/天猫 | ✅ 高（零售现货，MOQ=1） | ⚠️ 网页拍立淘；开放平台搜索 API 官方已收缩、权限收紧（2012 收缩公告沿用至今），个人/采购场景难获批 | ❌（采购场景） | ✅ 1 件 | 零售价（高） | ✅ 7天无理由/店销体系 | ✅ 国内任意 | ✅ 发票好开 | TOP 商品数据 API 限特定场景/企业 |
| AliExpress | ⚠️ 中（同款多为中国仓发货的铺货款） | ⚠️ 无官方图搜；有关键词/类目查询 | ✅ 官方 Open Platform：Affiliate API + Dropshipping API，需开发者账号+App 类型审核；"All Dropshipping API is only for users that join the dropshipping center"（官方文档）；联盟 API 不含库存 | ✅ 1 件 | 零售价（高于 1688） | ✅ 买家评价体系 | ✅ **直发俄罗斯**（唯一内建俄区物流） | ✅ 平台发票/纠纷体系 | 需经审核；下单 API 属 dropshipping 体系 |
| DHgate 敦煌网 | ⚠️ 中（小额批发） | ⚠️ 网页搜索；DOP 官方 REST API 证实为卖家侧（dh.item.add 等）+ Affiliate API quick start（affiliate@dhgate.com），**买家侧商品搜索 API 未证实** | ⚠️ 部分 | ✅ 小批量起批 | 中低 | ⚠️ | ✅ 跨境物流发达 | ⚠️ | DOP 服务协议约束 |
| Made-in-China 中国制造网 | ⚠️ 中低（B2B 外贸款） | ❌ | ⚠️ "open.made-in-china.com 开放平台"仅见于疑似软文教程，官方域名本轮 fetch 失败，**未证实** | ❌ 工厂 MOQ | 中高 | ✅ Audited Suppliers | ⚠️ 港交 | ✅ | RFQ 官方流程 |
| Global Sources | ⚠️ 中低（付费卖家/展会系） | ❌ | ❌ 无公开官方商品 API（官方仅 affiliate 搜索框/供应商数据同步页） | ❌ | 中高 | ✅ 实厂核验+展会 | ⚠️ 港交 | ✅ | — |
| 义乌购/Chinagoods | ✅ 高（小商品档口、一件代发/专供出口标签） | ⚠️ 网页搜索 | ❌ 无公开商品 API：义乌购走"海外官方授权合作伙伴"授权制；Chinagoods 开放平台面向生态伙伴；可经**大吉义乌购适配**获得（VIP 邀约制+通道费，自述含下单/余额支付闭环） | ✅ 混批 | 低 | ✅ 实体档口/执照可查 | ✅ | ⚠️ 档口头层供应商凭证薄 | 授权合作制 |

### A/B/C/D 分类（"有网页"≠"有 API"，逐一核对过官方入口）

- **A 可立即人工采购（零开发）**：1688 网页（现用兜底）、淘宝/天猫（样品单/断货应急，1 件起）、AliExpress 网页（小单测试+俄区直发）、义乌购/Chinagoods 网页（小商品+一件代发标签）、Alibaba.com/敦煌网网页（小批发比价）。
- **B 可开发 adapter（有官方或可签约的接口）**：
  1. **大吉 Open API**（V2 Ocean skill 备胎首选，ROADMAP 已点名）：图搜/关键词搜/详情/店铺/运费/类目 + 1688 下单、支付（免密）、订单管理 + 多语言（含 ru）+ 义乌购适配（邀约制）。定位=第三方官方生态封装，非 1688 本体；接入前须商务核验+合同确认数据授权。
  2. **AliExpress Open Platform**（官方）：Affiliate API（找品/详情，无库存）+ Dropshipping API（限加入 dropshipping center 的用户，App 类型审核）→ 只为"接单后采购"的自动化备选。
  3. DHgate DOP（官方，affiliate/卖家侧证实、买家侧搜索未证实）→ 低优先级，仅在需要敦煌网小批发比价时再评估。
- **C 只适合批量工厂询价（RFQ/人工，不写代码）**：Alibaba.com RFQ + Trade Assurance、Made-in-China RFQ/Audited Suppliers、Global Sources（无公开 API）、1688 实力商家/超级工厂深度询价。触发条件：单 SKU 需求 ≥ 数百件、需要定制/OEM、或要压缩到工厂出厂价。
- **D 不建议/无可靠官方接入**：淘宝 TOP 商品搜索 API 用于找货（官方收缩政策+场景限制）；万邦 onebound/凡邦 fan-b 及同类爬虫数据中转（来源不可验证、与上游协议冲突）；任何自建爬虫；Made-in-China"开放平台"（未证实前不接）；Global Sources 第三方 scraper。

## 4. 多货源策略（目标商品 → 确认供应商）

```text
Ozon 候选(主图+spec)
  → ① 多渠道发现：1688 图搜（自动）→ 无货源/额度受限时 文字搜 → 人工补查
     淘宝/AliExpress/义乌购（网页，人工 5 分钟）；工厂级需求 → RFQ（C 类）
  → ② 同款聚类：cluster_id = 视觉同一性闸(qwen-vl 对拍 Ozon 图) + 规格指纹(类目+关键属性)
     —— 平台 native ID 不可作聚类键（各平台 ID 体系不同且不稳定）
  → ③ 总到手成本排序（不看单价）：货价×量 + 国内运费 + 中转/代发服务费
     + 国际运费(realFBS 自发 | Ozon Global/FBP) + Ozon 佣金(按卢布分档) + 推广准备金8–10% + 风险参数
  → ④ 供应稳定/履约打分（序数）：sold_count、发货时效(24h/48h/72h)、库存快照、
     供应商年限/认证、是否一件代发；低于阈值→只作备选
  → ⑤ 人工选源（top3–5 展示，修复 sync_to_db 只写 suppliers[0] 的 top1 自动锁定）
     同时记录采购模式：轻备货 | 接单后采购
```

**归一化与防重复（最小规则）**：
- Offer 主键 = `(channel, native_id)`：`1688:{offer_id}`（沿用现有 offer_id，gen 资产目录已按它命名）、`aliexpress:{productId}`、`taobao:{item_id|mi_id}`、`yiwugo:{goods_id}`。
- 聚类层：`cluster_id` 挂到 products 行（可加 `products.canonical_query` 同款思路）；同 cluster 只允许一张卡上架，跨渠道 offer 进 cluster 的备选列表——天然复用 sync_to_db 现有 ozon_id/query/offer_id 三重去重闸。
- 图片：本地 sha256 + 来源 URL + 抓取日期 + 供应商图片授权状态字段（ROADMAP 风险项的落点）。
- 规格：统一 schema 见 §6；体积重量必须真实值（Ozon import 官方硬要求非零），缺真值→人工录入环节，不猜。
- **防"只靠低价"**：总到手成本含准备金与风险参数；低价但无一件代发/发货时效差的 offer 自动降级为备选，不允许进 top1。

## 5. V1 最小可执行渠道组合与优先级（首批 ≤20 卡）

| 优先级 | 渠道 | 用途 | 成本 |
|---|---|---|---|
| P0 | 1688 Ocean skill（现有） | 主通道：图搜+同一性闸+top3–5 | 已接入，免费额度内 |
| P0 | 1688 网页人工 | skill 无货源/相似度不过闸时人工找同款；sample 样品单 | 0 开发 |
| P1 | 淘宝/AliExpress 网页人工 | ①样品/断货应急单件采购；②若定"接单后采购"模式，AliExpress 直发俄罗斯可作应急履约（价高，仅小额验证用） | 0 开发 |
| P1 | 义乌购/Chinagoods 网页人工 | 小商品品类扩展（一件代发/专供出口标签筛选） | 0 开发 |
| V2 | 大吉 adapter | Ocean skill 备胎（图搜+库存+下单+俄语多语言），商务核验后签约接入 | 付费+1 个 adapter |
| V2 | AliExpress DS API | 仅当"接单后采购"被证明为主模式 | 审核+adapter |
| 不做 | 淘宝 API、万邦/凡邦、MIC/GS API、DHgate | 见 §3 D 类 | — |

**何时用 1688 / 何时用其他**：
- 默认一切候选：1688 图搜（价格最低+字段全+已在链路）。
- 1688 图搜 ok=False 或 sim<0.80 降级后仍无货源 → 文字搜 → 仍无 → 人工跨渠道（淘宝/AliExpress/义乌购）。
- 单位经济判负 → 先换同 cluster 备选货源（义乌购/淘宝价高但 MOQ=1 可试小单），仍负 → 毙；**不许用降质低价货源硬凑正利润**。
- 图搜命中但 MOQ>轻备货预算 → 找"一件代发"标签或同款替代供应商（人工）。
- 大额工厂单/定制（≥数百件）→ C 类 RFQ 渠道，人工询价，不进自动化。

## 6. 对 Harness 的影响（不重写框架）

**Supplier adapter 最小接口**（每渠道一个文件，包现有函数；1688 实现为现有 run_skill 包装）：

```python
class SupplierAdapter(Protocol):
    channel: str                                   # "1688" | "daji" | "aliexpress" | ...
    def search_by_image(self, image: str, limit: int = 10) -> list[Offer]: ...
    def search_by_keyword(self, query: str, limit: int = 10) -> list[Offer]: ...
    def get_offer(self, offer_id: str) -> Offer: ...   # 价格/库存/MOQ 刷新（下单前必调）
    # Phase 2（仅大吉/AliExpress DS，需 external-write 权限+人工批准）：
    # def place_order(...) / def track_order(...)
```

**统一 Offer schema**（1688 现有字段的超集，缺字段置 null 不猜）：
`channel, offer_id, title, price_cny, currency, moq, stock, sold_count, ship_days, similarity, supplier{name, id, years, cert}, sku[], pkg{L,W,H,weight}, url, image_url, image_license, fetched_at, raw`

关键约束：
- **相似度分不跨平台可比**：sim≥0.90/0.80–0.90 分档闸仅对 1688 有效；其他渠道候选一律过 qwen-vl 同一性闸后直接进人工（1 件采购成本低于错误自动化成本）。
- 速率/预算/缓存：复用 harness `budgets` 表（scope=`tool:<channel>`）+ offer 缓存 24h（价格是慢变量，下单前 `get_offer` 强制刷新）+ 每渠道失败隔离（单渠道挂掉降级下一渠道，不熔断整条找货链）——与 harness 报告 §2.8/降级链设计一致，不需要新框架。
- 下单类调用永远走 `external-write` 权限级 + human_task 批准 + 单步执行（大吉可下单免密支付 = 真实资金动作，必须人工闸）。

## 7. 风险与未验证假设

1. **大吉自述未独立核验**："官方认证生态合作伙伴/聚石塔/无爬虫"是其官网自述，未在阿里巴巴官方渠道找到独立佐证；接入前须商务核验合同、数据授权范围、与 1688 服务协议的兼容性。
2. **万邦/凡邦数据来源**：为爬虫中转（其官网即此定位），存在上游平台封禁与数据质量风险，仅作对照不接入。
3. **AliExpress 细节**：Affiliate API 不含库存、DS API 限 dropshipping center 用户——来自官方文档+二手核对，方法签名以首次实测为准。
4. **DHgate 买家侧搜索 API、Alibaba.com 买家侧图搜 API、open.made-in-china.com**：均未证实存在；不要按"有网页"推测接口。
5. **1688 Ocean 免费额度**：官方未公布长期额度承诺，ROADMAP 已按"收紧/停服=中风险"管理——这正是大吉备胎的价值，但备胎本身待商务核验（见 1）。
6. **跨渠道同一性闸成本**：每候选 2 次 VL 调用，渠道×候选数增长会放大百炼成本，需入 budgets。
7. Ozon 端不变量：真实体积重量必填非零、字典属性禁自由文本、审核通过≠在售、佣金按卢布分档——货源侧所有变化都不改变这些（官方镜像已核，见 ozon-listing-api.md）。

## Contradictions

- 大吉"官方生态伙伴"自述 vs 阿里官方渠道无独立佐证：**记录为待商务核验**，本报告按自述定位（第三方封装服务商）引用。
- 淘宝 TOP：官方 2012 收缩公告（搜索 API 收缩）vs 第三方 2025–2026 教程宣称可申请 `taobao.item.search`：以官方政策为准，教程不可信。
- Made-in-China：教程类文章称有 `open.made-in-china.com` 开放平台 vs 官方域名本轮抓取失败：**未证实**，不入 B 类。

## Missing evidence

- 大吉的实际报价、SLA、数据授权合同条款（需商务接触）。
- AliExpress Dropshipping API 的完整方法清单（官方站点 JS 渲染，本轮仅取得官方文档镜像页与二手核对）。
- DHgate DOP 完整 API 列表中是否存在买家侧搜索（官方 API 列表页 JS 渲染，仅证实卖家侧+affiliate）。
- 义乌购官方授权合作的条款细节（授权制页面存在，条款未公开）。

## Sources

**保留（官方/一手）**
- 本仓库一手代码：`vendor/1688-product-find/`（SKILL.md、cli.py、scripts/_http.py：官方网关/字段/compare）；`sourcing_agent.py`（三级阈值/同一性闸/TOP_KEEP=5）；`sync_to_db.py`（suppliers[0] 锁定/去重闸）。
- 1688 官方：Skill Hub https://clawhub.1688.com ；AI版服务协议 https://terms.alicdn.com/legal-agreement/terms/b_platform_service_agreement/20260529174810515/20260529174810515.html ；开放平台 https://open.1688.com ；合作伙伴入驻协议 https://terms.alicdn.com/legal-agreement/terms/suit_bu1_b2b/suit_bu1_b2b202002111644_70849.html ；应用市场入驻协议 https://terms.alicdn.com/legal-agreement/terms/suit_bu1_b2b/suit_bu1_b2b201802011720_49107.html
- 大吉（自述定位）：https://www.dajisaas.com/ ；能力清单 https://wiki.dajisaas.com/9668855m0 ；平台概述 https://wiki.dajisaas.com/overview ；FAQ/限额 https://wiki.dajisaas.com/faq
- 万邦（自述定位，第三方数据中转）：https://open.onebound.cn/help/api/yiwugo.item_search_shop.html
- AliExpress 官方：Open Platform 入口 https://openservice.aliexpress.com/doc/api.htm ；Dropshipping 接入步骤（官方文档镜像）https://developer.alibaba.com/docs/doc.htm?treeId=762&articleId=120418&docType=1 ；Affiliate API（TOP 侧已废弃公告）https://developer.alibaba.com/docs/doc.htm?articleId=118195&docType=1&treeId=674
- 淘宝 TOP 官方：搜索 API 收缩政策 https://open.alitrip.com/support/hotProblemDetail.htm?id=109819
- Alibaba.com 国际站官方 API 列表（供应商侧/采购协同）：https://developer.alibaba.com/docs/api_list.htm
- DHgate 官方：Open Platform https://open.dhgate.com/ ；API 调用说明 https://open.dhgate.com/docs/getting-started/api-use-detail ；API 列表（卖家侧）https://open.dhgate.com/docs/api/001001 ；开放平台协议 https://open.dhgate.com/docs/getting-started/agreement
- 义乌购官方：海外授权合作伙伴 https://en.yiwugo.com/partners/index.html ；Chinagoods 官方开放平台页 https://www.chinagoods.com/creation/platform
- Ozon 官方（本仓库已核镜像，沿用）：docs.ozon.ru/api/seller 入口；商品 import/审核语义 https://github.com/DragonSigh/ozon-seller-api-docs/blob/master/uploading-and-updating-products-1.md ；类目/属性镜像 https://salacoste.github.io/ozon-daytona-seller-api/daytona-ozon-api-docs/categories/11-category.html ；官方 TG https://t.me/s/OzonEnSellerAPI

**拒绝/降权（不作为事实来源）**
- 阿里云开发者社区/51CTO/CSDN"1688 API 接入教程"类（Onebound 等中转商 SEO 软文）——仅用于反证其第三方身份。
- elecfans/jishuzhan"MIC 开放平台接入"教程——疑似中转商软文，官方域名未证实。
- quarra/quarralabs、kaviolabs（AliExpress API 二手核对）——仅用于与官方镜像交叉确认"Affiliate 无库存/DS 限 dropshipping center"，主证据仍为官方文档镜像。
- api.sh/init.computer/Apify 等 Global Sources/MIC/义乌购 scraper 商品页——非官方，弃用。

## Next steps

1. 大吉商务接触：确认授权链条、计费、1688/淘宝/义乌购覆盖与限额实测（半天）。
2. 若"接单后采购"定为主模式：注册 AliExpress 开发者账号 + Dropshipping App 类型，小样本验证 `aliexpress.affiliate.product.query` 与下单链路。
3. 实现 `sync_to_db`/审核台的 top3–5 展示与人工选源落库（消除 suppliers[0] 自动锁定）——这是多货源策略的前置工程，无外部依赖。
