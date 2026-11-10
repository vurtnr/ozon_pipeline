# Research: Nexscope 余额耗尽后的 Ozon 热词/选品免费替代

## 结论（按优先级）
1. **Seerfar Open API 是最优替代（近乎 drop-in）**：官方文档 `POST /open-api/market/search/ozon`（Ozon 市场热词）返回字段与我们 Nexscope marketKeywordSearch **完全同名**：`searchVolume`、`count30GrowthRate`、`competingProducts`、`avgPrice`、`returnCancellationRate`（≈退货率），且每条记录带 `products[]`（top10：title/sku/ozonId/imageUrl）→ 可直接喂 1688 图搜。用户已有 Seerfar 账户，主要待确认项是 API Key 开通与积分计费。
2. **过渡方案：Seerfar 网页端导出 Excel 做热词库** — 官方支持导出，字段与 API 同源，但按套餐限次。
3. **Ozon 官方"Аналитика запросов"（卖家后台 免费/按订阅）** 可做交叉验证词库，但按 2026-10 官方帮助更新，完整访问已挂到 Premium 订阅下，且无公开 API 输出市场级热词。
4. MPStats / Moneyplace / Маяк **均无真正免费的关键词量级数据层**（只有免费浏览器插件/1天演示）→ 排除。

## Seerfar 能力清单（(a)能否用 Seerfar 导出热销产品 Excel 做热词库 → 能）
| 项 | 结论 | 证据 |
|---|---|---|
| Ozon 覆盖 | ✅ 核心平台（Ozon+WB） | seerfar.com 首页、FAQ |
| 关键词分析 | 关键词挖掘/关键词反查/市场热词/关键词词库；指标：月搜热度、月搜索增长、竞品数、竞对数、平均价格、加购转化率、标题密度、相关度 | 官方关键词挖掘教程 |
| Excel 导出 | ✅ V2.5.0 起勾选导出支持：热销榜单、市场热词、关键词挖掘、关键词反查、产品库、关键词库；教程明确"可导出为Excel表格" | 官方 V2.5.0 更新页 |
| API | ✅ 官方 Seerfar Open API，base `http://api.seerfar.cn`，Bearer Key，积分(credit)计费，错误码 8000=积分耗尽；含 market/search/ozon、keyword/mining/ozon、productReport/search/ozon、keyword/back-search、product/collect 等 | doc.seerfar.cn/api-docs.config.json（官方文档源文件，直接取回并逐字段核对） |
| 免费/付费 | Free $0/月（各模块 Limited Access，Data Export Limited）；Starter $59/月（查询/导出 100、50次/日）；Professional $79；Enterprise $159 无限查询（导出 200/日）；新用户 3–5 天全功能试用 | seerfar.com/pricing、FAQ |
| 热销产品图片 | 关键词挖掘页可看"关键词相关产品（前10条）"Ozon 前台图；API `products[].imageUrl` 直给 | 教程 + API 文档 |

⚠️ 待办：API 接口单价表为文档内远程占位（`api-pricing.example.json` 未取回具体数值）；API Key 是否随现有网页账户开通、是否需另购积分 → 需咨询 Seerfar 客服。

## Ozon 官方免费数据源
| 来源 | 内容/字段 | 访问 |
|---|---|---|
| 卖家后台 Аналитика → "Что продавать на Ozon"/"Поисковые запросы" | 平台 top~1000 买家搜索词：запрос 热度(популярность)、竞品的数量、加购转化率、平均价格；日更 | 免费展示部分指标；2026-10-28 官方帮助更新后完整访问标注 Premium Pro/Premium Plus；Excel 下载亦在订阅门槛内 |
| Seller API `POST /v1/analytics/product-queries`（getProductQueries，及 …Details） | 自己商品被搜索的查询词+次数/排名/转化；按 SKU ≤1000 | 非订阅仅部分指标，完整需 Premium/Premium Plus；限速约 1次/分。**注意：只覆盖自家商品流量词，不是市场热词榜** |

验证限制：本网络无法解析 seller-edu.ozon.ru / docs.ozon.ru（DNS 失败），上表依据官方文档镜像与转述官方帮助的 2026-10 记录，置信度中。

## 字段映射（漏斗 6 字段 ← 各来源）
| 需要的字段 | Seerfar API (market/search/ozon) | Seerfar Excel | Ozon 后台热词 | MPStats/Moneyplace/Маяк |
|---|---|---|---|---|
| searchVolume | `searchVolume` ✅ | ✅ | 热度 ✅ | ❌免费层 |
| count30GrowthRate | `count30GrowthRate` ✅ | ✅ | ❌ | ❌ |
| competingProducts | `competingProducts` ✅ | ✅ | 竞品数 ✅ | ❌ |
| avgPrice | `avgPrice` ✅ | ✅ | 平均价 ✅ | ❌ |
| returnRate | `returnCancellationRate` ✅（口径=退货+取消，需确认） | ⚠️列名待核对 | ❌ | ❌ |
| top products (title/image/price/ozonId) | `products[]`: title/sku/ozonId/imageUrl ✅（price 走 productReport） | ✅（热销榜单导出含图/价/SKU） | ❌ | ❌ |

## 矛盾与缺口
- **矛盾**：Seerfar 定价页把 Data Export 列为 Starter "50 times/day"，而 V2.5.0 更新称勾选导出覆盖 8 个模块——按"有套餐可导、次数受限"理解，未实测。
- **推断（非源述）**：Nexscope 与 Seerfar 字段命名逐一对应（含 count30GrowthRate/competingProducts 等非常规名），且 Seerfar 教程图床域名为 nexulas-home.*，提示两者数据同源或同体系——迁移成本应极低，但无官方声明证实。
- **缺证**：Seerfar API 具体积分数价、开通流程；Ozon 后台热词的确切免费可见字段与导出按钮（DNS 不可达未能查官方页）。

## Sources
- Seerfar Open API 官方文档源文件 — http://doc.seerfar.cn/api-docs.config.json （字段级证据，本次核源核心）
- Seerfar 关键词挖掘教程（指标定义+Excel导出） — https://www.seerfar.cn/articles/keyword-expansion-tutorial
- Seerfar V2.5.0 更新（导出覆盖模块） — https://www.seerfar.cn/articles/版本更新-seerfar-v2-5-0版本更新详细介绍
- Seerfar 定价（免费层/套餐/导出限额） — https://www.seerfar.com/pricing
- Seerfar FAQ（平台覆盖、3-5天试用） — https://www.seerfar.cn/articles/seerfar-用户常见问题faqs
- Seerfar 市场热词教程 — https://www.seerfar.cn/articles/market-research-tutorial
- Ozon Seller API product-queries（官方 OpenAPI 镜像，含 Premium 门槛原文） — https://1gtgicrc0d.apifox.cn/api-306863083
- Ozon 帮助页变更记录（查询分析→Premium Pro/Plus，2026-10-28） — https://selltrix.ru/journal/ozon-poiskovye-zaprosy-premium/
- Ozon 后台热词字段转述 — https://mpseo.ru/poiskovye-zaprosy-ozon/ ; https://skuscan.ru/ozon/podbor-klyuchevyh-slov/
- Маяк 定价（免费仅插件，热词/导出付费） — https://mayak.bz/tarif
- **Rejected**: cocoloop/claudeskills 的 LinkFox 第三方包装 API（非 Seerfar 一手）; willbedone/a3-agency 等 SEO 博客; New Retail 2024 旧闻（"热词完全免费"已被 2026 帮助更新覆盖，视为过期证据）。
