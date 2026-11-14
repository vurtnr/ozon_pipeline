# Research: Ozon 一级类目中文名核查（Seerfar 数据源 vs 用户禁售黑名单前缀匹配）

> 产出路径说明：本次运行唯一授权输出为本文件（artifacts outputs/research.md）。任务正文中提到的仓库路径 docs/research/ozon-category-top-level-mapping.md 按运行指令被忽略，未写入仓库（保持只读约束）。
> 证据分级：【直证】= 我亲自读取/抓取核验；【转述】= supervisor 代跑提取后文字告知（本地 PoC 文件为单行 249KB JSON，超出我 read 工具 50KB/行上限，无法直接复读；supervisor 摘要文件 categoryInfo-summary.txt 在多个候选路径均未找到）；【推断】= 我基于证据的推理。

## Summary
用户给出的名单实际是 **11 个名字（任务写"9 个"）**。逐项核对结果：精确存在且可直接用作 cnTitlePath 首段前缀的只有 **电子产品、鞋类、食品、药店** 4 个；**电影/音乐/视频游戏** 在数据源中不是独立一级类目，而合并为「电影、音乐、视频游戏、软件」；**珠宝、慈善** 在 Ozon 官方树中没有对应一级类目；**书籍、Ozon Fresh食品** 树中存在对应俄语一级类目（Книги、Продукты питания Ozon Fresh）但本地无 Seerfar 中文写法样本，无法确认精确字符串。id 结构：一级类目 id 为**单段数字**，商品级 categoryInfo.category.id 为下划线连接的完整路径（如 15621031_200001517_93157），文档对 categoryIds 是否自动包含子类目**无任何说明**（缺口）。

## Findings

1. **Claim:** PoC 落盘数据（data/poc/productReport-20261113-234929.json，来自 POST /open-api/productReport/search/ozon）共 100 条 records，全部 level=3；cnTitlePath 分隔符 100/100 为「 > 」；中文首段分布：服装17、电子产品16、家用电器10、药店10、家具9、宠物用品8、鞋类7、住宅和花园6、食品4、日化4、美容和卫生3、建筑和装修2、儿童用品2、电影、音乐、视频游戏、软件1、文具1（合计 100，内部自洽）。**Sources:** supervisor 代跑提取（interview 回复，2026-11 会话）。**Support:** 转述（文件本体无法用现有工具复读，见 Missing evidence）。**Confidence:** medium。

2. **Claim:** Seerfar 的 cnTitlePath 是 Seerfar 自行翻译的中文路径，与 Ozon 俄语树逐层对应；文档自带样例：`"titlePath": "Обувь > Туфли"` ↔ `"cnTitlePath": "鞋类 > 单鞋"`；`"Кино, музыка, видеоигры, софт" → "电影、音乐、视频游戏、软件"`；`"Бытовая техника" → "家用电器"`；`"Одежда" → "服装"`；`"Канцелярские товары" → "文具"`。**Sources:** [Seerfar api-docs.config.json](http://doc.seerfar.cn/api-docs.config.json)（free，匿名可抓取），存储内容检索 "cnTitlePath"/"fullCategoryId"/"鞋类"/"Бытовая техника"。**Support:** direct evidence。**Confidence:** high。

3. **Claim:** categoryInfo 结构：`category.id` 为下划线连接的完整路径 id（三级=三段，如 `17027492_17029017_91400`、`15621032_15621049_115951147`）；`pid` 为父级完整路径（去掉最后一段，如 `"pid": "17027492_17029017"`）；`level` 为深度（1/2/3）；另有 `fullCategoryId` 数组 = 各级累计路径 `["15621031","15621031_x","15621031_x_y"]`，及 `titlePath/cnTitlePath/enTitlePath`、`children`。**Sources:** api-docs.config.json 多个端点的 responseExample/responseFields。**Support:** direct evidence（文档样例）+ 转述（PoC 100 条实测 id 三段、pid=父级前缀、level 全 3）。**Confidence:** high。

4. **Claim:** 一级类目 id 形态 = **单段数字**。Ozon 俄语树（data/categories-leaf.json，51,555 行、约 7,363 叶子、26 个一级）中 categoryId 首段即一级 id：我直接抽样核验 9 个：Бытовая техника=17027486、Товары для животных=17027487、Дом и сад=17027494、Строительство и ремонт=17027482、Спорт и отдых=17027491、Электроника=**15621042**、Красота и гигиена=17027489、Автотовары=17027495、Мебель=17027915。supervisor 全表另给出：Одежда=15621031、Обувь=15621032、Аптека=52265716、Продукты питания=17027496、Продукты питания Ozon Fresh=**200001388**、Книги=200001482、Кино,музыка,видеоигры,софт=**99999999（伪 id）**。**Sources:** data/categories-leaf.json（直证抽样）+ supervisor 提取（转述）。**Support:** direct evidence + interpretation。**Confidence:** high（抽样部分）/ medium（全表）。

5. **Claim:** 用户名单逐项核对（11 项）：
   - **电子产品** ✅ 精确存在：PoC 首段 ×16；树中 Электроника=15621042【直证+转述】
   - **鞋类** ✅ 精确存在：文档样例 + PoC ×7；Обувь=15621032【直证】
   - **食品** ✅ 精确存在：PoC ×4；Продукты питания=17027496【转述+直证(PoC 计数)】
   - **药店** ✅ 精确存在：PoC ×10；Аптека=52265716【转述+直证(PoC 计数)】
   - **珠宝** ❌ 无一级类目：树中 Ювелирн* 子串仅 2 处（更深层级），PoC 首段 0 次 → 前缀匹配首段永远命不中【转述 grep 计数】
   - **书籍** ⚠️ 树中有一级 Книги=200001482，但 PoC 0 样本，Seerfar 中文写法（书籍/图书?）本地无法确认【转述】
   - **电影 / 音乐 / 视频游戏** ❌ 均非独立一级：合并为「电影、音乐、视频游戏、软件」（PoC ×1 印证含顿号与"软件"尾缀；文档样例同），用户三个名字都无法精确首段匹配【直证(文档)+转述(PoC)】
   - **Ozon Fresh食品** ⚠️ 树中一级为「Продукты питания Ozon Fresh」=200001388（样例叶子 200001388_17028768_973031355），Seerfar 中文名本地无样本；"Ozon Fresh食品" vs "Ozon Fresh 食品"（空格变体）无法裁定【转述】
   - **慈善** ❌ 完全不存在：树中 Благотворительн* = 0 处，PoC 0 样本，文档无【转述 grep】
   **Sources:** 同上各条。**Confidence:** 高（4 个✅）、高（3 个合并类目❌）、中（书籍/Ozon Fresh ⚠️）。

6. **Claim:** categoryIds 参数的文档定义仅为「类目 ID 数组 / Category ID array」（required: false, example: "-"），productReport 端点（POST /open-api/productReport/search/ozon）与类目端点（POST /open-api/category/detail/search/ozon）均如此；**全文无任何关于"传一级 id 自动包含子类目"的说明**。类目端点请求示例传的是完整三级路径 `15621032_15621049_115951147`。**Sources:** api-docs.config.json（存储检索 "categoryIds"×多处、"productReport/search/ozon"）。**Support:** direct evidence。**Confidence:** high。→【推断】把一级单段 id 传入 categoryIds 是否等价于整棵子树，属于未文档化行为，黑名单若依赖它必须先用一次低成本实测验证。

7. **Claim:** 文档指定的类目 id 权威来源是 Feishu 在线表格：categoryId 描述为「类目id。从类目文档中获取，文档链接：[Ozon类目](https://e1p7i0bkkue.feishu.cn/wiki/R2Nmwih5li76bdkv71ecJkBFnJg)」；匿名抓取该链接 **302 跳转到 accounts.feishu.cn 登录页，不可免费访问**（无 API Key，符合任务约束未再尝试）。**Sources:** api-docs.config.json（feishu 检索命中）；fetch_content 实测跳登录。**Support:** direct evidence。**Confidence:** high。

8. **Claim:** 文档内的顶级 id 样例存在匿名化/不一致（如 Кино-музыка 组顶级 id=99999999 且样例 disabled:true；Бытовая техника 在不同样例里分别出现 pid 657 与本地树 17027486；音乐订阅 pid=99999999_200001489），因此**文档样例 id 不可作为顶级 id 对照表**；对照应以本地 categories-leaf.json（Ozon 树）+ PoC 实测为准。**Sources:** api-docs.config.json 样例 vs data/categories-leaf.json 实读。**Support:** direct evidence + researcher inference。**Confidence:** high。

## Contradictions
1. **Seerfar 中文翻译漂移**：同一个顶级类目 Канцелярские товары 在文档不同端点样例中分别译作「文具」和「办公用品」（cnTitlePath "文具 > 纸 > 打印纸" vs "办公用品/纸张/打印纸"）。→ 中文首段字符串随端点/版本可能漂移，黑名单匹配建议同时校验 id 首段。【直证】
2. **路径分隔符不一致**：文档样例中 cnTitlePath 同时出现「 > 」和「/」两种分隔符（不同端点）；PoC productReport 实测 100/100 为「 > 」。→ 若未来换端点，按首段切分需兼容两种分隔符，或改用 category.id 首段。【直证】
3. **任务计数矛盾**：任务称"9 个名字"，实列 11 个（电子产品、鞋类、食品、药店、珠宝、书籍、电影、音乐、视频游戏、Ozon Fresh食品、慈善）。按 11 项逐一核对。【直证（任务文本）】
4. **用户认知 vs 数据结构**：用户把「电影/音乐/视频游戏」「Ozon Fresh食品」当作独立一级类目；数据源中前者是合并类目（含"软件"后缀），后者树中名为「Продукты питания Ozon Fresh」。【直证+转述】

## Missing evidence
- **PoC 原始文件未能独立复读**：productReport-20261113-234929.json 为单行 249KB JSON，read 工具行上限 50KB，无 bash/jq；Finding 1/5 中的 PoC 统计全部来自 supervisor 转述（数字内部自洽：计数和=100，与文档独立证据互相印证），标注为转述级证据。supervisor 称已写入 categoryInfo-summary.txt 但我在 9 个候选路径均未找到该文件。
- **书籍、Ozon Fresh食品 的 Seerfar 中文精确写法**：本地 PoC 无样本；免费渠道（docs）无完整类目表；Feishu 表需登录。→ 这 2 个名字如果保留在黑名单，需一次付费端点实测或人工登录 Feishu 表确认。
- **categoryIds 传一级 id 的子树包含语义**：文档零说明，未验证（任务禁止调用付费端点）。
- **data/category-tree-ru.json**（869.8KB 单行）同样超出 read 工具行上限，未能直读；已用 categories-leaf.json（同目录、多行版叶子数据）替代核验 26 个一级中的 9 个。
- PoC 100 条中**无任何 level=1 记录**，"一级 categoryInfo.category.id 的 pid 取值"（null/"0"/空）无本地样本。

## Sources
- Kept:
  - Seerfar Open API 文档 http://doc.seerfar.cn/api-docs.config.json — 免费匿名可读；categoryInfo 结构、cnTitlePath 样例、categoryIds 定义、Feishu 类目表链接、productReport/category 端点路径的唯一权威文本证据。
  - data/categories-leaf.json — Ozon 官方树叶子级数据（俄语），一级 id=categoryId 首段；我直接抽样核验 9 个一级类目 id。
  - data/poc/productReport-20261113-234929.json（经 supervisor 提取）— 唯一的 Seerfar 实测中文首段分布样本。
  - supervisor interview 回复 ×2 — 提供 PoC 文件名、PoC 统计、26 个一级俄名↔id 对照、grep 计数（Ювелирн=2、Благотворительн=0）。
- Rejected/deprioritized:
  - Feishu 类目表 https://e1p7i0bkkue.feishu.cn/wiki/R2Nmwih5li76bdkv71ecJkBFnJg — 匿名访问跳登录，本任务约束下不可用（已记录为文档指定的权威来源）。
  - data/category-tree-ru.json — 869.8KB 单行，工具不可读，信息与 leaf 文件重叠。
  - 未调用任何 api.seerfar.cn 端点、未使用任何 API Key（任务约束）。

## Next steps
1. 用一次最低成本实测回答"categoryIds 传一级单段 id 是否含子类目"（例如传 15621042 与传其任一三级 id 各查一页，比较返回 categoryInfo 首段）——需解锁付费调用授权后做。
2. 登录 Feishu 类目表（或向 Seerfar 索取 CSV）补齐「书籍 / Ozon Fresh食品 / 珠宝所在父类目」的 Seerfar 中文精确字符串与最新一级清单。
3. 黑名单实现建议（推断）：以 **id 首段白名单/黑名单为主、cnTitlePath 首段为辅**（两套同时校验），并按 finding 8 伪 id 99999999 与 finding 1 翻译漂移预留映射表；「电影、音乐、视频游戏、软件」必须作为单一整体条目。
