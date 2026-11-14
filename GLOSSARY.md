# Ozon Pipeline

为中国卖家（Ozon Global realFBS 跨境店）构建的数据驱动铺货自动化系统：选品 → 找货 → 素材 → 上架，全流程无人值守，仅图审、视频审两道人审。

## Language

**候选（Candidate）**:
通过六维漏斗初筛的 Ozon 关键词及其 top 爆款，流水线处理的最小单元。唯一键 = query + ozon_id。
_Avoid_: 选品结果、商品（未上架前不叫商品）

**闸门（Gate）**:
流水线上每一道机械/LLM 检查点（选品、找货、趋势、算账、内容、生图）。闸门只杀不放，被杀的候选进 dead 并记录死因。
_Avoid_: 步骤、阶段（阶段是中性的，闸门强调淘汰语义）

**待审（review_pending）**:
素材齐备、等待人工图审的状态。审核台队列的唯一来源。
_Avoid_: 待审核、审核中

**打回（regenerate）**:
图审不通过但商品本身可做：状态回 imaging 换 seed 重新生图，同时落一条 eval 标签。
_Avoid_: 拒绝、重做

**毙品（kill）**:
图审判定该候选不值得继续：状态进 dead，死因必填（图片质量/商品形变/文案错误/侵权风险/利润不足/其他），反哺阈值校准。
_Avoid_: 删除、淘汰

**eval 标签（eval label）**:
审核台每次点击（过/打回/毙品 + 死因）落 reviews 表的记录，累积成 golden set，用于 prompt 迭代对照与阈值回归校准。
_Avoid_: 审核记录（它首先是评估数据，其次才是日志）

**套图（slot set）**:
一张商品卡的 8 张图 + 1 视频封面，每个位置称 slot（slot1 封面信息图 … slot8 适用场景条）。
_Avoid_: 图片集、主图（主图只是 slot1）

**文字双轨制**:
图片文字的生产分工：AI（qwen-image）只画场景+图标小字，俄语大标题由本地 PIL（text_overlay.py）贴字——因 AI 渲染西里尔大字有字母级错误。
_Avoid_: 文字渲染、贴字

**数字溯源**:
内容闸的机械校验规则：LLM 产出的任何数字必须能在 1688 标题/竞品属性等原始来源中找到，找不到即判定编造，置 need_confirm 转人工。
_Avoid_: 参数校验、事实核查

**need_confirm**:
内容闸机械校验未过的标记，该候选进图审队列时带 ⚠️ 警告，人审必须逐条核对校验问题。
_Avoid_: 待确认、有风险

**文案（listing copy）**:
上架用的俄语文字内容 = ozon_title（标题 ≤200 字符）+ description_ru（详情介绍 ≤1000 字符）+ 套图 slot 文案。由 content_agent 在生图前产出。
_Avoid_: 描述、详情（单独说"详情"时指 description_ru）

**上架属性（listing attributes）**:
Ozon 类目要求填写的商品特征（характеристики，如颜色/材质/品牌），含必填（is_required）与筛选项（is_aspect）。字典值属性只能从官方字典选值（dictionary_value_id），禁止自由文本。由 LLM 预填、人工在图审阶段确认。
_Avoid_: 商品标签、参数

**类目佣金（category commission）**:
Ozon 按类目与售价档（≤1500₽ / 1500–5000₽ / >5000₽）抽取的销售佣金百分比。本地维护在 `data/ozon-commission.json`（数据源：毛子ERP 定价工具，542 个叶子类目，cate_id 即 Ozon description_category_id）。
_Avoid_: 平台扣点、抽成
