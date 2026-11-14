# Handoff: Ozon 上架链路调研 → OSS 图床 → 定价数据库/公式 → slot1 v4 → 审核台编辑器 v1

> 日期：2026-11-11 · 单会话 · 项目仓库：`~/rust_workspace/ozon-pipeline`
> 状态：审核台编辑器已上线并端到端实测通过；24 测试全绿；上架硬阻塞全部关闭。
> 前篇：[2026-11-10-ozon-imagegen-v3.md](./2026-11-10-ozon-imagegen-v3.md)

## 1. 本会话完成（全部未提交 git）

| 主题 | 要点 |
|---|---|
| 上架方案调研 | 自建卡路线（/v3/product/import，非跟卖）；8 个 endpoint 结构/限流/必填字段 → `docs/research/ozon-listing-api.md` |
| 1688 采购价确认 | 图搜/文搜已返回 `price` 并入 `metrics.sourcing.price_cny`；是列表展示价（起批价），非 SKU 实价，无阶梯价、不含国内运费 |
| OSS 图床（硬阻塞②关闭） | 阿里云 OSS 法兰克福 bucket `ozon-pipeline-img`，公共读、免 ICP、按量付费；RAM 子账号+最小权限策略 `ozon-pipeline-img-rw`；上传+公网 GET 实测 200。凭证在 `.env`（`OSS_*`，不进 git） |
| 店铺币种 | **CNY** [user-decided 后台实查] → import 传 `currency_code="CNY"`，price 直接填人民币价 |
| 定价数据库 | `data/ozon-commission.json`（542 叶子类目三档佣金，cate_id=description_category_id 直接 join）；`data/ozon-logistics.json`（60 条费率：3 尺寸档×11 供应商×express/standard/economy） |
| 定价公式逆向 | 毛子ERP 定价工具（登录态 ego-browser 抓包）反推出精确公式，对拍验证到分 → `docs/research/ozon-pricing-formula.md`。**新增两个官方固定扣点：ITO 0.33% + 尾程 2%**（ROADMAP 原成本模型没有） |
| slot1 v4（兑现时刻） | 静物→情绪钩子（手+泡沫/抽屉装餐具/狗叼玩具）；6+3+3 轮迭代收敛，跨品类压测通过 → `docs/research/ozon-cover-emotion-v4.md`（含全部教训） |
| **审核台编辑器 v1** | 详情页"✏️ 上架编辑"→四区模态（文案/尺寸重量/定价计算器/上架属性）；浏览器实测卡2 全流程通过 |

### 新增/改动代码文件
- 新增：`pricing.py`(109)、`ozon_client.py`(82)、`listing_edit.py`(87)、`tests/test_pricing.py`(6 测试)、`tests/test_listing_edit.py`(5 测试)
- 改动：`review_server.py`（编辑器 UI+5 个 API 路由）、`image_worker.py`（slot1 支持 `cover_action_en` 触发 v4，v3 保留）、`text_overlay.py`（**标题字色自适应**：按顶部区亮度亮底深字/暗底浅字 [user-decided]）、`GLOSSARY.md`（新增术语：上架属性、类目佣金）、`prompts/slot1_payoff_v4.txt`（新增模板）
- 测试基线：**24 绿**（原 13 + 定价 6 + 编辑 5）

## 2. 关键实测结论（不要重复验证）

- **定价公式**（毛子ERP 口径，服务端验证到分）：`P = (采购价+国内运费+物流费) ÷ (1−佣金%−广告%−其他%−0.33%−2%−利润%)`；划线价=P÷(1−划线折扣)；₽=P×汇率。**佣金按售价₽三档迭代**（低档起算→跨档重算至收敛，服务端越档拒算）；物流按采购价货值选档。详见 ozon-pricing-formula.md。
- **毛子ERP 抓取方法**：页面 fetch 跨域被 CORS 拦；正解=CDP `Page.addScriptToEvaluateOnNewDocument` 注入劫持 XHR，reload 后页面自发请求时捕获响应体。API 无自定义头（纯 cookie 会话）。
- **slot1 v4 手部约束**：负面约束（no hands）服从率 2/2 失败——把手纳入规则精确管控（至多一只、仅从底部入画、腕连小臂不断腕、前景不穿越产品、禁人脸）。**几何留白（色带坐标）在全动态场景失效→改景深留白**（背景整体浅景深虚化即贴字位）。v4 单图成功率 ~60%，投产按 1.5× 重试成本预算。
- **审核台编辑器**：属性定义实时拉 Ozon（磁盘缓存 data/attr-cache/），字典属性用 HTML datalist 搜索 + dictionary_value_id 映射；编辑写回 spec.listing_edits（slot7 先例），sync_to_db 重跑不丢。
- **researcher 子代理本会话仍连续 Connection error 不可用**（第三次会话复现），调研全部走主会话 web 工具 + ego-browser。

## 3. 当前实况

- **审核台 server 运行中**（8080）。**卡2 已被填入测试演示数据**（尺寸 35×25×6cm、重量 300g、售价 ¥31.54、5 属性值）——⚠️ 是我编的示例值非实物真值，**用户须对照实物改正尺寸/重量**。卡1 未动。
- **slot1 A/B 候选就绪**：皂液器 B6✅ / 收纳 B3⚠️(持勺手+纸箱) / 狗玩具 B3✅，在 `data/gen/<offer>-ab/`；A 版 v3 资产未动。
- **卡2 need_confirm 已清除**（编辑器保存即人工确认，Q2 语义）；卡1 仍 need_confirm（标题/芯片"免打孔"重复未修）。
- 词库 79 词已消费完，下轮 `keyword_library.py build --limit 10`（≈320 积分）。
- 测试：`python3 -m pytest tests/ -q` → 24 绿。

## 4. Next Steps（按优先级）

1. **用户核对卡2 真实尺寸/重量**（编辑器里改），卡1 修文案去重 → 两卡过审进 approved
2. **PoC 手动建 1 张卡**（任务 8，0 代码）：后台向导走一遍，记录必填属性/媒体步骤/币种实况，校准 listing_worker 假设
3. **`listing_worker.py`**（任务 7）：approved→listed；import→import/info 轮询→prices/stocks→listed；单卡单请求、日限 100、429 退避；图片经 OSS 传公网 URL（凭证已就绪）
4. content_agent 扩字段：`cover_action_en` 自动生成（三规则见 v4 文档 §7）+ 上架属性 LLM 预填 + 芯片/标题去重校验
5. 上架后跑官方 A/B-тест фото（封面 A/B），结果落 reviews 表 kind="ab_cover"
6. 遗留：unit_econ 自动判负闸（任务 4，等计算器攒 10-20 真实定价样本后固化）；佣金/物流库刷新脚本落盘；汇率动态源；无货源卡处置规则[agent-suggested]；打回重生接 seed[agent-suggested]
7. 用户待办：open.1688.com 企业入驻（pkgSizeSource 真尺寸）；`TOP_BLACKLIST` 草案确认；排查 researcher 子代理 Connection error

## 5. 重要规则沉淀

- **编辑持久化唯一通道 = spec.listing_edits**（Q1：spec 是资产之源，DB 是镜像）；**编辑即人工确认**（Q2：清 need_confirm + 落 event，approved 未 listed 可编辑不回退状态）。
- **图内文案不进编辑器**（Q3：改图内文字走打回重生；编辑器只管纯数据字段）。
- **定价 = 计算器辅助人工**（Q5：任务 4 拆两步，先人工校准样本再自动化）。
- 文字双轨制/数字溯源/同一性闸不变；新增：字色自适应（亮深暗浅）。

## 6. Suggested skills

- `verify` — 改动后 `pytest tests/ -q`（24 绿基线）
- `tdd` — listing_worker 开工（验收：10 卡批量上架无 429、图片全量上传）
- `diagnosing-bugs` — 审核台/生图/上架异常
- `research` — **researcher 子代理不可用，用主会话 web 工具 + ego-browser**
- `ego-browser` — 需登录态页面（毛子ERP 刷新、Ozon 后台 PoC）
- `grill-with-docs`（grilling+domain-modeling）— 新功能边界拷问
- `handoff` — 会话结束

## 7. 参考文档（勿重复调研）

- `docs/research/ozon-listing-api.md` — 上架 API 全链路（8 endpoint/限流/必填字段/坑）
- `docs/research/ozon-pricing-formula.md` — 定价公式 + 佣金档迭代 + 物流档 + 数据来源维护
- `docs/research/ozon-cover-emotion-v4.md` — slot1 v4 方案 + 全部实战教训（手部/留白/字色）
- `docs/ozon-dropshipping-research.md`（my-mind 仓库）— 前期选品/找货/上架调研
- `GLOSSARY.md` — 文字双轨制、上架属性、类目佣金等术语
