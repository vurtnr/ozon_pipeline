# Research: 用 Nexscope 类目级接口拉真实热销商品池 → Ozon 现有选品流程的可行性

> 调研日期：2026-11-13。
> 输入代码/文档（本地一手）：`nexscope_client.py`、`keyword_library.py`、`select_products.py`、`sourcing_agent.py`、`pipeline.py`、`docs/research/ozon-listing-api.md`、已有 Nexscope 实测 artifact `data/candidates-2026-11-10.json`（真实 marketKeywordSearch 输出）。
> 来源原则：Nexscope 侧全部取官方文档（nexscope.ai/api-docs 与官方 GitHub `nexscope-ai/nexscope-ecommerce-api`）逐条核对；Ozon 侧沿用 `ozon-listing-api.md` 的镜像原则（docs.ozon.ru 对本环境 Antibot 403）。证据分级标注：**[官方文档]** / **[实测]** / **[推断]**（推断=研究者由一手信号推理，非来源原文）。
> 本报告不改任何业务代码、不读密钥、不执行付费调用与外部写。

## Summary

方案可行：Nexscope 官方 `/mp/ozon/categoryProducts` 就是"按类目拉全平台商品池"的接口（pageSize≤100、按 revenue/sales 排序、数值过滤），10–25 积分/次，**一次调用≈一个类目 top-100 池**；但前提是否定一个错误假设——Ozon 官方 Seller API/Analytics **没有**按全平台类目拉热销/竞品的接口（分析接口全为自店数据；平台级"什么好卖"只在卖家后台网页与合作方申请制的 Statistics API 里）。同时必须明确：Nexscope 的类目销量/销售额是第三方（MPSTATS/Seerfar 系）**估算值**，官方文档明示不得当作市场官方数据。按 330 积分/日预算，categoryProducts 路径可建 13–33 个类目池/日（≈1,300–3,300 行），发现能力提升 1–2 个数量级；瓶颈仍在找货 VL、生图、人工图审与上架吞吐，拉 100 商品 ≠ 上架 100。

## Findings

### 1. Nexscope 现有接口的真实语义、分页与计费（回答任务问题 1）

**F1.1 categoryProducts（`/mp/ozon/categoryProducts`，官方名 "Ozon Intelligence MP"）**
- **Claim:** 按**俄语类目全路径**（`categoryPath`，"/" 分级，必填）返回类目内商品池：分页 `page` 从 1 起、`pageSize` 1–100 默认 100；`sortField`（snake_case：sales/revenue/final_price/balance/rating）+ `sortDirection` asc/desc；`filters[]` 数值条件 `{field, op, value, value2?}` 多条件 AND（官方示例 op=GTE）；`startDate/endDate` YYYY-MM-DD 最晚=昨天；`includeFbs`、`currency/currencyRate` 可选。响应 `total`=**类目命中总数**，`products[]` 每行含 productId/title/brand(Id)/sellerName(Id)/imageUrl/productPageUrl/price/oldPrice/ozonCardPrice/rating/reviewCount/balance/balanceFbs/turnoverDays/lostProfit/daysInSite/daysInStock/salesPerDay/monthlySalesUnits/monthlySalesRevenue/position/categoryPosition/revenueSharePercent 等 42 字段。
- **Sources:** [Nexscope 官方 API 页](https://www.nexscope.ai/api-docs/ozon-category-products)、[官方 GitHub 文档](https://github.com/nexscope-ai/nexscope-ecommerce-api/blob/main/docs/data/ozon-category-products.md)
- **Support:** direct evidence（官方参数表+字段表原文）
- **Confidence:** high

**F1.2 销量口径与默认窗口**
- **Claim:** 官方 AI 指南明示 "monthly 字段名不必然等于自然月"，销量是"请求统计周期内"的值；每个 API 页均带免责声明 *"Do not assume official marketplace data or real-time freshness when it is not explicitly documented."*。不传 startDate/endDate 时默认窗口文档未写（categorySearch 官方示例为 2026-08-27→2026-09-26 滚动约 30 天，可类推，属推断）。
- **Sources:** [categoryProducts 官方页（Data explained/AI Guide 段）](https://www.nexscope.ai/api-docs/ozon-category-products)
- **Support:** direct evidence（免责原文）+ 推断（默认窗口 30 天）
- **Confidence:** high（免责）/ medium（默认窗口）

**F1.3 categorySearch（`/sr/ozon/categorySearch`，官方名 "Ozon Intelligence SR"）**
- **Claim:** 入参是 `categoryId`（多级 "_" 连接，即 fullCategoryId）+ 可选 `date`（yyyy-MM 历史月）、`fulfillment`（单值 FBO/FBS/RFBS/FBP/OZON）；`page` 必填 `{page, pageSize, orders[]}`，orders 常用字段 sales/price/revenue/reviewRating。**pageSize 上限 20，超过返回 errcode 1002**（官方参数表原文）。响应=类目聚合（totalSales/totalRevenue/avgPrice/rating/**seasonalityAmplitude/seasonalityCoef**/sellerType 配送方式分布/categoryInfo）+ 商品列表（`data` 与 `products` 内容相同），且 **`total` 是当前页行数、不是类目商品总数**，翻页必须用 `hasNextPage`。
- **Sources:** [Nexscope 官方 API 页](https://www.nexscope.ai/api-docs/ozon-category-search)
- **Support:** direct evidence
- **Confidence:** high
- **影响:** categorySearch 与 categoryProducts 的 `total` 语义不同；两者单行成本差 6–16 倍（见 F1.5），**商品池应统一走 categoryProducts，categorySearch 只用作类目级聚合/季节标签的一次性调用**。

**F1.4 marketKeywordSearch（`/sr/ozon/marketKeywordSearch`，现词库在用）**
- **Claim:** 官方文档称 `keywords[]` 最多 1000 且有 `matchType`（0 精确/1 模糊）、`page.pageSize` 默认 20；行字段 searchVolume（月搜）/count30GrowthRate/competingProducts/sellers/avgPrice/returnCancellationRate/ca/categories/categoryInfos/products（top 商品 ozonId/sku/title/imageUrl/advert）。计费 Recent Avg 32 · Range 13–32 积分/次；实测 32/次。
- **Sources:** [Nexscope 官方 API 页](https://www.nexscope.ai/api-docs/ozon-market-keyword-search)；实测记录在 `nexscope_client.py`（2026-11 自账户消耗记录）
- **Support:** direct evidence；**实测矛盾**见 Contradictions #1（传 2 词返回 0 行仍计费 → 保持一词一调）
- **Confidence:** high

**F1.5 积分计费与免费核对通道**
- **Claim:** ① Data API 每次执行至少 10 积分（官方 Quickstart）；② 响应 `cost` 字段是毫秒不是积分（官方 FAQ）；③ 积分分订阅积分与 Data 充值积分（top-up 仅限 Data API，官方 FAQ）；④ **免费**核对真实扣费：`GET /api/skill-api/v1/account/credits`（0 积分，返回 permanentCredit/cycleCredit/debt/netCredit/**grossConsumedCredit**（窗口内结算台账消耗、退款前））与 Account Usage API（0 积分，逐条记录业务调用），两查询共享 60 次/60s 限速；⑤ 平台限速 1 req/s、1 并发（官方 Pricing）。
- **Sources:** [Quickstart](https://www.nexscope.ai/api-docs/quickstart)、[Account Credits API](https://www.nexscope.ai/api-docs/account-credits)、[FAQ credits](https://www.nexscope.ai/faq/how-credits-work)、[FAQ cost 字段](https://www.nexscope.ai/faq/api-response-cost-field)、[Pricing](https://www.nexscope.ai/pricing)
- **Support:** direct evidence
- **Confidence:** high
- **校准提示 [推断]:** 现客户端 `CREDITS` 记账用实测下限（categoryProducts=10），而官方区间均值 20、上限 25——日熔断按 10 记账会最多低估 2.5×，熔断触发偏晚。建议记账取区间上限（25/32）或每周用 account/credits 实测校准（不改代码，属实施建议）。

**F1.6 每天 320–330 积分能拉多少（公式）**
- **Claim:** 设日预算 B（任务口径 320，仓库熔断 `DAILY_CREDIT_CAP=330`，两者取整差 1 次调用）、单次成本 c、每页行数 R、每类目页数 k：
  `调用数/天 = ⌊B/c⌋`；`商品行/天 = ⌊B/c⌋ × R`；`类目池/天 = ⌊B/c⌋ ÷ k`
  - **categoryProducts**（R=100，c 实测 10 / 官方均值 20 / 官方上限 25，B=330）：**33 / 16 / 13 个类目池/天**（top-100 池，k=1）→ **3,300 / 1,600 / 1,300 行/天**；B=320 时 32/16/12。
  - **categorySearch**（R≤20，c 实测 32 / 官方下限 16）：B=330 → 10 页/天 → ≤200 行/天（c=16 时 ≤400 行）。
  - 每行成本：categoryProducts **0.10–0.25 积分/行** vs categorySearch **0.80–2.00 积分/行** → 同一份 50–100 商品池，categoryProducts 1 次调用（10–25 积分）搞定，categorySearch 需 3–5 页（48–160 积分），**差 6–16 倍**。
  - 月度（10,000 积分）：**400–1,000 个 top-100 类目池**（c=25→400；c=10→1,000）。
  - 假设清单：① 额度为 1 万档且可全部用于 Data API（订阅积分/Data 积分构成需 account/credits 实读确认，见 Missing evidence）；② 单次扣费取区间值且 PoC 前按上限预算；③ 不计失败重试（`code!=0` 不入账，现客户端只在成功时记账）。
- **Sources:** 各官方 API 页计费标注；`nexscope_client.py` 实测值
- **Support:** direct evidence（区间）+ researcher inference（公式推导）
- **Confidence:** high（区间与上限）/ medium（实际单次均值，待 PoC 实测）

**F1.7 数据性质：第三方估算，不是 Ozon 官方数据**
- **Claim:** categoryProducts 的底层数据来自 MPSTATS 分析库，categorySearch 来自 Seerfar 分析库（Nexscope 官方 skills 生态的原文："MPSTATS Ozon Category Products … from the MPSTATS analytics database"、"Seerfar Ozon Category Search … from the Seerfar analytics database"；官方 categorySearch 示例响应含 `"sourceTool": "Seerfar-Ozon-查类目"`；mcpmarket 生态页："Data is sourced through MPSTATS e-commerce analytics via the Nexscope API"）。**[推断]** MPSTATS/Seerfar 系的竞品销量由间接信号估算（Ozon 2023-09 起以申请制向数据服务商开放 Statistics API，提供商品/价格/库存/评分/评论，但不提供卖家销量），因此销量数字 ≠ Ozon 财务口径，只能作筛选信号；俄语综述同口径（"外部评估别人商品按间接特征估算"）。
- **Sources:** [skills.sh 官方 skill：category-products](https://www.skills.sh/nexscope-ai/nexscope-ecommerce-skills/ecommerce-ozon-category-products)、[skills.sh：category-search](https://www.skills.sh/nexscope-ai/nexscope-ecommerce-skills/ecommerce-ozon-category-search)、[mcpmarket](https://mcpmarket.com/tools/skills/ozon-category-product-intelligence)、[searchengines.guru 2023-09 Statistics API 新闻](https://searchengines.guru/ru/news/2057633)、[wiki.optimism.ru](https://wiki.optimism.ru/%D0%90%D0%BD%D0%B0%D0%BB%D0%B8%D1%82%D0%B8%D0%BA%D0%B0_Ozon)
- **Support:** direct evidence（数据源标注+官方免责）+ interpretation（估算方法学）
- **Confidence:** high（第三方数据源）/ medium（具体估算方法）

### 2. Ozon 官方能力核验：否定"官方接口按类目拉热销"假设（回答任务问题 2）

**F2.1 错误假设被否定**
- **Claim:** 对普通跨境卖家，**Ozon 官方不存在"按全平台类目拉取热销商品/竞品清单"的 Seller API 接口**。具体：① Seller API 的分析方法全部是自店数据——`/v1/analytics/data` 官方描述"与个人中心『分析→图表』相符"，指标/维度均针对自己 SKU，限 1 次/分钟，无 Premium 订阅仅近 3 个月+有限指标；`/v1/analytics/stocks`、`/v1/analytics/turnover/stocks`、`/v2/analytics/stock_on_warehouses`、`/v1/analytics/average-delivery-time` 同为自店。② 466 个官方方法（Seller 420 + Performance 46）枚举中没有任何平台级类目商品榜端点；平台级数据仅两类出口：(a) **卖家后台网页**（无公开 API）——"Аналитика → Что продавать на Ozon" 与 "Данные по категориям"（28 天品类需求报告，2024-02 向全体卖家开放，页面 URL `seller.ozon.ru/app/analytics/what-to-sell/categories-data`），第三方只能浏览器登录态抓取（`data.ozon.ru/app/bestsellers` 抓取项目、what-to-sell 周期 dump 项目即反证）；(b) **Statistics API**（`docs.ozon.ru/api/statistics`，2023-09 开放，**申请制**）——面向"为卖家开发第三方产品的开发者/分析服务"，提供平台商品特性/价格/库存/评分/评论，普通卖家申请不到，且它也不含"热销榜"成品。③ Premium 里的 `/v1/search-queries/top`（平台热门搜索词，PREMIUM_PRO）是**词级**平台需求数据，不是类目商品列表。
- **Sources:** [Apifox 官方 spec 镜像 /v1/analytics/data](https://1gtgicrc0d.apifox.cn/api-121998415)、[salacoste GitHub Ozon Seller API 镜像·Premium](https://salacoste.github.io/ozon-daytona-seller-api/daytona-ozon-api-docs/categories/26-premium.md)、[466 方法目录](https://github.com/wbcyclist/OZON_MCP/blob/main/methods_catalog.md)、[searchengines.guru 2024-02 后台报告新闻](https://searchengines.guru/ru/news/2058379)、[searchengines.guru 2023-09 Statistics API 新闻](https://searchengines.guru/ru/news/2057633)、[data_paqu（登录态抓取反证）](https://github.com/zhangman4706/data_paqu)、[ITech-Ozon（登录态 dump 反证）](https://github.com/MrExpen/ITech-Ozon)、[wiki.optimism.ru（自店 vs 外部估算综述）](https://wiki.optimism.ru/%D0%90%D0%BD%D0%B0%D0%BB%D0%B8%D1%82%D0%B8%D0%BA%D0%B0_Ozon)
- **Support:** direct evidence（镜像原文+新闻原文+方法枚举）
- **Confidence:** high
- **落地含义:** "用 Ozon 官方接口按类目拉 50–100 热销竞品"不成立；可行路径只有 ① Nexscope categoryProducts（本方案）；② 后台网页人工看 + 登录态抓取（平台 ToS 风险，不采纳）。

### 3. 流程设计：category → 50–100 商品池 → 去重 → 合规/价格/竞争 → 1688 图搜 → 趋势/经济 → 图审（回答任务问题 3）

以下为 **researcher 设计**（基于上述一手接口事实），非来源陈述。

**3.1 池构建（替代词级 harvest）**
- 白名单叶类目来自 `data/categories-leaf.json`（跳过 `TOP_BLACKLIST` 顶层）；`categoryPath` 直接用 `pathRu`。
- 调用模板：`categoryProducts{categoryPath, startDate=T-30, endDate=T-1, page=1, pageSize=100, sortField:"revenue", sortDirection:"desc", filters:[{field:"rating",op:"GTE",value:4.5}]}`（官方示例同款过滤；价格带用 `final_price` 过滤在服务端做，省积分）。
- 每类目留 50–100 行入库（建议新表 `category_products`，`UNIQUE(productId)`），字段直接用官方 42 字段原子值——阈值读时过滤、不写死（沿用 keyword_library 的"漏斗阈值读时过滤"原则）。

**3.2 去重**
- 全局按 `productId` 跨类目去重；并与 `keywords.top_products` 已消费 ozonId、`pipeline.db products.ozon_id` 求差（防同一竞品重复进流程）。注意 `select_products.py` 现有近似：相关词未必同类目——池模式天然消除该 shortcut（categoryId 由类目路径原生携带）。

**3.3 词级闸 → 品级闸（哪些词级闸不再适用）**
- 现行 `keyword_library.passes` 的词级阈值（月搜≥5000、growth30d≥-5%、词竞争≤3000、价带 300–5000₽、退货率≤20%）描述的是"词"，池模式下逐品不可得，替代映射：
  - 词竞争度 → 类目级：categorySearch 一次调用的 `totalRevenue/avgPrice/sellerType` + 品级 `revenueSharePercent/categoryPosition`（头部集中度：集中度低=长尾可切）。
  - 词月搜 MIN_SEARCH_VOLUME → 品级 `monthlySalesUnits`（统计期直接销量证据）。
  - growth30d → 见 3.4（不再逐词，改为类目基线+短名单复查）。
  - PRICE_BAND → 品级 `price/final_price` 服务端 filter。
  - 词级退货率 → **池模式下无逐品替代字段**（categoryProducts 不返回退货/取消率）；合规闸改用类目顶层黑名单 + 1688 货源属性 + 图审把关（原闸只放弃词级维度，合规维度保留）。
- 新增品级闸（官方字段直接支撑）：`rating≥4.5 且 reviewCount≥N`（质量下限）、`salesPerDay≥阈值`、`balance/turnoverDays/lostProfit`（缺货机会 vs 过饱和）、`daysInSite`（新品能进场=非垄断老链接）。

**3.4 趋势基线与每品复查（批量降本的关键）**
- 类目基线（一次 16–32 积分）：categorySearch 的 `seasonalityAmplitude/seasonalityCoef/totalSales/totalRevenue` 直接充当该类目的"趋势基线 + 规模 + 旺淡季标签"，取代词级 growth30d 的角色。
- 品级趋势：不逐品购买。仅对池内短名单 top-N（如 20 个）做复查，二选一：① categoryProducts 双窗口对比（T-30..T-0 vs T-60..T-30）；② 仅对 final 竞品用 Ozon Product Detail（官方单 SKU 销量+日趋势接口）。成本从"每词 32 积分"降为"每类目 10–25 积分×窗口数"，摊到 100 品 ≈ **0.2–0.5 积分/品**。
- 词库降级：`keyword_library` 保留用于"类目内选词→标题/属性优化"，不再是选品入口。

**3.5 漏斗示例（假设值，跑两周后用真实数据校准）**
100 品池 → 品级闸 ~20 → 1688 货源+同一性闸 ~10 → 单位经济达标 ~5 → 生图 5 → 人工图审过 3–5 → 上架 3–5（`/v4/product/info/limit` 实测 daily_create=100/天，远不构成约束，见 `ozon-listing-api.md` §4）。

### 4. 瓶颈对比：当前 5 品/日 vs 类目商品池（回答任务问题 4）

**researcher 分析**（基于本地代码事实与官方限额）：
- **发现层（被解决）:** 现状=词库 pop 5 词/日（harvest 32 积分/词，1 词≈50 行+top3 商品）；池模式=10–25 积分拿 100 品/类目、日预算可建 13–33 个类目池——发现能力提升 1–2 个数量级；1 req/s 限速（86,400 次/天）远不构成约束。**拉 100 商品 ≠ 上架 100：池是 backlog，不是吞吐承诺。**
- **真瓶颈（不变）:**
  1. **找货 VL**：`sourcing_agent` 每候选 1 次 qwen3-vl 识别 + 1 次 1688 搜 + 最多 8 次同一性对拍（`identity_gate max_checks=8`）——每品多次 VL 调用，成本与延迟的主要来源；100 品/日全跑会立即撞预算。
  2. **生图**：每上架品一套图（pipeline `imaging` 状态）。
  3. **人工图审**：`reviews` 表 approve/regenerate/kill 需人力节拍，是每日真实吞吐上限（≈3–5 品/天）。
  4. **上架限额**：官方 `/v4/product/info/limit`，后台实测每日可添加 100、可更改 2,000（`ozon-listing-api.md` §4）——被人工图审完全掩盖，非实际约束。
- **结论:** 池方案的正确收益是"候选积压 + 把贵阶段（VL/生图/图审）留给二次筛过的品"，并在词库枯竭时持续供血；日上架量仍由图审人力决定（≈3–5 品/天），与池大小无关。

### 5. 最小 PoC 与验收（回答任务问题 5；0–2 次付费调用，无外部写）

**researcher 设计**，全部使用官方免费端点与已有缓存优先：
- **PoC-0（0 积分）:** 调 `GET /api/skill-api/v1/account/credits`（免费）记录 `grossConsumedCredit` 基线与积分构成（顺带验证 1 万档是否全为可用 Data 积分）；调 Account Usage（免费）核对近 30 天 categoryProducts/marketKeywordSearch **真实单次扣费分布**，校准 `CREDITS` 表估计偏差。
- **PoC-1（1 次付费，≤25 积分）:** 从 `data/categories-leaf.json` 白名单取 1 个叶类目（pathRu 即 categoryPath；`data/candidates-2026-11-10.json` 已有真实类目可交叉验证），调 `categoryProducts{page:1, pageSize:100, sortField:"revenue", sortDirection:"desc"}`；调用后再读 account/credits，**差值=真实扣费**。验收标准：
  1. HTTP 200 且外层 `code=0`；
  2. `len(products)≥50`，且 ≥80% 行 `sales/monthlySalesUnits`、`price`、`imageUrl` 非空；
  3. 真实扣费 ∈ [10,25] 且与 `costTime` 无关（cost 是毫秒）；
  4. 记录 `total` 与翻页语义（官方字段表 vs GitHub fixture 冲突，见 Contradictions #3，必须实测裁决）；
  5. 不重试失败调用（防烧积分），熔断保持 `DAILY_CREDIT_CAP=330`。
- **PoC-2（可选 1 次付费，≤32 积分）:** 同 `categoryId` 调 `categorySearch`（pageSize=20）验证聚合字段与 seasonality 标签；并实测 `pageSize>20 → errcode 1002`。
- **通过后才允许**：在 `nexscope_client.py` 增加 `category_products()` 薄封装（`CREDITS` 按 25 保守记账）+ `category_products` 表 + 池闸配置——本轮不实施（不改业务代码）。

## Contradictions

1. **marketKeywordSearch 批量语义：** 官方文档称 `keywords[]` 最多 1000 + `matchType` 精确/模糊（[官方页](https://www.nexscope.ai/api-docs/ozon-market-keyword-search)）；**实测**（`nexscope_client.py` 2026-11-10）传 2 词返回 0 行仍计费 → 按实测执行：一词一调。
2. **categoryProducts 单次扣费：** 官方区间 10–25（均值 20）vs 实测 10（账户消耗记录）。PoC-1 实测均值后修正预算公式；熔断建议按上限 25 记账（推断）。
3. **categoryProducts 的 `total` 语义：** 官方 API 页字段表写"类目命中总数（The total number of hits in the category）"，而官方 GitHub 仓库 fixture 示例中 `total=1` 且 `hasNextPage=true`（[repo 文档](https://github.com/nexscope-ai/nexscope-ecommerce-api/blob/main/docs/data/ozon-category-products.md)）——两处不一致，翻页逻辑以 PoC 实测为准。categorySearch 的 `total` 则明确是当前页行数（官方原文）。
4. **数据"准确性"宣传 vs 官方免责：** 生态页称 "accurate sales metrics"（[mcpmarket](https://mcpmarket.com/tools/skills/ozon-category-product-intelligence)），但 Nexscope 官方每个 API 页明示 *"Do not assume official marketplace data…"*。以官方免责为准：销量按估算信号使用，不作财务事实。

## Missing evidence

- categoryProducts 不传 `startDate/endDate` 时的默认统计窗口（文档未写；"monthly≠自然月"为官方明示）。
- 各端点扣费落在区间内位置的实际决定因子与均值（官方只给区间；PoC-0/1 实测补）。
- 池模式下退货率/加购转化率的逐品替代字段（categoryProducts 不返回；仅词级接口有）。
- Statistics API 当前申请条件、开放范围与配额（docs.ozon.ru 对本环境 403，无法一手核验；仅有 2023-09 新闻佐证）。
- 10,000 档积分的构成（订阅积分 vs Data 充值积分是否全部可用于 Data API）——需 account/credits 实读。
- Nexscope 与 MPSTATS/Seerfar 的商业关系（自营聚合 vs 转售）官方未声明——仅生态页与示例响应为间接证据。

## Sources

**Kept（一手/官方）：**
- [Nexscope 官方 categoryProducts API 页](https://www.nexscope.ai/api-docs/ozon-category-products) — 端点路径/参数/pageSize 1–100/排序/过滤/计费 10–25/42 字段/官方免责
- [Nexscope 官方 categorySearch API 页](https://www.nexscope.ai/api-docs/ozon-category-search) — pageSize 上限 20/errcode 1002/聚合与季节字段/计费 16–32/total=当前页行数
- [Nexscope 官方 marketKeywordSearch API 页](https://www.nexscope.ai/api-docs/ozon-market-keyword-search) — 词级行字段/计费 13–32/官方 keywords 参数（与实测矛盾待记）
- [Nexscope Account Credits API](https://www.nexscope.ai/api-docs/account-credits) — 免费台账核对（PoC-0 核心）
- [Nexscope 官方 GitHub nexscope-ecommerce-api](https://github.com/nexscope-ai/nexscope-ecommerce-api/blob/main/docs/data/ozon-category-products.md) — 官方请求样例/响应 fixture/发布字段
- [Nexscope Quickstart / FAQ / Pricing](https://www.nexscope.ai/api-docs/quickstart) — 最低 10 积分/cost=毫秒/积分类型/1 req/s
- [skills.sh：MPSTATS Ozon Category Products（Nexscope 官方 skill）](https://www.skills.sh/nexscope-ai/nexscope-ecommerce-skills/ecommerce-ozon-category-products) — categoryProducts 数据源证据
- [skills.sh：Seerfar Ozon Category Search（Nexscope 官方 skill）](https://www.skills.sh/nexscope-ai/nexscope-ecommerce-skills/ecommerce-ozon-category-search) — categorySearch 数据源证据
- [Apifox 官方 spec 镜像 /v1/analytics/data](https://1gtgicrc0d.apifox.cn/api-121998415) — 自店语义+1 次/分钟（Ozon 官方 spec 镜像）
- [salacoste GitHub Ozon Seller API 镜像·Premium 分析](https://salacoste.github.io/ozon-daytona-seller-api/daytona-ozon-api-docs/categories/26-premium.md) — Premium 门槛/无订阅仅 3 个月
- [wbcyclist/OZON_MCP methods_catalog（466 方法）](https://github.com/wbcyclist/OZON_MCP/blob/main/methods_catalog.md) — 无平台热销类目端点的枚举证明 + /v1/search-queries/top 为词级
- [searchengines.guru 2024-02](https://searchengines.guru/ru/news/2058379) — "Данные по категориям"是卖家后台网页报告
- [searchengines.guru 2023-09](https://searchengines.guru/ru/news/2057633) — Statistics API 申请制开放给数据服务商
- [wiki.optimism.ru Аналитика Ozon](https://wiki.optimism.ru/%D0%90%D0%BD%D0%B0%D0%BB%D0%B8%D1%82%D0%B8%D0%BA%D0%B0_Ozon) — 自店数据 vs 外部估算综述
- [mcpmarket Ozon Category Product Intelligence](https://mcpmarket.com/tools/skills/ozon-category-product-intelligence) — "sourced through MPSTATS" 直接引用

**Rejected/deprioritized（二手/背景）：**
- docs.ozon.ru 直接文档页 — 本环境 Antibot 403，改用镜像（沿用仓库既定原则）
- skuscan / bhapi / 4eker / kenvara 等卖家工具营销页 — 二手综述，仅背景
- zhangman4706/data_paqu、MrExpen/ITech-Ozon 抓取项目 — 仅作"平台级数据无公开 API"的反证，方案不采纳登录态抓取

## Next steps

1. 执行 PoC-0/1/2（1–2 次真实调用 + 免费台账核对），拿真实扣费分布、字段完整率与 `total` 语义裁决。
2. 通过后实施：`nexscope_client.category_products()`（薄封装）+ `category_products` 表 + 品级闸配置；`keyword_library` 降级为标题/属性优化词源。
3. 用 2 周真实漏斗率（池→闸→货源→经济→图审→上架）校准 §3.5 假设比例，再决定是否扩大到每日多类目池。
