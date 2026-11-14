# Ozon Seller API 上架链路 · 一手来源调研

> 调研日期：2026-11-11。用途：listing_worker（ROADMAP V1 任务 7）设计输入。
> 来源原则：docs.ozon.ru 有 Antibot 403，一律采用官方文档镜像（GitHub DragonSigh/ozon-seller-api-docs 英文镜像、Postman 官方集合、Apifox 官方 spec 镜像）与官方 TG 频道存档，逐条标注置信度。
> 前置阅读：`docs/ozon-dropshipping-research.md`（my-mind 仓库，§3.1 上架链路已实测 endpoint 存在）。

## 1. 商品创建：POST /v3/product/import

**必填（缺/错即不创建）**（[英文镜像原文](https://github.com/DragonSigh/ozon-seller-api-docs/blob/master/uploading-and-updating-products-1.md)，置信：高）：

- `offer_id`（卖家系统内唯一 ID，自定）、`name`、`price`（字符串，如 `"1000"`）、`vat`
- `description_category_id` + `type_id`（类目末级，两者都要）
- **真实体积重量**：`depth`/`width`/`height`/`dimension_unit`（mm）、`weight`/`weight_unit`（g/kg）——官方原文："Don't skip these parameters in the request and don't specify 0."
- `attributes[]`：类目必填属性（`is_required=true`，从 /v1/description-category/attribute 获取）
- `images[]`：公网可直链 URL，JPG/PNG，**最多 15 张**；`primary_image` 可选（不给则数组首图为主图）。**图片可直接随 import 内联传入，无需单独调 pictures/import。**

**vat 取值**（[Apifox spec 镜像](https://1gtgicrc0d.apifox.cn/schema-174933727)，置信：高）：`"0"`=免除增值税，`"0.05"/"0.07"/"0.1"/"0.2"`。跨境卖家（Ozon Global，非俄主体）用 `"0"`。

**currency_code**：必须与店铺后台设置的币种一致；默认 RUB，店铺若设为元则传 CNY（[镜像原文](https://github.com/DragonSigh/ozon-seller-api-docs/blob/master/uploading-and-updating-products-1.md)，置信：高）。**本店币种 = CNY [user-decided 2026-11-11 后台实查]** → import 传 `currency_code="CNY"`，price 直接填人民币价。注意：佣金分档（≤1500₽/≤5000₽）仍按卢布计价，档位判定时需 P(CNY) × CNYRUB 汇率换算后比对。

**barcode**：普通必填清单不含 barcode；官方 TG 2023-12-12 公告：**FBP 模式（中国/香港发货入官方仓）必须逐品生成 barcode**，合作仓拒收无码货（[TG 存档](https://t.me/s/OzonEnSellerAPI?before=106)，置信：高）。realFBS（卖家自发）不强制——**本店 realFBS 可不传**。

**更新语义**：更新商品时请求里要传**全部信息**（全量覆盖，不是增量 patch）。

**异步**：返回 `task_id` → 轮询 `POST /v1/product/import/info {task_id}` → 逐品 `status: imported | failed` + `errors[]` + `product_id`（镜像含完整响应示例，置信：高）。一次请求 ≤100 商品。

**审核流**：import 成功后商品进内容审核；官方原文："Once the moderation is complete, the product will appear in your personal account, but won't be available to customers until you put it up for sale."——即**审核通过 ≠ 在售**，还需设价+设库存（realFBS）。

## 2. 类目与属性发现

- `POST /v1/description-category/tree {language:"RU"}`：末级节点给 `description_category_id`（父）+ `type_id`（叶）。本项目 `data/categories-leaf.json` 已有 7,363 叶，`categoryId` 下划线拼接路径的**最后一段=type_id，倒数第二段=description_category_id**。
- `POST /v1/description-category/attribute {description_category_id, type_id, language}`：返回属性列表，关键标志（[SDK 镜像](https://salacoste.github.io/ozon-daytona-seller-api/daytona-ozon-api-docs/categories/11-category.html)，置信：高）：
  - `is_required: true` = 创建时必填
  - `dictionary_id > 0` = 值必须来自字典 → 再调 `POST /v1/description-category/attribute/values` 取 `dictionary_value_id`（**禁止自由文本填字典值**）
  - `is_collection: true` = 可多值；`is_aspect: true` = 前台筛选项（影响搜索曝光，建议填满）
- ⚠️ 镜像所载旧路径 `/v3/category/attribute` 已 404，现行路径是 `/v1/description-category/attribute`（2026-11-05 实测纠正，见 ozon-dropshipping-research.md §6.1）。

## 3. 价格与库存（realFBS）

- 价格：`POST /v1/product/import/prices`，≤1000 商品/请求，**同一商品价格 ≤10 次/小时**（SevenRelay 综述，置信：中高）。也可以在 import 时直接带 `price`/`old_price`。
- 库存：`POST /v2/products/stocks`，body 需 `warehouse_id`（[英文镜像](https://github.com/DragonSigh/ozon-seller-api-docs/blob/master/updating-prices-and-product-stocks.md)，置信：高）。
- 仓库列表：`POST /v1/warehouse/list`（无参数，按 Client-Id 识别），返回 `is_rfbs` 标志（[Postman 镜像](https://www.postman.com/googlesheets/ozon-seller-api/request/fwykmzl/)，置信：高）。
- ⚠️ **2026-03-24 官方公告**：2026-04-07 起停用 7 个 v1 FBS 库存/配送/仓库方法，v2 替代已发布（[KENVARA 转官方公告](https://kenvara.ru/news/ozon-seller-api-stock-warehouse-methods-migration-march-2026/)，置信：中高）。实现时先探测 `/v1/warehouse/list` 是否仍 200，若 404 切 v2 路径。

## 4. 配额与限流

- `POST /v4/product/info/limit`：返回 `total`（在售上限）、`daily_create`、`daily_update`，超限报 `item_limit_exceeded`（镜像原文，置信：高）。
- 本店后台实测（2026-11-06，ozon-dropshipping-research.md §6.8）：**每日可添加 100、可更改 2,000**。
- 全局：每 Client-Id 所有方法合计 ≤50 req/s（官方 overview 镜像）。
- 2026-02-24 起 `/v3/product/import`、`/v1/product/pictures/import`、`/v1/product/import-by-sku`、`/v1/product/update/offer-id` 合并统一商品操作限流，超限 429（存在=高置信，具体数值=低置信，二手"30k/min"存疑）。
- 价格修改 ≤10 次/小时/商品（见 §3）。

## 5. 上架后状态查询

- `POST /v3/product/list {filter:{offer_id:[...]}}` → 拿 `product_id`；`POST /v3/product/info/list` → `statuses`（含审核/可见状态）、`errors[]`、`commissions[]`（各类佣金，**定价可回查真实佣金**）、`volume_weight`。
- `POST /v1/product/rating-by-sku`：内容分（0-100）+ 改进建议清单（图片数/属性填充率/rich-content 等），可做上架后 eval 信号（镜像含完整示例，置信：高）。
- 图片状态：`POST /v1/product/pictures/info`（若单独传图时轮询）。

## 6. 与本项目相关的坑（按踩坑概率排序）

1. **图片必须公网 URL**：Ozon 在 import 时主动来拉图，本地文件不行，且 Ozon 服务器在俄区——图源要俄区可达（防盗链/地域屏蔽会拉取失败）。alicdn 直链不可行（防盗链史）。
2. **尺寸重量非零必填**：1688 pkgSizeSource 需企业入驻（暂拿不到），缺真值时上架被硬拦——必须有人工录入环节。
3. **字典属性禁自由文本**：`dictionary_id>0` 的属性必须给 `dictionary_value_id`，否则 `failed`。
4. **币种必须与店铺设置一致**，否则报错。
5. **全量覆盖语义**：import 即全量更新；pictures/import 二次调用会清空前次图片——图片更新必须一次全量。
6. **审核通过 ≠ 在售**：realFBS 必须再设价格+库存（warehouse 维）才前台可见。
7. 变体合并（同款多色/尺寸并入一卡）：attributes 加 `id=9048` 且除颜色/尺寸外全一致（官方原文）。V1 单 SKU 不用。
