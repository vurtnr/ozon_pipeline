All evidence gathered. The configured output path is outside my sandbox and I have no write-capable filesystem tool for it, so per protocol I'm returning the complete artifact in this response for the runtime to persist.

---

# Research: 1688 商品真实规格尺寸数据来源（slot7 解锁）

## 结论（TL;DR）

**现有凭据拿不到包装尺寸。** 本 repo 已集成的唯一一个 1688 数据端点是 `POST /api/alibaba.1688.find.product/1.0.0`（gateway.1688.com Claw Skill 网关，`ALI_1688_AK` 签名），它返回 SKU 标题（`sku_title`，如 "20cm 细网"）但**不返回** 重量/长宽高。真实装箱尺寸存在于开放平台官方接口 **`product.search.queryProductDetail`**（`com.alibaba.fenxiao.crossborder`）的 `productShippingInfo` 块中，含 `length/width/height/weight`、SKU 级 `skuShippingDetails[]`，以及来源标记 `pkgSizeSource`（"预测-同款放大 / 商家自填 / 官方测量"）和 `officialLength/officialWidth/officialHeight/officialWeight`、`aiWeight`——这正是 slot7 闸需要的"可溯源真实尺寸"。

**最优路径（按成本排序）：**
1. **同 AK 试 `prod_detail`**：官方姊妹 skill `next-1688/1688-shopkeeper` 用完全相同的 AK 体系调 `/1688claw/skill/workflow` `{code:"offer_detail"}`，README 称返回"标题、价格、类目、SKU、**属性**与商家信息"。零新增注册，先实测其是否含重量/尺寸字段。
2. **注册 open.1688.com 应用走 `product.search.queryProductDetail`**：需企业开发者资质 + 加入跨境/分销解决方案 + OAuth `access_token`；基础接口免费。搜索/compare 返回的 `detail_url` 里有 offerId，可直接作入参。带 `pkgSizeSource` 分级采信（官方测量 > 商家自填；仅预测值则维持 slot7 拦截）。
3. **备选**：详情页爬取（mtop `queryofferskuselectormodel` + Playwright，违反 ToS、脆弱）；详情图 OCR（LLM 已可用，但参数表常为图片）；旺旺问供应商（人工、最慢但可信）。

## 一、本 repo 已集成的 endpoint 与字段证据

| 调用点 | Endpoint | 凭据 | 尺寸相关字段 |
|---|---|---|---|
| `_http.search_products`（`search_vl` 用；`compare` 内部复用同一 API） | `POST /api/alibaba.1688.find.product/1.0.0` | `ALI_1688_AK`（HMAC 签名，keyring/`.ak_store.json`） | ❌ 见下 |

`_http.py::_parse_product_item` 映射的全部响应字段：`itemId, title, imageUrl, detailUrl, score, currentPrice, skuId, skuTitle, yxIndex, quantityBegin, unit, company, soldOut, storeAmount, userId, memberId, cateId, promotionTags, serviceInfos, sellingPoints`。**没有** attribute/规格参数/重量/装箱尺寸字段；最接近的是 `sku_title`（SKU 规格文本，如 compare.md 示例 "20cm 细网"——是规格名而非装箱尺寸，且非结构化）。

配置键名（未读取值）：`config.py` 加载 `BAILIAN_API_KEY, BAILIAN_BASE_URL, OZON_CLIENT_ID, OZON_API_KEY, NEXSCOPE_API_KEY`；1688 侧为 `ALI_1688_AK` 及 `OAUTH_1688_{ACCESS_TOKEN,REFRESH_TOKEN,TOKEN_SCOPE,...}`（`_auth.py`），走 gateway.1688.com OAuth（`/api/get_token_by_auth_code`、`/api/query_all_scope`）。**注意：这套 Claw AK ≠ open.1688.com 的 AppKey/AppSecret，是两条独立准入。**

## 二、官方 API 证据：哪里能拿到真实尺寸

**`product.search.queryProductDetail`**（open.1688.com，`com.alibaba.fenxiao.crossborder`）——官方文档响应示例直接包含：

```
"productShippingInfo": {
  "weight": 200.0, "width": 1.0, "height": 3.0, "length": 2.0,
  "skuShippingInfoList": [{ "skuId", "specId", "width", "length", "height", "weight" }],
  "skuShippingDetails": [{ "skuId", ..., "pkgSizeSource": "预测-同款放大，商家自填，官方测量",
                           "officialLength", "officialWidth", "officialHeight", "officialWeight",
                           "aiWeight", "aiWeightAccuracy": "80%" }],
  "pkgSizeSource": ..., "officialLength": 12.0, ...
}
```

同响应还有 `productAttribute[]`（`attributeName/value`，即 材质/规格 等结构化属性对）与 `productSkuInfos[].skuAttributes[]`。入参 `offerDetailParam: {"offerId":..., "country":"en"}`；需要 `_aop_signature` + `access_token`；归属"跨境综合解决方案/分销跨境"，**需建跨境铺货关系**（文档原文："跨境场景获取商品详情，需要建立跨境铺货关系之后才能获取详情"）。

其他相关官方接口：`alibaba.product.get`（⚠️ 文档注明"只能查询自己所有的产品"，不适用）；`alibaba.cross.productInfo`（同样跨境解决方案门槛）；`alibaba.category.attribute.get`（叶子类目属性定义，可用于校验"该类目是否存在尺寸属性"）。接入门槛（第三方总结，未逐条官方验证）：企业开发者账号、跨境类应用需单独资质申请、基础接口免费。

## 三、备选方案排序（若不走开放平台注册）

1. **shopkeeper `prod_detail`（同 AK）** — `api_post("/1688claw/skill/workflow", {"code":"offer_detail","bizParams":{"item_id":[...]}})`。可行性高（凭据相同、官方维护），但尺寸/重量字段是否在返回中**未验证**，需实测一个 offerId。
2. **详情页抓取** — SKU 数据在 `mtop.1688.wosc.queryofferskuselectormodel`（`skuPropsList` 含尺寸维度），属性参数部分在 HTML；Playwright 中间人监听可免签名逆向。反爬强、违反 ToS、脆弱——只作临时核验，不进流水线。
3. **详情图 OCR** — 参数表常为图片；已有 qwen3-vl 基建可复用，但"尺寸出现在图里"≠"真实装箱尺寸"，仍需 `pkgSizeSource` 类溯源才能过 slot7。
4. **旺旺问供应商** — 最可信的人工兜底，适合 SKU 确认后一次性获取。

## Sources

Kept:
- open.1688.com queryProductDetail 官方文档（响应字段一手证据）— https://open.1688.com/api/apidocdetail.htm?id=com.alibaba.fenxiao.crossborder%3Aproduct.search.queryProductDetail-1
- open.1688.com alibaba.cross.productInfo / alibaba.product.get 文档（门槛与限制）— https://open.1688.com/api/apidocdetail.htm?aopApiCategory=category_new&id=com.alibaba.product%3Aalibaba.cross.productInfo-1 、 https://open.1688.com/api/apidocdetail.htm?aopApiCategory=product_new&id=com.alibaba.product%3Aalibaba.product.get-1
- next-1688/1688-product-find SKILL.md v1.7.0（本 repo 所用 skill 上游）— https://github.com/next-1688/1688-product-find/blob/main/SKILL.md
- next-1688/1688-shopkeeper prod_detail 源码/README — https://github.com/next-1688/1688-shopkeeper
- 跨境解决方案版本说明（consignPrice 废弃、字段演进）— https://open.1688.com/solution/solutionDetail.htm?solutionKey=1697014160788
- 1688 爬虫 mtop 实战（备选方案 2 依据）— https://www.sakasa.cn/posts/pa1688/
- 接入门槛总结（企业账号/资源包，二手）— https://developer.aliyun.com/article/1758810
- 本 repo：sourcing_agent.py、vendor/1688-product-find/scripts/_http.py、_auth.py、references/capabilities/compare.md

Rejected：imooc/CSDN/everyinfra 等第三方"item_get"转述（SEO 味重、字段与官方文档不一致处未采信）；大吉/凡邦等代理平台文档（非官方凭据路径）。

## Contradictions
- 第三方教程称 `alibaba.product.get` "可查他人商品"，官方文档标注"只能查询自己所有的产品" —— 以官方为准。
- 无证据表明当前 Claw AK 的 scope 覆盖任何详情接口（`query_all_scope` 端点存在但 scope 清单未实测）。

## Missing evidence
- `prod_detail`/`offer_detail` 工作流实际返回字段（是否含重量/尺寸）——需实测。
- 当前 OAuth token 的 scope 集合；open.1688.com 企业入驻对个人的实际可行性；`pkgSizeSource` 各值在真实商品上的覆盖率。

## Next steps
1. 用现有 AK 跑一次 `prod_detail --item-ids <某已知offerId>`，检查是否出尺寸。
2. 查 scope 清单/尝试订购"跨境综合解决方案"评估注册成本。
3. 抽 5–10 个目标品实测 `productShippingInfo` 覆盖率与 `pkgSizeSource` 分布，再定 slot7 采信策略。