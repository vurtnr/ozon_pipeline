# 研究：用浏览器 Agent 采集 Seerfar 市场页热销 Top200 并接入 ozon-pipeline 的可行性与架构

> 调研日期：2026-11-13。只读研究：未操作浏览器、未绕过任何验证码/反爬、未执行外部写、未修改业务代码。
> 本地一手输入：`docs/research/ozon-seerfar-market-20261113-analysis.md`、`docs/research/ozon-category-product-discovery.md`、`docs/ozon-user-driven-selection-workflow-plan.md`、`docs/research/ozon-user-driven-selection-plan-review.md`、`select_products.py`、`sourcing_agent.py`、`sync_to_db.py`、`pipeline.py`、`nexscope_client.py`。
> 证据分级：**[官方原文]**（直接抓取的一手来源原文）/ **[实测]**（本仓库已有实测记录）/ **[推断]**（researcher 由一手信号推理，非来源原文）。
> 任务前提之一已被主会话实测否定：space 21 访问 `/admin/market` 仍重定向登录页，**不能假设已登录**。

## Summary

**结论：浏览器 Agent DOM 驱动方案不采纳——不是能力问题，而是条款与账号风险问题。** Seerfar 官方用户协议（RU/EN 双语原文一致）明文禁止以 crawlers、scripts、bulk requests、**simulated user behavior**（模拟用户行为）等方式访问平台，未经授权的 scraping/批量收集属"严重违约、可视为对平台安全的攻击"，**可封号且不退款**；用浏览器 Agent 操作已登录页面恰好是"模拟用户行为"的字面情形，且当前会话未登录、订阅费用可能作废。

调研同时发现被任务前提忽略的关键事实：**Seerfar 存在官方 Open API**（`api.seerfar.cn`，文档 v1.1，整理日期 2026-11-10），其中 `POST /open-api/productReport/search/ozon`（Ozon 查热销榜单）就是市场热销数据的官方通道：**不传 `searchDate` 默认即查近 30 天**，支持 categoryIds/价格/加购率/退货率/配送方式等服务端过滤，records 每行自带 **sku、productUrl、imageUrl、标题、品牌、卖家、销量、销售额、评论、类目全路径、crossBorderSellable（是否可跨境）、加购率、退货取消率、订单转化率、浏览量、搜索曝光、广告费用份额**——严格覆盖并超出 20261113 CSV 的 24 列（CSV 无 productId/商品链接）。10 积分/次、限速 3 次/3 秒、免费 quota 端点查余额。架构上应以 **Open API 为主、站内官方 CSV/XLSX 导出（人工点导出、pipeline 只读文件）为兜底**，浏览器 Agent 仅保留为"用户本人人工查看与导出"的界面角色，不做任何自动化操作。

---

## Findings

### 1. 浏览器 Agent 与普通爬虫的真实差异和边界（任务问题 1）

**F1.1 两者在条款面前没有本质区别，浏览器 Agent 反而更明确地落在禁令字面内**
- **Claim:** Seerfar 条款禁止的是行为方式（脚本/批量/模拟用户行为/绕过防护），不区分"渲染浏览器的 Agent"与"requests 脚本"。浏览器 Agent 操作已登录页面 = 条款 5(3) 的 "simulated user behavior" 字面情形；拦截页面 XHR 复用请求 = 条款 5(4) 的接口滥用/绕过技术措施风险。两者都不是官方许可的自动化通道；唯一条款干净的自动化路径是 **Open API Key**（官方鉴权流程本身就校验"数据使用条款确认状态"，即官方把自动化合规收敛到 API + 数据条款同意），其次是站内官方导出按钮。
- **Sources:** [Seerfar RU 条款](https://www.seerfar.ru/terms-of-use)、[Seerfar EN 条款](https://www.seerfar.com/terms-of-use)、[Seerfar Open API 官方文档](http://doc.seerfar.cn/api-docs.config.json)
- **Support:** direct evidence（条款 4(5)(6)、5(2)(3)(4) 原文；API 鉴权流程第 4 步原文"校验客户账号状态、**数据使用条款确认状态**和积分余额"）
- **Confidence:** high

**F1.2 浏览器 Agent 的真实优势与代价（不能宣称"必然更快/更稳"）**
- **Claim:** 真实优势：复用用户登录态免逆向签名、能渲染 JS 重页面、操作过程可截图留证、适合"人在环"确认交互。真实代价：①速度通常**慢于**直接 API 一个量级（每页要渲染、等待、截图）；②稳定性取决于 CSS 选择器/布局，页面改版即断，且 Seerfar 是频繁迭代的商业 SPA（官方有版本更新公告通道）；③登录态脆弱（cookie 失效、验证码、单设备登录互踢）；④每个字段都要从 DOM 文本反解析，无 schema 保障。官方文档、官方导出、页面自身 XHR、DOM 解析，四者的稳定性依次递减。
- **Sources:** 同上 + [Seerfar 版本更新公告示例](https://www.seerfar.cn/articles/%E7%89%88%E6%9C%AC%E6%9B%B4%E6%96%B0-seerfar-v2-5-0%E7%89%88%E6%9C%AC%E6%9B%B4%E6%96%B0%E8%AF%A6%E7%BB%86%E4%BB%8B%E7%BB%8D)
- **Support:** interpretation（工程事实 + 条款文本推论）
- **Confidence:** high

**F1.3 何时 DOM 驱动合适、何时应改道（本项目场景的裁决）**
- **Claim:** DOM 驱动只在"无官方 API、无官方导出、数据只存在于渲染后的 DOM、一次性低频、允许人工逐步确认"时合适。本项目四个条件全部不满足：官方 Open API 已存在且含热销榜单端点；站内导出按钮官方支持（V2.5.0 起热销榜单等 8 个板块可勾选导出 CSV/XLSX）；数据在 API 里有 schema 化字段；需求是月频以上的批量 200 条。**裁决：采集走 Open API，兜底走人工导出 CSV；DOM 自动采集不做。** 另一旁证：Seerfar 官方浏览器插件的做法是把页面可见 SKU POST 到 Seerfar 自家 product-search 端点——连官方自己都走接口而非解析 DOM。
- **Sources:** [官方 API 文档](http://doc.seerfar.cn/api-docs.config.json)、[V2.5.0 更新公告](https://www.seerfar.cn/articles/%E7%89%88%E6%9C%AC%E6%9B%B4%E6%96%B0-seerfar-v2-5-0%E7%89%88%E6%9C%AC%E6%9B%B4%E6%96%B0%E8%AF%A6%E7%BB%86%E4%BB%8B%E7%BB%8D)、[Seerfar 插件安全审查（第三方分析）](https://amibeingpwned.com/extensions/seerfar-analysis-extension-for-ozon-and-wildberries)
- **Support:** direct evidence（端点存在性）+ interpretation（裁决）
- **Confidence:** high

### 2. 拉取 200 条的工程细节（任务问题 2）——按 Open API 路径回答

**F2.1 分页与成本**
- **Claim:** `POST /open-api/productReport/search/ozon`，Bearer API Key 鉴权；body 含 `page{pageNumber, pageSize, orders[{field,direction}]}`，官方示例 `pageSize=20`、`orders=[{field:"revenue",direction:"DESC"}]`；排序标准字段含 price/upTime/sales/revenue/reviewCount/reviewRating。**10 积分/次**，限速单 Key 3 次/3 秒、每端点每平台全局 10 次/秒。200 条 = 2 页（若 pageSize 上限 100）到 10 页（若上限 20）= **20–100 积分**；pageSize 上限文档未写明（未验证，PoC 实测）。
- **Sources:** [api-docs.config.json productReport 端点定义](http://doc.seerfar.cn/api-docs.config.json)、[官方价目表](http://doc.seerfar.cn/api-pricing.example.json)
- **Support:** direct evidence（参数/费率原文）；pageSize 上限 = missing evidence
- **Confidence:** high（参数与单价）/ low（pageSize 上限）

**F2.2 字段字典（响应 records[]，官方 responseFields + responseExample 原文）**
- **Claim:** 每行：`sku`、`title`、`productUrl`、`price`（₽）、`upTime`（上架时间，毫秒时间戳）、`brandId/brandName/brandUrl`、`imageUrl`、`sellerId/sellerName/sellers`（卖家数）、`sales`（销量）、`revenue`（销售额）、`reviewCount/reviewRating`、`categoryInfo{category{id, title(RU)/enTitle/cnTitle, pid, level, disabled, **crossBorderSellable**, children}, fullCategoryId[], titlePath/cnTitlePath/enTitlePath}`、`sessionCountSearch`（搜索曝光）、`drr`（广告费用份额）、`convToCartPdp`（加购率）、`returnCancellationRate`（退货取消率）、`orderConversionRate`（订单转化率）、`sessionCount`（浏览数）。请求侧服务端过滤：`price/monthlySales/monthlyRevenue/reviewCount/reviewRating/convToCartPdp/returnCancellationRate`（min/max）、`creationDate`（上架时间）、`categoryIds`、`fulfillment[]`（配送方式）、`skus[]`、`brand{type,brandName[]}`、`keywords[]`（语义待实测）、`variationsMerge`（0/1 合并变体）、`filterRemoveProduct`、`searchDate`。
- **Sources:** 同 F2.1
- **Support:** direct evidence
- **Confidence:** high
- **与 CSV 对比 [推断]:** 20261113 CSV 为关键词—类目聚合表，缺 productId/URL/单品品牌/评论/上架时长，且"10 张关联图无商品身份"；API records 逐品自带 sku+productUrl+imageUrl+品牌+卖家+类目全路径+跨境标记，**正是 20261113 分析 §6 要求的"商品级表格"字段清单**，另多出加购率/退货率/转化/曝光逐品值（现有管线只有词级聚合值）。

**F2.3 图片与商品 ID 映射、去重**
- **Claim:** 每行 `imageUrl`（ir.ozone.ru 系）与 `sku`/`productUrl` 天然绑定，图片→商品身份映射问题（CSV 的核心缺陷）在 API 路径下不存在。去重以 `sku` 为全局主键：与 `pipeline.db products.ozon_id`、`keywords.top_products` 已消费 ozonId、历史 candidates 求差（沿用 `sync_to_db.py` 的去重闸与"毙过不复活"原则）。
- **Sources:** [api-docs.config.json]；本地 `sync_to_db.py`、`select_products.py`
- **Support:** direct evidence + 本地代码事实
- **Confidence:** high

**F2.4 数据日期窗口**
- **Claim:** `searchDate` 官方原文："日期范围，不传默认查近 30 天数据，示例：传 2026-04-01 查 2026 年 3 月数据，仅能查近 1 年的数据"。即任务要求的"最近 30 天"就是默认窗口；历史只能按自然月回查，最长 1 年。口径警示（沿用既有原则）：Seerfar 销量是第三方**估算**，不是 Ozon 财务真值，只作筛选信号。
- **Sources:** [api-docs.config.json searchDate 参数描述]
- **Support:** direct evidence + 沿用免责原则
- **Confidence:** high

**F2.5 会话失效 / 页面改版 / 超时 / 部分失败 / 重试与幂等**
- **Claim:** ①会话：API Key 是长期凭证，无浏览器会话失效问题；错误码明确（401 鉴权失败、403 账号/权限/授权状态、429 限速、8000 积分耗尽、下游业务码透传如 5001 商品不存在）。②改版风险从"DOM 选择器漂移"转为"官方 API 版本变更"，且有 `api-updates.json` 变更日志通道可订阅核对。③计费规则官方明示（月度趋势端点注）：**HTTP 200 但业务 code≠200 仍扣费；HTTP 非 200 或调用异常退回预扣积分**——重试策略必须区分两种失败：网络失败可安全重试（不扣费），业务失败禁止盲目重试（已扣费），与 `nexscope_client.py` "不重试失败调用防烧额度"的经验一致。④幂等：按 `run_id+页码` 记账（写入现有 `credit_log` 表），重拉前先查账；沿用 `DAILY_CREDIT_CAP` 日熔断模式（现值 330 是 Nexscope 口径，Seerfar 积分需独立配额表）。⑤超时：单请求 timeout 30s（对齐现客户端），失败页整页重试而非逐行。
- **Sources:** [api-docs.config.json 通用响应/错误码/计费 tip]、本地 `nexscope_client.py:13-57`、`sync_to_db.py`
- **Support:** direct evidence + 本地代码事实
- **Confidence:** high
- **[推断] 若强行走 DOM 路径，以上每项各对应一个脆弱点：** 分页=滚动/翻页选择器、字段=列头文本解析、图片=懒加载窗口、改版=即断、会话=cookie 失效+验证码+单设备互踢、重试=无法幂等（无请求级账目）。

### 3. 架构（任务问题 3）

**F3.1 采集到上架的完整链路（researcher 设计，兼容既有用户驱动选品架构）**
```text
用户在计划页填：类目白名单 / 排除类目 / 价格带 / 配送方式(realFBS) / 目标条数(200) / Seerfar 积分预算 / 窗口(近30天)
  → 用户点击启动（不自动跑）
  → seerfar_client 薄封装（新模式对齐 nexscope_client：日熔断 + credit_log 记账 + 只在 Run 内调用）
      POST /open-api/productReport/search/ozon（近30天默认窗口，服务端过滤省积分）
      [免费] GET /open-api/quota 调用前后各查一次 → 差值=真实扣费入账
  → raw snapshot 落盘 data/runs/<run_id>/seerfar-report-<date>.json（请求参数+完整响应+quota 差值，证据可追溯）
  → schema validation（固定字段契约；缺失字段记 null，标"未获取"，不编造——沿用 workflow-plan F3 原则）
  → 规则代码硬过滤：crossBorderSellable / 类目黑名单 / 价格带 / fulfillment（LLM 不碰阈值）
  → sku 去重（对 products.ozon_id、keywords.top_products、已毙行求差）
  → LLM 仅排序+解释（priority/rationale/risks/missing_data，禁止改数值）
  → 人工确认短名单（复用用户驱动选品的人工确认闸）
  → 1688 图搜（sourcing_agent 直接吃 imageUrl，无需改动）
  → 趋势/经济真实闸 → 生图 → 原图审 → 上架
```
- **Sources:** 本地 `select_products.py`/`sourcing_agent.py`/`sync_to_db.py`/`pipeline.py`/`nexscope_client.py` + [workflow-plan](docs/ozon-user-driven-selection-workflow-plan.md) + [plan-review](docs/research/ozon-user-driven-selection-plan-review.md)
- **Support:** interpretation（架构设计），组件均有一手依据
- **Confidence:** high

**F3.2 浏览器凭证与人工登录边界**
- **Claim:** 采纳 Open API 后**不需要**保存 Seerfar 密码/cookie/浏览器会话：API Key（`op_live_xxx` 形态）进 config/keyring（对齐 `NEXSCOPE_API_KEY` 现做法），不入库、不打日志。登录态只存在于用户本人浏览器里，且条款+FAQ 约束：账号须本人手动创建、单账号同时仅一个设备/浏览器登录——**pipeline 若与用户本人浏览器共用登录态还会互踢**。若用户将来坚持浏览器方案，硬边界必须是：用户本人人工完成登录与验证码（agent 不碰凭证、不自动填表、不绕过任何防护）、单任务低频、每步人工确认，并书面确认条款风险自担；**研究结论是建议直接不做**。
- **Sources:** [RU/EN 条款 1/2 条]、[Seerfar FAQ（单设备登录）](https://www.seerfar.cn/articles/seerfar-%E7%94%A8%E6%88%B7%E5%B8%B8%E8%A7%81%E9%97%AE%E9%A2%98faqs)
- **Support:** direct evidence（条款/FAQ）+ interpretation（边界设计）
- **Confidence:** high

**F3.3 Run 级约束沿用既有审核结论**
- **Claim:** 单活动 Run、启动前预算预检（本次估算积分 ≤ min(计划预算, 当日余量)）、独立 Runner 子进程、结构化事件表、刷新只恢复不重跑——全部沿用 `ozon-user-driven-selection-plan-review.md` A1–A8 的裁决，不因数据源换成 Seerfar 而改变。
- **Support:** 沿用既有审核（一手本地文档）
- **Confidence:** high

### 4. 与 Seerfar CSV、Nexscope 的角色分工（任务问题 4）

**F4.1 关键事实：Nexscope 的部分数据就是 Seerfar 数据的转售**
- **Claim:** 前期研究已证实 Nexscope categorySearch 底层来自 Seerfar 分析库（官方 skills 生态注明 "Seerfar Ozon Category Search … from the Seerfar analytics database"，示例响应 `sourceTool: "Seerfar-Ozon-查类目"`）。现在 Seerfar 官方 Open API 直连后，"市场热词/类目/热销榜"这类**同源估算数据存在两条付费通道**：Nexscope categorySearch 16–32 积分/20 行 vs Seerfar 直连 10 积分/次。继续两边买 = 重复购买同一份数据。
- **Sources:** [ozon-category-product-discovery.md F1.7](docs/research/ozon-category-product-discovery.md)、[Nexscope 官方 skill 页](https://www.skills.sh/nexscope-ai/nexscope-ecommerce-skills/ecommerce-ozon-category-search)、[Seerfar 价目表](http://doc.seerfar.cn/api-pricing.example.json)
- **Support:** direct evidence（两处官方标注）
- **Confidence:** high

**F4.2 角色划分建议（避免重复购买）**
- **Claim:** ①**Seerfar Open API = 市场层唯一采买通道**：热销榜单（10/次）、查类目（10/次）、市场热词（10/次）、查竞品（3/次）、月度销量趋势（1/次/SKU）、变体特征（1/次）；评论分析 200 积分/SKU 太贵，不启用。②**Nexscope = 仅保留 Seerfar API 没有对应物的部分**：categoryProducts（MPSTATS 源、按类目全路径拉全平台商品池 top-100）——Seerfar Open API 的"查类目"是类目聚合，没有逐品池端点（PoC 确认）；以及已资产化的 `keyword_library` 存量复用。③**Seerfar 站内 CSV/XLSX 导出 = 零 API 积分的兜底与对账通道**（用户人工点导出，pipeline 只读文件；同时校验 API 字段完整率）。④同一市场问题只允许一个数据源，候选记录写 `source` 字段，`credit_log` 分通道记账，防止同一数据双份付费。⑤另一个未验证点：Open API 积分的**货币成本**未知（Key 申请/购买流程未公开文档化），切换前必须先算总账，未必比 Nexscope 便宜。
- **Sources:** 同 F4.1 + [Seerfar pricing](https://www.seerfar.cn/pricing)
- **Support:** direct evidence（端点/价格）+ interpretation（分工）
- **Confidence:** medium（分工是设计；积分别溢价是 missing evidence）

### 5. 合法性 / 服务条款 / 账号风险（任务问题 5）

**F5.1 官方条款原文（已查到，两域名交叉一致）**
- **Claim:** 已获取并核对 Seerfar 官方用户协议全文（RU 版 seerfar.ru 与 EN 版 seerfar.com，条款逐条对应）：①条款 4(6)：**"任何未经授权的收集（scraping）、批量收集和传播平台数据、模型内容、结果信息等行为，属严重违反本协议，可被视为对平台安全的攻击，我们有权封禁账号且不退款"**（EN："any unauthorized scraping, bulk collection, or dissemination … we have the right to ban the account without refund"）。②条款 5(3)：**承诺不"以爬虫、脚本、批量请求、模拟用户行为或类似方式访问或干扰平台服务"**（EN："crawlers, scripts, bulk requests, simulated user behavior, or similar methods"）——浏览器 Agent 驱动已登录页面正是此条字面情形。③条款 5(4)：不绕过技术保护措施、不使用爬取/注入/接口滥用手段。④条款 4(5)：禁止提交含爬虫的代码。⑤条款 5(2)：未经授权不得将 Seerfar 数据用于转售、竞争分析或展示。⑥条款 1：账号必须本人手动创建、不得使用自动化手段。⑦FAQ：单账号同时仅支持一个设备/浏览器登录。
- **Sources:** [RU 条款全文](https://www.seerfar.ru/terms-of-use)、[EN 条款全文](https://www.seerfar.com/terms-of-use)、[FAQ](https://www.seerfar.cn/articles/seerfar-%E7%94%A8%E6%88%B7%E5%B8%B8%E8%A7%81%E9%97%AE%E9%A2%98faqs)
- **Support:** direct evidence（全文原文抓取）
- **Confidence:** high

**F5.2 合规结论**
- **Claim:** ①自动采集（无论 DOM 还是拦截 XHR）= 违反 ToS，后果是**封号不退款**（付费订阅作废），且叠加当前"未登录"事实，方案从根上不成立。②官方许可的自动化通道 = **Open API**（鉴权流程内建"数据使用条款确认状态"校验）与站内导出按钮。③**未验证项**：seerfar.cn 站点的中文用户协议页面（`/terms-of-use` 404，仅有隐私政策/退款条例），本次以 RU/EN 条款为准；.cn 注册账号适用的具体协议文本未单独核验（RU/EN 条款主体同为 MAAS PVT. LIMITED，[推断] 同一运营方条款同源）。④Open API Key 的申请入口、积分售价、免费额度（quota 响应含 `freeCreditLimitMonth` 字段暗示存在月度免费额度）未在公开文档找到——**不假设允许，需向官方客服确认后才可开通**。
- **Sources:** 同 F5.1 + [api-docs.config.json quota 端点]
- **Support:** direct evidence + 明确标注的未验证项
- **Confidence:** high（禁令）/ low（.cn 适用文本、API 商务条款）

### 6. 最小 PoC 与可验证验收（任务问题 6）——先 20 条再 200 条，不改业务代码

**F6.1 前提门**
- **Claim:** PoC 前用户必须在 Seerfar 后台/客服确认两件事：①Open API Key 的获取入口；②积分余额与购买价格。**若拿不到 Key，PoC 自动降级为 CSV 导出路径**（见 F6.4），浏览器方案不作为备选。
- **Support:** interpretation
- **Confidence:** high

**F6.2 PoC-0（0 积分，只读）**
- 调 `GET /open-api/quota`（官方 0 积分/次）记录 `creditLimit/creditUsed/freeCreditLimitMonth/freeCreditUsedMonth` 基线；拉 `api-updates.json` 记录当前 API 版本。验收：两请求 200 且能解析；产出基线记录。
**F6.3 PoC-1（1 次付费，10 积分：只拉 20 条）**
- `POST /open-api/productReport/search/ozon`：官方示例 body + `categoryIds=[1 个白名单叶类目]` + `searchDate` 不传（默认近 30 天）+ `variationsMerge=1` + `page{pageNumber:1, pageSize:20, orders:[{field:"revenue",direction:"DESC"}]}`。**验收标准（全过才扩量）：**
  1. HTTP 200 且 `code=200`；
  2. `records` 非空，且 ≥80% 行的 `sku/productUrl/imageUrl/sales/price` 非空；
  3. 行的 `categoryInfo.fullCategoryId/titlePath` 与所选类目一致（验证 categoryIds 过滤语义）；
  4. 调用前后 quota 差值 = 10 积分（校准记账）；
  5. 抽 3 行与 Seerfar 网页/近期 CSV 人工对照 sku 与销量口径一致；
  6. 全程只读：不触碰 collect/评论分析等写型或昂贵端点。
**F6.4 PoC-2（可选，2–10 次，20–100 积分：扩到 200 条）**
- 先单页探测 pageSize 上限（20→50→100，一次一档，撞上限即停），再翻页拉满 200 条；验证 `total/totalPages` 翻页语义；输出《API vs CSV 字段差集清单》——与近 30 天官方导出 CSV 同参数对照，逐列核验"比 CSV 全"的差集（重点：productId/productUrl、逐品加购率/退货率、类目路径、跨境标记；同时确认 CSV 是否含商品链接——20261113 版没有，未验证新版导出列）。
**F6.5 共同验收边界**
- 失败处理：业务 code≠200 不重试（已扣费），HTTP 非 200 可重试一次（官方明示退款）；全部产物落 `data/research/` 或 run 目录，**零业务代码改动**（select/source/sync 不动）；验收报告记录积分成本/字段完整率/与 CSV 差集三件事。
- **Sources:** [api-docs.config.json]、[价目表]
- **Support:** interpretation（PoC 设计），参数均一手
- **Confidence:** high

---

## Contradictions

1. **"网页有导出按钮" vs "20261113 CSV 无 productId"：** V2.5.0 更新公告称热销榜单等板块支持勾选导出，教程页面也演示导出；但实际拿到的 Market CSV 是关键词聚合表且无商品 ID/链接。两者不必然矛盾（不同板块导出列不同），但"网页字段比 CSV 全"的具体差集未逐列实测——列为 PoC-2 验收项。
2. **官方 API 文档 baseUrl 为 `http://api.seerfar.cn`（HTTP 非 HTTPS）：** 文档原文如此；是否支持 HTTPS 未验证，实施前必须确认（涉及凭证传输安全）。
3. **条款 5(2) 禁止"未经授权用数据做竞争分析" vs Seerfar 产品本身即竞品分析工具：** [推断] 该条应理解为禁止"转售/对外展示/未授权再分发"，卖家自身内部选品是产品预期用途；此解读无官方释义佐证，若量大或对外分享数据前应向官方确认。
4. **沿用前报告已记录的 Nexscope 计费矛盾**（官方区间 vs 实测），Seerfar 积分记账同样应按价目表上限保守记账、用 quota 差值实测校准。

## Missing evidence

- Open API Key 申请流程、积分货币定价、免费额度政策（公开文档未写；`freeCreditLimitMonth` 字段暗示存在免费额度）。
- `productReport/search/ozon` 的 `pageSize` 上限、`keywords[]` 过滤参数的确切语义、`categoryIds` 的格式（官方指向 Feishu 类目文档 `e1p7i0bkkue.feishu.cn/wiki/R2Nmwih5li76bdkv71ecJkBFnJg`，本次未打开验证）。
- seerfar.cn 中文站适用的用户协议文本（`/terms-of-use` 404）。
- 市场页登录态 DOM 的逐字段清单（未登录无法核验；Open API 字段已覆盖需求，不再追）。
- ego-browser 的本地能力文档未找到（my-mind 仓库为无关项目）；浏览器边界分析基于通用工程事实 + 条款文本，未依赖 ego-browser 内部实现。
- source_check 工具本次未返回任何引文段落（validation limitation）；条款结论的验证依据是直接抓取的 RU/EN 条款全文原文，非二手转述。

## Sources

**Kept（一手/官方）：**
- [Seerfar 用户协议（RU 全文）](https://www.seerfar.ru/terms-of-use) — 4(5)(6)/5(2)(3)(4) 爬取与模拟用户行为禁令、封号不退款条款（决策关键）
- [Seerfar Terms of Service（EN 全文）](https://www.seerfar.com/terms-of-use) — 同款条款英文交叉验证
- [Seerfar Open API 官方文档配置（api-docs.config.json，v1.1，2026-11-10）](http://doc.seerfar.cn/api-docs.config.json) — productReport/search/ozon 端点、searchDate 语义、字段字典、错误码、限速、计费规则
- [Seerfar 官方积分价目表（api-pricing.example.json）](http://doc.seerfar.cn/api-pricing.example.json) — 各端点积分单价与频率
- [Seerfar API 文档站](http://doc.seerfar.cn/api-docs.html?lang=zh-CN) — quota 示例（creditLimit/creditUsed/freeCredit*）
- [Seerfar V2.5.0 版本更新公告](https://www.seerfar.cn/articles/%E7%89%88%E6%9C%AC%E6%9B%B4%E6%96%B0-seerfar-v2-5-0%E7%89%88%E6%9C%AC%E6%9B%B4%E6%96%B0%E8%AF%A6%E7%BB%86%E4%BB%8B%E7%BB%8D) — 热销榜单等板块官方勾选导出功能
- [热销榜单选品教程](https://www.seerfar.cn/articles/product-research-tutorial) — 网页可见字段（售价/销量/销售额/评论/评分/卖家/变体/趋势）
- [Seerfar 套餐价格](https://www.seerfar.cn/pricing) — 订阅档位与每日查询次数
- [Seerfar 用户 FAQ](https://www.seerfar.cn/articles/seerfar-%E7%94%A8%E6%88%B7%E5%B8%B8%E8%A7%81%E9%97%AE%E9%A2%98faqs) — 单设备登录、核心功能、数据来源
- [Seerfar 插件安全审查（amibeingpwned）](https://amibeingpwned.com/extensions/seerfar-analysis-extension-for-ozon-and-wildberries) — 官方插件将页面 SKU POST 到 Seerfar 端点的旁证（SPA 有 JSON 端点）
- 本地一手：`docs/research/ozon-seerfar-market-20261113-analysis.md`（CSV 缺陷清单）、`docs/research/ozon-category-product-discovery.md`（Nexscope=Seerfar 数据源证据 + F1.7）、`docs/ozon-user-driven-selection-workflow-plan.md`、`docs/research/ozon-user-driven-selection-plan-review.md`（Run 架构裁决 A1–A8）、`select_products.py`、`sourcing_agent.py`、`sync_to_db.py`、`pipeline.py`、`nexscope_client.py`（熔断/记账模式）

**Rejected/deprioritized：**
- seerfar.cn/admin/*（market.html、product-search.html 等）— 登录后页面，公网只能看到导航壳，不能作字段证据；且不做登录态采集
- seerfar.cn/terms-of-use — 404，不存在（已记入 Missing evidence）
- tbadc.com 等第三方评测页 — 营销综述，无一手价值
- docs.ozon.ru 及俄语综述 — 与本题无直接增量（沿用前报告结论）

## Next steps

1. **用户动作（决策门）：** 向 Seerfar 客服/后台确认 Open API Key 获取入口、积分定价与免费额度；拿到答复后再决定 PoC 走 API 还是 CSV 导出降级路径。
2. 执行 PoC-0/1（0+10 积分）按 F6.3 六条验收；通过后 PoC-2 扩 200 条并产出字段差集清单。
3. 若 API 通道成立：新增 `seerfar_client.py` 薄封装 + `selection_runs` 内 Seerfar 数据阶段 + 积分独立熔断表——作为独立切片走计划预算预检，不与本次研究混作。
4. 若最终仍想保留浏览器形态：仅作为用户本人手动查看/导出的界面，pipeline 侧永远只读导出文件或 API 响应。
