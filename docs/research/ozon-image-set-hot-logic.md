# Ozon 热卖商品卡套图逻辑 + qwen-image I2I 提示词实践

> 研究日期：2026-11-10 · 触发：生图套图平庸、slot8 失败、slot3 图标无标签，重构生图提示词前的调研
> 结论先行：**Ozon 套图是 8 张各担一职的销售漏斗，不是 8 张好看的图**；qwen-image 西里尔文字官方不保证 → 文字全走 PIL；prompt 写法"先不变量后变更"。

## 1. Ozon 官方/平台规则（硬约束）

来源：skuscan.ru 综合 Ozon 官方 Telegram 公告（@ozonmarketplace）与平台帮助页 [secondary, 但引一手公告]；yarkio.ru 规则整理 [secondary]。

- 格式：竖版 3:4，推荐 900×1200px（≥1200×1600 利于缩放）；JPG/PNG/WEBP ≤10MB；每卡最多 30 图。[direct: https://skuscan.ru/ozon/infografika/]
- 主图：商品完整清晰；**允许信息图**（特征+图标），但禁止"скидка/хит/новинка"、价格、配送条件、联系方式、站外二维码。[direct: 同上]
- **封面左下角必须留空**——Ozon 在该位置叠加折扣/活动角标，重要文字会被遮挡。官方构图建议：商品居中或偏右，文字靠左，四周留边距（Ozon 官方 Telegram 2026-02-20, t.me/ozonmarketplace/3016）。[direct: 同上]
- 语言：2026-03-01 起买家可见文字必须俄语（外语词仅限商标/词典词，或附同等大小俄语翻译）。[direct: 同上]
- 内容分（content rating）：媒体项满分 45/100 给"8 图+视频"；**买家平均只看 5-7 张图，主要论据必须进前 5 张**。[direct: 同上]
- 处罚：封面广告词→卡片打回修改期间停售；联系方式→罚款 1000₽+隐藏商品；盗图→罚分。[direct: 同上]

## 2. 热卖套图的 8 张漏斗结构（核心结论）

来源：skuscan.ru（引 Ozon 平台测试数据）[secondary 综合，结构被广泛复述]。

| # | 幻灯片 | 销售职能 | 示例（保温杯） |
|---|---|---|---|
| 1 | 封面钩子 | 争取点击：1 个大利益点+大数字 | "450 мл · 8 часов тепла" |
| 2 | 使用场景 | 尺度感+使用剧本 | 车载杯架里，手做参照 |
| 3 | 核心卖点 | 3 个数字化论据 | 双层壁、密封盖、304 钢 |
| 4 | 细节工艺 | 质量信任 | 盖子剖面、硅胶圈 |
| 5 | 尺寸规格 | "放得下吗" | 高 20 см、直径 7 см |
| 6 | 套装内容 | 到手什么 | 杯+盖+备用圈+盒 |
| 7 | 异议应答 | 回答评价里的疑问 | "倒置不漏水"+实测图 |
| 8 | 变体选择 | 不出卡即可选 | 5 色阵列 |

文字规则 [direct: 同上]：
- **一张图一个信息点**；>3 行短句的封面没人读。
- **每个承诺必须有数字或可验证事实**（"прочный"差，"выдерживает 120 кг"好）。
- 对比只能对"普通商品"（безымянный），不能指名品牌（投诉风险）。
- 字体：一种无衬线体，2-3 个产品同色系颜色；文字/背景对比度 ≥4.5:1。
- 不要在购买前展示负面（"有问题别写评价"类幻灯片有害）。

常见翻车 [direct: 同上]：封面 10 行字手机上不可读；主数字放左下被角标遮挡；方形图被平台补边导致商品比邻居小；承诺无事实→"与描述不符"退货。

## 3. qwen-image I2I 提示词工程（官方+实践）

来源：阿里云百炼官方文档《千问-图像编辑》[direct: https://help.aliyun.com/zh/model-studio/qwen-image-edit-guide]；modellix prompt guide（引用官方 enhancer 行为与模型卡）[secondary: https://www.modellix.ai/blog/qwen-image-edit-prompt-guide]。

- **文字渲染：官方只保证简体中文和英文**，"其他语言可自行尝试，但效果存在不确定性"（官方 FAQ）→ **俄语/西里尔全部走 PIL 双轨，包括图标标签**。[direct: 官方 FAQ]
- **同一性写法：先声明不变量，后声明变更**（invariant first, change second）。官方 enhancer 模板："Replace X with Y; **keep smile, short hair, and gray jacket unchanged**"。产品同理：先"keep the product pixel-identical"，再说放哪。[direct: modellix 引官方 enhancer]
- 一条指令一个动作，祈使动词开头；指令预算 3.0 系列 ~800 token。[direct: modellix]
- **negative_prompt 是正式参数**（3.0 系列），适合放反复出现的失败模式：warped text, altered labels, extra objects, deformed product。[direct: 官方 API 参数表]
- `prompt_extend`（智能改写，默认开）：构图必须精确保持时建议关闭——改写器会"填坑"，可能破坏留白约定。[direct: 官方参数说明 + modellix]
- `seed`：相同 seed 结果相对稳定 → 打回重生换 seed，审过的图锁 seed。[direct: 官方参数说明]
- 多文本区域/长字符串一次渲染必出乱码 → 一张图最多一个 AI 文本区域，最好零个。[direct: modellix 实测]
- 输入 1-3 张参考图为最优；最后一张图可能决定输出画幅 → size 显式指定。[direct: 官方]

## 4. 对本流水线的设计结论 [推断]

1. **保留并扩大文字双轨制**：标题/徽章/图标标签/场景标签全部 PIL（Manrope Bold），AI 只画产品+场景+无字图标。官方证据支持，不是妥协是架构。
2. **版式网格化**：AI 按"3 列固定网格画图标位（无字）"，PIL 按同一网格坐标贴标签——绕开"AI 不知道把字留哪"的坐标问题。
3. **8 slot 重映射到漏斗职能**：slot3 从"标题+徽章"改"3 个数字化论据格"；slot8 从"长说明带"改"3 场景图标+各自标签"（顺带根治单行贴字溢出）。
4. **反平庸 = 反"库存图美学"**：删掉所有"premium minimal/calm warm tones"式空泛风格词，改为：高对比、一个视觉焦点、信息图密度、利益点先行。文案侧 content_agent 必须拿到更多事实（竞品标题/OCR 尺寸/供应商参数）才写得出带证明的卖点。
5. **封面左下留空**（平台角标区）写进 slot1 模板——当前模板恰好把图标列放在那。
6. call_api 增加 negative_prompt；OpenAI 兼容端点已确认支持 `negative_prompt`/`seed`/`prompt_extend`（百炼扩展字段，顶层平铺）[direct: https://docs.bailian.console.aliyun.com/zh/model-studio/qwen-image-generation-and-editing-api-reference]。

## 5. 2026-11-10 乱码事故补充 [direct: 实测]

- **提示词里写"reserved for a headline/text chips"等于邀请模型画字**——预留区只描述为"empty background"，用途只写进代码注释。
- negative_prompt 即使生效也不是充分条件（模型仍会偷画字）→ image_worker 增加 VL 成品检测闸：除 slot7 外成品带叠加文字→加强措辞重试一次→仍带字判失败，不交付乱码图。
- **人格 preamble 不能出现"Ozon"品牌名**——模型直接画出 Ozon logo（侵权）；也不能用"designer/design"措辞——会把任务理解成做信息图海报。改用"product photographer + 文字由排版工具后加"。
- **style_ref 模版图自带文字/拼贴版式会被 I2I 一并迁移**（slot4 连续失败根因）→ 机制整体停用，v3 提示词自带版式。
- **场景动作词决定道具先验**："演示机构"→ 开箱/纸箱/说明书（满图文字）；改为"使用中"（放进抽屉）后一次通过。VL 闸问法需含"包装盒/纸张/背景物体上的可读文字"，只说"叠加文字"会漏道具。
