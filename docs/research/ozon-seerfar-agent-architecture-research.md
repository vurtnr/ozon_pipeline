# 研究：选品逻辑切换 Seerfar Open API + 架构向 AI Agent 形态调整

> 调研日期：2026-11-13（本仓库时间线）。只读研究：未修改任何业务代码，未输入/请求 API Key，未实际调用 api.seerfar.cn。
> 文档站按任务约束用 **HTTP** 读取（`http://doc.seerfar.cn/api-docs.config.json` 与 `api-pricing.example.json` 均直接抓取成功；HTTPS 站存在 Mixed Content/证书问题，未使用）。
> 证据分级：**[一手]**（本轮直接抓取的官方文档原文/直接读到的本地代码）/ **[二手·已审核]**（docs/research 下既有研究）/ **[推断]**（researcher 推理，非来源原文）。
> 注：任务原要求写入仓库 `docs/research/ozon-seerfar-agent-architecture-research.md`；本次运行以会话产物路径为准，父会话可直接复制本文档到该仓库路径。

---

## 0. Summary（直接回答）

1. **数据源切换技术可行且方向已被既有研究裁定**：Seerfar 官方 Open API 的 `POST /open-api/productReport/search/ozon`（10 积分/次，pageSize 最大 100，200 条 ≈ 2 页 ≈ 20 积分）提供商品级热销榜单，字段严格覆盖现有 CSV 缺口（sku/productUrl/imageUrl/品牌/卖家/类目全路径/crossBorderSellable/逐品加购率/退货率/转化率）[一手 + 二手·已审核]。浏览器 Agent 自动采集已被条款审查否决（封号不退款），结论维持：**Open API 为主，站内人工导出 CSV 为兜底**。
2. **现有代码 seam 足以平替"数据源 client"层，但不足以承载"用户驱动 + Run 隔离"的 Agent 形态**：`nexscope_client.py` 的"薄封装 + credit_log 记账 + 日熔断"模式可直接复制为 `seerfar_client.py`；但选品入口 `keyword_library.consume()` 是词级形状（词→top3 爆款），Seerfar 商品池是品级形状（records[].sku+imageUrl），需新增品级选品模块；且 `selection_runs` Run 隔离、独立 Runner 子进程、预算预检均**尚未实施**（`selection_runner.py` 不存在）[一手]。
3. **改造范围初步估计**：新增 3 个文件（seerfar_client / selection_runner / 品级选品模块）+ 修改 5–6 个文件（config、select/sync、trend_check、review_server、pipeline 迁移）+ 1 组新表；sourcing/pricing/image/listing/content 全部不动。工作量集中在 Run 生命周期与真实闸门（既有审核评级 B/D/E = 高）[推断，依据二手·已审核 A1–A8]。

---

## 1. 事实来源层级（本报告的证据结构）

| 层级 | 来源 | 状态 |
|---|---|---|
| 一手·官方 | `http://doc.seerfar.cn/api-docs.config.json`（v1.1，整理日期 2026-11-10，约 1.1MB） | 本轮直接抓取全文 [一手] |
| 一手·官方 | `http://doc.seerfar.cn/api-pricing.example.json`（全部端点积分单价+频率） | 本轮直接抓取全文 [一手] |
| 二手·已审核 | `docs/research/ozon-seerfar-browser-agent.md`、`ozon-category-product-discovery.md`、`ozon-seerfar-market-20261113-analysis.md`、`ozon-user-driven-selection-plan-review.md`、`docs/ozon-user-driven-selection-workflow-plan.md` | 本轮全部复读，关键结论与一手文档交叉核对一致 [一手核对] |
| 一手·本地 | `pipeline.py`、`config.py`、`nexscope_client.py`、`keyword_library.py`、`select_products.py`、`sourcing_agent.py`、`sync_to_db.py`、`trend_check.py`、`pricing.py`、`review_server.py`、`content_agent.py`、`image_worker.py`、`listing_worker.py`、`listing_edit.py`、`ozon_client.py`、`category_tree.py`、`tests/test_pricing.py`、`tests/test_listing_worker.py`、`ROADMAP.md` | 本轮直接读取源码 [一手] |

合规边界重申（沿用 `ozon-seerfar-browser-agent.md` F5.1，一手抓取过 RU/EN 条款全文）：任何 DOM/登录态自动采集均违反 Seerfar 条款（可封号不退款）；唯一干净的自动化通道是 Open API（鉴权流程内建"数据使用条款确认状态"校验 [一手：api-docs.config.json auth 节]）。

---

## 2. Seerfar Open API 一手事实（本轮直接抓取）

### 2.1 基础与鉴权
- Base URL：`http://api.seerfar.cn`（文档原文即 HTTP；是否支持 HTTPS 未验证）[一手：api-docs.config.json base-info]。**API Key 走 HTTP 传输是安全隐患，接入前必须先确认 HTTPS 可用** [推断]。
- 鉴权：全部接口 `Authorization: Bearer <platform_api_key>` + `Content-Type: application/json` [一手：auth 节]。
- 鉴权链（原文顺序）：①校验 Authorization 头格式（401）→ ②API Key 哈希匹配定位 api_key_id/account_id（401）→ ③Key 状态须 active（401）→ ④校验**客户账号状态、数据使用条款确认状态和积分余额**（403 或 8000）→ ⑤Key 级+账号级限流（429）→ ⑥按端点所需能力校验 Ozon/WB 授权状态与 Token 类别（403）[一手：auth 节表格]。
- 通用响应：`{code, msg, data}`；错误码 401/403/429/500/**8000 Api Key limit exhausted** [一手：response 节]。

### 2.2 计费与限速（决策关键，原文引用）
- 计费规则 tip（原文）：**「响应透传下游业务状态，请同时检查 HTTP 状态和 code。当前版本按 HTTP 状态处理扣费：HTTP 200 且 code 非 200 的业务失败仍会扣除 1 积分；HTTP 非 200 或调用异常时退回预扣积分。」** [一手：ozon-product-monthly-sales-trend 端点 tip]
  → 工程含义：网络失败可安全重试（退积分），业务失败禁止盲目重试（已扣费）——与 `nexscope_client.py` "失败调用不重试防烧额度"经验一致，重试策略必须区分两种失败 [推断]。
- 限速 tip（原文）：**「单 API Key 限制为 3 次/3 秒，每个平台接口的全局限制为 10 次/秒，两个平台分别限流。」** [一手：同端点 tip]
- 积分查询：`GET /open-api/quota`，**0 积分/次**（1次/3秒）；返回 `creditLimit`（付费积分总量）、`creditUsed`（已用）、`freeCreditLimitMonth`/`freeCreditUsedMonth`——**后两个字段文档已标注「已弃用」，示例值为 0** [一手：quota 端点 responseFields]。

### 2.3 全端点积分价目表 [一手：api-pricing.example.json]

| 端点（Ozon 侧） | 积分 | 频率 | 选品用途 |
|---|---|---|---|
| 查询 API Key 积分 `/open-api/quota` | 0/次 | 1次/3秒 | 记账校准（调用前后差值=真实扣费） |
| **查热销榜单 `/open-api/productReport/search/ozon`** | **10/次** | 3次/3秒 | **商品池主入口（本轮核心）** |
| 查市场热词 `/open-api/market/search/ozon` | 10/次 | 3次/3秒 | 词级需求发现（替代/补充 Nexscope 词库） |
| 查类目 `/open-api/category/search/ozon`* | 10/次 | 3次/3秒 | 类目聚合与类目 id 查询 |
| 查竞品（product detail） | 3/次 | 3次/3秒 | 短名单单品复查 |
| 查相似商品 | 15/次 | 3次/3秒 | 扩展同款池 |
| 查变体特征 / Top 变体 / 商品变体 | 1 / 3 / 3 每次 | 3次/3秒 | 变体与属性结构 |
| **月度销量趋势 `/open-api/product/monthlySalesTrend/ozon`** | 1/次/SKU | 3次/3秒 | 短名单品趋势复查（替代逐词 growth） |
| 查店铺 `/open-api/shop/detail/search/ozon` | 10/次 | 3次/3秒 | 店铺级商品池+汇总（对标卖家/选店铺） |
| 关键词挖掘 / 关键词反查 | 15/次 | 3次/3秒 | 词库建库备选通道 |
| 采集商品 / 查询采集商品 | 3/SKU、3/次 | 3次/3秒 | 异步任务式采集（响应含 status/failType/expireAt） |
| 评论分析：创建任务 200/SKU；报表/词频/统计/原始评论各 1/次 | — | 3次/3秒 | 过贵，沿用既有结论不启用 [二手·已审核 F4.2] |

\* 查类目路径以端点 id `ozon-category-search` 为准（文档 endpoints 数组内含确切 path，本轮未逐字展开该端点）。
WB 侧同构端点价格一致（各 10/15/3/1 档）[一手]。

### 2.4 `productReport/search/ozon` 完整规格 [一手：api-docs.config.json ozon-product-report-search 端点]

**请求**：`POST`，描述「按筛选条件查询 Ozon 热销榜单商品数据」。

**bodyParams 全量**（除 page 外全部可选）：
- 数值区间过滤（各含 `min`/`max`）：`reviewCount`(评论数)、`drr`(广告费用份额)、`reviewRating`(评分)、`questionsAndAnswers`(qa)、`price`(价格₽)、`monthlyRevenue`(销售额)、`monthlySales`(销量)、`monthlySalesRate`(销量增长率)、`weight`(**重量g**)、`volume`(**体积L**)、`grossMargin`(毛利率)、`variants`(变体数)、`convToCartPdp`(加购率)、`returnCancellationRate`(退货取消率)；
- `creationDate`：上架时间分档 null不筛/1近30天/3近90天/6近180天/12近一年/24近2年；
- `categoryIds[]`：类目 id 数组；`fulfillment[]`：OZON/FBO/FBS/RFBS/FBP；`skus[]`：最多 10 个；`sellerName[]`：卖家名称；
- `brand{type, brandName[]}`：0包含/1排除/2无品牌；`labels[]`：0新品/1正品/2畅销品；`keywords[]`：关键词（确切语义未验证）；`variationsMerge`：0不合并/1合并变体；`searchDate`：**「日期范围，不传默认查近30天数据，示例：传2026-04-01查2026年3月数据，仅能查近1年的数据」**；`tag`：标签词；
- **`filterRemoveProduct`：出现在 bodyExample 与 curl 示例中（true），但未在 bodyParams 里声明**（文档内部不一致，实测前按可选布尔处理）[一手观察]；
- `page`（必填）：`pageNumber`、`pageSize`（**「分页每页条数，最大100」**）、`orders[{field, direction(DESC/ASC)}]`；
- **排序字段全集**：revenue, price, sales, grossMargin, views, sessionCountSearch, sessionCount, convToCartPdp, orderConversionRate, drr, returnCancellationRate, missedRevenue, variants, reviewCount, reviewRating, upTime, weight, volume, questionsAndAnswers。

**响应 `data.records[]` 字段全量**（responseFields 原文）：
`sku`, `title`, `productUrl`, `brandId/brandName/brandUrl`, `imageUrl`, `sellerId/sellerName`, `sellers`(已弃用), `fulfillment[]`, `weight`, `questionsAndAnswers`, `upTime`(毫秒时间戳), `upDays`, `upMonths`, `price`, `grossMargin`, `reviewCount`, `reviewRating`, `reviewIncr`(已弃用), `sales`, `salesRate`, `revenue`, `revenueRate`, `categoryInfo{category{id, enTitle, title, cnTitle, pid, level, disabled, **crossBorderSellable**, children}, fullCategoryId[], titlePath, cnTitlePath, enTitlePath}`, `volume`, `dimension`(毫米), `labels[]`, `variants`, `variationIds[]`, `variationsRelationId`, `sellerType`(0本土/1跨境), `sessionCount`(产品卡浏览量), `sessionCountSearch`(曝光量), `drr`(广告费用), `convToCartSearch`(搜索加购率), `discount`(平均折扣), `promoRevenueShare`(促销销售占比), `convViewToOrder`(展示转化率), `convToCartPdp`(加购率), `returnCancellationRate`(退货取消率), `orderConversionRate`(订单转化率), `views`(展示总量), `missedRevenue`(错失销售额)。

**分页字段**：`data.total` =「符合筛选条件的商品数」（唯一有效翻页依据）；`size/current/currentSize/totalPages/orders/timeInterval` 在 responseFields 中**全部标注「已弃用」**（示例响应仍带值：total=27,744,276 与 totalPages 相等）。→ 翻页按 `ceil(total/pageSize)` 计算，弃用字段不依赖；语义需 PoC 实测裁决 [推断，基于一手标注]。

**成本换算 [推断，算术推导]**：200 条 = pageSize 100 × 2 页 = 2 次 × 10 积分 = **20 积分**；服务端过滤（价格带/类目/fulfillment/creationDate）不额外计费。

### 2.5 与选品相关的其他端点补充
- `ozon-shop-search`（查店铺，10/次）：按店铺 id 查近 30 天商品及汇总，商品按上架时间倒序；`productCount/rating/totalRevenue/totalSales/dailySales/fulfillment` **仅 pageNumber=1 返回**；过滤参数含 weight/volume/grossMargin/convToCartPdp 等同款区间 + labels + pageSize 最大 100 [一手：端点定义与 bodyExample]。
- `ozon-product-monthly-sales-trend`（1/次/SKU）：按 SKU 查月度销量/销售额数组；sku 必填 >0；null=暂无数据 [一手]。
- 采集类端点是**异步任务模型**（提交→查询任务，响应含 `status/failType/openApiRequestId/createTime/expireAt`）[一手：collect-box-page 响应示例]，与榜单/搜索的同步模型不同，接入时不可复用同一调用模板 [推断]。

---

## 3. 本地既有研究关键结论（二手·已审核，本轮复读核对）

以下结论本轮已与其引用的一手文档交叉核对，继续有效：

1. **浏览器 Agent 采集否决**：条款 4(6)/5(2)(3)(4) 禁止 scraping/批量/模拟用户行为，违者封号不退款；官方许可通道只有 Open API 与站内人工导出（`docs/research/ozon-seerfar-browser-agent.md` F1/F5，来源为 seerfar.ru/.com 条款全文）。
2. **API 字段覆盖 CSV 缺口**：20261113 CSV 是关键词—类目聚合表（无 productId/URL/单品指标，图片无法映射商品身份）；`productReport/search/ozon` records 逐品自带 sku/productUrl/imageUrl + 类目全路径 + 跨境标记，正是该分析 §6 要求的商品级导出契约（`ozon-seerfar-market-20261113-analysis.md` §1.2/§6 + browser-agent F2.2）。本轮一手字段清单再次确认（含新增可见的 weight/volume/dimension 包裹尺寸字段，利于"小件"硬过滤 [推断]）。
3. **数据口径**：Seerfar 销量是第三方估算，非 Ozon 财务真值，只作筛选信号（market 分析 §1.3；category-product-discovery F1.7）。
4. **Nexscope 与 Seerfar 数据同源**：Nexscope categorySearch 底层即 Seerfar 分析库（skills.sh 官方标注 + 示例 `sourceTool: "Seerfar-Ozon-查类目"`，category-product-discovery F1.7）→ 切 Seerfar 直连后，市场层数据应**单一采买通道**，Nexscope 只保留 Seerfar 无对应物者（categoryProducts 的 MPSTATS 全品池、已资产化词库存量），防同一份数据双份付费（browser-agent F4.1/F4.2）。
5. **成本参考**：Seerfar 榜单 10/次（一手价目表）vs Nexscope categorySearch 16–32/20 行——同源数据下 Seerfar 直连每行成本更低；**但 Seerfar 积分的货币定价、Key 申请入口、免费额度政策公开文档未载**（browser-agent Missing evidence，本轮一手复查：quota 响应的 freeCredit 字段已弃用且示例 0，免费额度假设进一步弱化，见 §6 矛盾 1）。
6. **Run 架构裁决 A1–A8 全部沿用**（`ozon-user-driven-selection-plan-review.md`）：Run≠Product 状态、Run 目录隔离、独立子进程 Runner、单活动 Run、预算预检、词/候选不永久烧毁、trend/econ 闸先变真、on_sale 为派生结果。数据源换成 Seerfar 不改变这些裁决 [二手·已审核，本轮确认代码现状与之相符，见 §4]。

---

## 4. 当前代码架构扫描 [一手：逐文件读取]

### 4.1 模块与 pipeline 阶段
```text
发现/选品      keyword_library.py (建库/刷新/消费词) ← nexscope_client.py (marketKeywordSearch)
              select_products.py (pop 词 → data/candidates-YYYYMMDD.json + image-search-tasks-YYYYMMDD.json)
找货          sourcing_agent.py (1688 图搜 skill vendor/1688-product-find；VL 识别 + 同一性闸)
趋势/经济     trend_check.py (词级重查告警；trend/econ worker 主闸未实现——sync_to_db.py 明示跳过)
              pricing.py (确定性定价 calc，佣金档迭代+物流档)
内容/生图     content_agent.py (LLM 出 spec JSON，类目机械查表) → image_worker.py (qwen-image-3.0 8-slot)
审核/上架     review_server.py (stdlib ThreadingHTTPServer：/board /review /product/<id>，POST /action)
              listing_edit.py (人工编辑写回 spec listing_edits) → listing_worker.py (OSS+import, offer_id=ozp-{pid})
底层          pipeline.py (SQLite 状态机 + 6 表), ozon_client.py (类目属性 3 方法), category_tree.py (7,363 叶类目)
```
- 状态机：`discovered → scored → sourced → checked → profitable → imaging → review_pending → approved → listed → syncing → winning → videoing → done`，任何态可 `dead`；`TRANSITIONS` 白名单强制 [一手：pipeline.py:14-44]。
- 六表：products / events / reviews / keywords / credit_log / harvested_seeds [一手：pipeline.py SCHEMA]。

### 4.2 数据源 client 的 seam 形状（平替评估的基准）
`nexscope_client.py` [一手:13-57]：
- `CREDITS = {endpoint: 单价}` + `DAILY_CREDIT_CAP = 330`（10,000/30天）；
- `spent_today()` = SUM(credit_log 今日)；`call()`: **预检（超熔断抛 CreditFuseError）→ POST → code!=0 抛错（失败不记账不重试）→ 成功后按估算值写 credit_log**；
- 记账是估算不是账单（Nexscope 不回传扣费明细）；"检查→调用→记账"非原子（多活动 Run 有竞态——plan-review A5 已识别）。
- **这就是 Seerfar client 的现成模板**：`seerfar_client.py` 可完全复刻该形状，且 Seerfar 有官方 `GET /open-api/quota`（0 积分）可做**调用前后差值实测校准**，比 Nexscope 纯估算更强 [推断]。

### 4.3 选品入口 seam 与"品级池"的形状差
- 现入口：`keyword_library.consume(n)` 返回**词级行**（月搜/竞争/均价/退货率/top3 爆款），`select_products.py` 组装 candidates + 图搜任务 `{query, imageUrl, ozonId}` [一手：keyword_library.py consume / select_products.py main]。
- Seerfar 池是**品级行**（records[].sku/title/productUrl/imageUrl/逐品指标/类目路径）。词级漏斗阈值（MIN_SEARCH_VOLUME 等 5 项，keyword_library.py:33-38）对品级行不适用，需品级闸替代（rating/reviewCount/salesPerDay/upDays/集中度等，映射方案已在 category-product-discovery §3.2 设计）[一手 + 二手设计]。
- **下游 worker 与数据源解耦良好**：`sourcing_agent` 只吃 `{query, imageUrl, ozonId}` 任务；`sync_to_db` 去重闸以 `(query, ozon_id)` + ozon_id 全局查重 + "毙过不复活"；`records.sku` → `ozonId`、`records.imageUrl` 直接喂图搜即可 [一手 + 推断（映射）]。

### 4.4 LLM 调用点（与数据源切换正交）
| 调用点 | 模型 | 用途 | 文件 |
|---|---|---|---|
| sourcing_agent.vl_describe / vl_same_product | qwen3-vl-plus（BAILIAN） | 图识别采购关键词 / 同一性对拍（最多 8 次/品） | sourcing_agent.py |
| content_agent 主流程 | BAILIAN chat | spec JSON（标题/描述/卖点），类目不让 LLM 猜 | content_agent.py |
| image_worker 生成 + VL 文字检测 | qwen-image-3.0 / qwen3-vl-plus | 8-slot 套图 + 偷画文字闸 | image_worker.py |
- 选品逻辑里**当前没有 LLM 参与数值判断**（漏斗阈值全是代码常量）——符合 workflow-plan"LLM 只排序解释、不碰阈值"原则；切 Seerfar 后此边界保持 [一手代码 + 二手契约]。

### 4.5 Run 隔离与成本预检现状
- **无 Run 模型**：`selection_runner.py` 不存在（本轮探测确认）；artifacts 是全局日期文件 `data/candidates-YYYYMMDD.json` 等，同日重跑覆盖/串读（plan-review A2 判定）；`sourcing_agent.main()` 取 `sorted(glob)[-1]` 最新文件 [一手]。
- **无计划预算预检**：预算控制只有全局日熔断 330；`credit_log` 不分通道（endpoint 字段可区分，但 `spent_today()` 混算 Nexscope/Seerfar）[一手 nexscope_client.py:26-30]。
- trend/econ 闸缺失：sync_to_db.py 明示「trend worker 未实现/econ worker 未实现」即推进 checked/profitable [一手 sync_to_db.py to("checked"...) 段]。
- `trend_check.py` 现存 bug：把 5 词列表传给单词接口 `market_keyword_search`（该函数签名 `keyword: str`），与"一词一调"实测约定冲突——plan-review §4 已列「修一词一查」为必改项 [一手代码 + 二手审核]。

### 4.6 配置与 .env 约定
- `config.py` 启动即读 `.env`（`os.environ.setdefault`，不进 git），**缺 key 直接 KeyError fail-fast**；现有键：BAILIAN_API_KEY/BAILIAN_BASE_URL/OZON_CLIENT_ID/OZON_API_KEY/NEXSCOPE_API_KEY/OSS_ACCESS_KEY_ID/SECRET/BUCKET/ENDPOINT [一手]。
- 无 `.env.example`（本轮探测确认）；接入 Seerfar 需新增 `SEERFAR_API_KEY`（沿用 NEXSCOPE_API_KEY 的不入库/不打日志约定 [二手 F3.2]）。

### 4.7 测试布局
- `tests/` + pytest；本轮确认存在：`tests/test_pricing.py`（毛子ERP 对拍）、`tests/test_listing_worker.py`（import payload 纯函数）；`tests/test_select_products.py` 不存在 [一手探测]。
- 模式：纯函数测试、不调外部 API（Mock 外部写）——新 client/池模块应沿用（如 seerfar body 组装、翻页计算、熔断记账的 assert 式自检）[一手 + 推断]。

---

## 5. 要接入 Seerfar 商品池需要改动的具体清单 [推断：基于 §2 一手 API + §4 一手代码 + 二手 A1–A8 裁决]

### 新增
| 文件/对象 | 内容 | 对齐的现有模式 |
|---|---|---|
| `seerfar_client.py` | 薄封装：`SEERFAR_CREDITS`（榜单10/热词10/类目10/竞品3/趋势1…）、**独立日熔断通道**（不与 Nexscope 330 混算；credit_log 复用但按 endpoint 前缀分通道统计）、`call()`=预检→POST→code!=0 抛错不重试（业务失败已扣费）→HTTP 非 200 可安全重试一次（官方退积分）→成功写 credit_log；`quota()` 免费查余额，调用前后差值=真实扣费回填 | `nexscope_client.py` call()/spent_today() 全套 [一手模板] |
| 品级选品模块（`select_from_pool.py` 或 select_products.py 新模式） | `productReport/search/ozon` 拉池：计划参数（类目白名单/价格带/fulfillment=RFBS/creationDate/排序 revenue DESC/pageSize 100）→ 硬过滤（crossBorderSellable/sellerType=1 跨境/类目黑名单/小件 weight·volume）→ sku 去重（对 products.ozon_id、keywords.top_products、已毙行求差）→ 输出 run 内 candidates + 图搜任务（sku→ozonId 映射） | select_products.py 输出契约；sync 去重闸语义 |
| `selection_runner.py` + `selection_plans/selection_runs/selection_events` 表 | 计划快照、单活动 Run、子进程隔离、结构化事件、预算预检（本次估算积分 ≤ min(计划预算, 当日分通道余量)） | plan-review §3 最小架构（已审核裁决） |
| `data/runs/<run_id>/` 目录约定 | candidates/sourcing/交接 artifacts；全局日期文件降级为旧路径兼容 | plan-review A2 |
| `tests/test_seerfar_client.py` 等 | body 组装、翻页计算（ceil(total/pageSize)，不依赖已弃用 totalPages）、熔断记账、失败重试分类 | tests/ 纯函数 Mock 风格 |

### 修改
| 文件 | 改动 | 依据 |
|---|---|---|
| `config.py` | +`SEERFAR_API_KEY` | 现有 fail-fast .env 模式 |
| `sync_to_db.py` | 接受显式 Run 目录（不再 glob 最新）；品级候选的 metrics 写入；保留旧兼容与去重原则 | plan-review A2/A4 + 一手 latest() 现状 |
| `sourcing_agent.py` | main() 接受显式任务文件参数（现硬编码 `sorted(glob)[-1]`）；核心算法不动 | 一手 main() + A4 |
| `trend_check.py` | 先修一词一调 bug；趋势复查可换 `monthlySalesTrend`（1/次/SKU）做品级趋势 | 一手 bug + 二手 §3.4 |
| `pipeline.py` | `_migrate` 增量加 3 张 selection 表（新库走 SCHEMA 全量） | 一手 _migrate 模式 |
| `review_server.py` | 仅当实施用户驱动计划页时：新计划/运行页面 + 启动/查询路由；原图审逻辑不动 | workflow-plan F1/F2 + plan-review §4 |

### 不动
`sourcing skill (vendor/1688-product-find)`、`pricing.py`、`image_worker.py`、`listing_worker.py`、`listing_edit.py`、`ozon_client.py`、`content_agent.py`（LLM 生成端与数据源无耦合）。

### 实施顺序（沿用切片裁决）
数据源平替最小切片 = seerfar_client + 品级选品 + Run 目录（先 CLI 形态跑通 20 积分 PoC）；计划页/可视化/on_sale 是后续 Slice A–F，与数据源切换解耦 [推断，依据 workflow-plan §8 依赖序]。

---

## 6. Seam 判断与矛盾/缺口

### 6.1 「现有 seam 是否足以平替数据源」的裁决 [推断]
- **client 层：足以平替。** `nexscope_client` 的调用/记账/熔断形状可直接复制；Seerfar 还多出免费 quota 校准通道。风险点仅两个：baseUrl 为 HTTP（安全）与 Key/定价商务信息缺失（见 6.2）。
- **选品入口层：形状不匹配，需要新增品级模块，但不是重构。** 词级漏斗（keyword_library）与品级池（productReport）是两种输入契约；好在下游（sourcing→sync→image→listing）只消费 `{query, imageUrl, ozonId}` 与 spec/assets，全部与数据源解耦——切源改动收敛在"发现层"。
- **Agent 形态层：seam 不足。** Run 隔离、事件流、计划快照、单活动 Run、预算预检当前**均不存在**，必须按已审核的 A1–A8 新建；这是本改造的主要工作量（B/D/E 高成本切片），不是数据源本身。

### 6.2 矛盾与文档不一致（记录，不静默解决）
1. **免费额度推断过时**：旧研究由 `freeCreditLimitMonth` 字段推断"可能存在月度免费额度"（browser-agent F5.2）；本轮一手 responseFields 显示该字段及 `freeCreditUsedMonth` **已标注「已弃用」**且示例 0 → 免费额度假设进一步弱化，预算模型不应计入免费积分。
2. **分页字段被弃用**：`size/current/currentSize/totalPages/orders/timeInterval` 在 responseFields 标注「已弃用」，但示例响应仍带值（total=totalPages=27,744,276）；旧研究建议按 total/totalPages 翻页（F2.1）。→ 以 `data.total`（「符合筛选条件的商品数」）自行计算页数，弃用字段不依赖；PoC 实测裁决。
3. **`filterRemoveProduct` 未声明却出现在示例**：productReport 的 bodyParams 未列该参数，bodyExample/curl 却带 `"filterRemoveProduct": true`（查店铺端点同样）→ 按可选布尔处理，PoC 验证语义。
4. **baseUrl HTTP**：文档全文只有 `http://api.seerfar.cn`；API Key 明文走 HTTP 的风险未解决（旧研究 Contradictions #2，本轮复核仍未有 HTTPS 说明）。
5. **同源数据双通道**：Nexscope categorySearch（16–32 积分/20 行）与 Seerfar 查类目（10/次）同源；切换后若两通道并存必须分通道记账并"同一市场问题单一数据源"（browser-agent F4.2 裁决沿用）。

### 6.3 Missing evidence（未验证/未公开）
- Seerfar API Key 申请入口、积分货币定价、付费方式（公开文档无；实施前须向官方确认——这是 PoC 前的用户决策门）。
- HTTPS 是否可用（涉及凭证传输安全）。
- `pageSize=100` 为文档声明值，未实测（旧研究记录 pageSize 上限未知，本轮一手文档已写「最大100」，但服务端行为未验证）。
- `keywords[]` 在 productReport 中的确切语义（词过滤 or 语义搜索）、`categoryIds` 的 id 来源（文档指向 Feishu 类目表，未打开验证）。
- `searchDate` 按自然月回查与"近30天默认"的边界行为（如月初/月末）未实测。
- `selection_plans` 等表、Run 目录、计划页：规划文档齐备但零代码，实施前需按 plan-review 做"选品入口拆阶段"的代码级 Spike（workflow-plan §11.1）。

---

## 7. Sources

**Kept（一手·本轮直接抓取/读取）：**
- Seerfar Open API 文档配置 `http://doc.seerfar.cn/api-docs.config.json` — 鉴权/通用响应/错误码/计费与限速 tip/全部端点定义；productReport/search/ozon 完整参数与字段字典（本文 §2 的唯一来源）
- Seerfar 官方价目表 `http://doc.seerfar.cn/api-pricing.example.json` — 全端点积分单价与调用频率
- 本地代码：`pipeline.py`、`config.py`、`nexscope_client.py`、`keyword_library.py`、`select_products.py`、`sourcing_agent.py`、`sync_to_db.py`、`trend_check.py`、`pricing.py`、`review_server.py`、`content_agent.py`、`image_worker.py`、`listing_worker.py`、`listing_edit.py`、`ozon_client.py`、`category_tree.py`、`tests/test_pricing.py`、`tests/test_listing_worker.py`、`ROADMAP.md`
- 本地既有研究（二手·已审核，复读核对）：`docs/research/ozon-seerfar-browser-agent.md`、`docs/research/ozon-category-product-discovery.md`、`docs/research/ozon-seerfar-market-20261113-analysis.md`、`docs/research/ozon-user-driven-selection-plan-review.md`、`docs/ozon-user-driven-selection-workflow-plan.md`

**Rejected/deprioritized：**
- HTTPS 版文档站（doc.seerfar.cn https）— 按任务约束与实测存在 Mixed Content/证书问题，不用
- `api.seerfar.cn` 实际调用 — 任务明令禁止（只读文档，不调 API、不碰 Key）
- 旧研究引用的 seerfar.ru/.com 条款全文 — 本轮未重复抓取，结论沿用（已审核二手，条款文本此前为一手抓取）

## 8. Next steps
1. 用户决策门：向 Seerfar 确认 API Key 获取入口、积分定价、HTTPS 可用性；拿不到 Key 则降级走站内人工导出 CSV 路径（不做浏览器自动化）。
2. PoC（0+10 积分）：quota 基线 → 单页 pageSize=100 榜单调用 → 六项验收（code=200、字段完整率、categoryIds 语义、quota 差值=10、与 CSV 对照、只读边界）——沿用 browser-agent F6 验收设计。
3. 通过后按 §5 清单实施最小切片（seerfar_client + 品级选品 + Run 目录），计划页/RUN UI 按既有 Slice A–F 另行排期。
