# Ozon Pipeline Agent 架构评估

> 调研日期：2026-11-12。用途：判断当前仓库是否已经是 AI Agent 产品，还是“普通 Python 流程 + 局部 AI 调用”，并给出最小可行演进路线。  
> 方法：优先使用 `.ua/knowledge-graph.json` / `.ua/meta.json` 定位模块、入口与调用关系，再定向阅读源码验证；关键事实均以源码路径+行号为准。未读取 `.env`，未执行上架/生图/外部 API 写操作。  
> 结论先行：当前系统已经有较完整的跨境铺货业务链路、SQLite 状态机、人工审核台和多处 AI/VL/生图能力，但架构形态仍主要是**脚本式确定性流水线 + 局部模型调用**，不是产品级 AI Agent 编排系统。

---

## 1. 总体判断

### 1.1 不是“纯脚本玩具”，但也不是 Agent 产品

当前项目的核心资产是：

- 以 `pipeline.db` 为唯一事实源的状态机、事件表、审核表、关键词库和积分台账（`pipeline.py:2-23`, `pipeline.py:25-66`, `pipeline.py:68-102`）。
- 选品、找货、文案、生图、审核、上架等阶段已经拆成独立 CLI/worker（例如 `select_products.py:2-13`, `sourcing_agent.py:2-13`, `content_agent.py:2-14`, `image_worker.py:2-18`, `listing_worker.py:2-13`）。
- 多个关键风险点用确定性规则或人工节点兜底：状态合法流转（`pipeline.py:25-44`, `pipeline.py:160-171`）、禁忌词/长度/数字溯源（`content_agent.py:110-160`）、同一性闸（`sourcing_agent.py:110-154`）、slot2 文字闸与 slot7 数字闸（`image_worker.py:111-158`, `image_worker.py:183-189`, `image_worker.py:242-330`）、人工图审与上架编辑（`review_server.py:550-681`, `listing_edit.py:19-75`）。

但它还不是产品级 Agent 编排，因为：

- 没有显式 planner 或任务图运行时，阶段推进主要靠人或 CLI 串联；`sync_to_db.py` 明确说 workers 暂不写 DB，本脚本只是 artifacts 到 DB 的桥，并且 trend/econ worker 未完全纳入自动执行（`sync_to_db.py:2-10`, `sync_to_db.py:156-185`）。
- LLM/VL 调用是硬编码在局部函数里的能力调用，不是统一 tool abstraction；如文案 `llm_spec()` 直接 POST 百炼 chat，找货 `vl_same_product()`/`vl_describe()` 直接调 VL，生图 `call_api()` 直接调 images/generations（`content_agent.py:81-107`, `sourcing_agent.py:110-139`, `sourcing_agent.py:189-226`, `image_worker.py:84-108`）。
- 状态和 memory 主要是 SQLite metrics JSON + 文件 artifacts，缺少 run/task/attempt/tool_call 级别的统一执行记录、可重放上下文和跨阶段记忆。
- 人工节点已经存在，但不是可配置的 human-in-the-loop 策略；图审和编辑器逻辑集中在本地 HTTP handler/UI 中（`review_server.py:1-13`, `review_server.py:515-547`, `review_server.py:803-818`, `review_server.py:821-924`）。

**判断**：现状更准确地称为“数据驱动铺货流水线 + 局部 AI worker + 人工审核台”，不是“Agent 化产品”。

---

## 2. 已实现功能与真实入口盘点

### 2.1 总览表

| 功能 | 真实入口 | 确定性逻辑 | LLM/VL/生图调用 | 人工节点 | 源码证据 |
|---|---|---|---|---|---|
| DB 状态机/事实源 | `python3 pipeline.py init/add/advance/list/kill` | 状态表、合法流转、events/reviews/keywords/credit_log | 无 | `record_review()` 被审核台调用 | `pipeline.py:2-23`, `pipeline.py:25-66`, `pipeline.py:68-102`, `pipeline.py:138-211` |
| 关键词库/选品 | `python3 keyword_library.py build/refresh/stats`; `python3 select_products.py --n` | 类目黑名单、词级漏斗、TTL、已消费去重、候选导出 | Nexscope API 非 LLM | 阈值校准需人工 | `keyword_library.py:2-14`, `keyword_library.py:23-47`, `keyword_library.py:115-135`, `select_products.py:2-13`, `select_products.py:29-65` |
| Nexscope 积分熔断 | `nexscope_client.call()` | 今日消耗估算、日熔断、credit_log 记账 | 外部 API，无 LLM | 无 | `nexscope_client.py:2-10`, `nexscope_client.py:30-57` |
| 找货/1688 | `python3 sourcing_agent.py [--limit/--image/--vl/--raw]` | 1688 skill 调用、销量/相似度排序、图搜失败降级文字搜 | qwen3-vl 识别采购关键词、同一性对拍 | 0.80-0.90 相似度标记 review，但无独立审核队列 | `sourcing_agent.py:2-13`, `sourcing_agent.py:36-50`, `sourcing_agent.py:73-93`, `sourcing_agent.py:110-154`, `sourcing_agent.py:189-294`, `sourcing_agent.py:297-336` |
| 趋势检查 | `python3 trend_check.py [--dry-run]` | 在监状态过滤、基线对比、跌幅告警写 events | Nexscope API，无 LLM | 告警只写事件，不自动改状态 | `trend_check.py:2-16`, `trend_check.py:19-23`, `trend_check.py:26-72` |
| 经济测算/定价 | `pricing.calc()`；审核台 `/api/price` | 毛子 ERP 公式、佣金档迭代、物流档、非正分母报错 | 无 | 审核台人工输入尺寸/重量/费率并保存 | `pricing.py:2-19`, `pricing.py:26-31`, `pricing.py:38-52`, `pricing.py:59-114`, `review_server.py:778-800`, `listing_edit.py:19-35` |
| 文案/spec | `python3 content_agent.py --query ... --title1688 ... --category-id ...` | 类目查表、禁忌词、长度、slot 完整性、数字溯源、need_confirm | qwen-plus 生成标题/描述/8-slot spec | 校验问题强制人工确认 | `content_agent.py:2-14`, `content_agent.py:74-107`, `content_agent.py:110-160`, `content_agent.py:163-190` |
| 尺寸 OCR | `python3 dimensions.py <image>`；被 image_worker 调用 | 失败返回空，作为 slot7 来源之一 | qwen3-vl-plus 抽取尺寸标注 | 无；人工尺寸更高信任级 | `dimensions.py:2-13`, `dimensions.py:27-57`, `image_worker.py:192-207` |
| 生图/套图 | `python3 image_worker.py --spec ... --image ... --out-dir ... [--only]` | prompt 模板渲染、slot2 白底原图优先、文字闸、slot7 数字闸、slots-report | qwen-image-3.0 I2I；qwen3-vl-plus 文字检测 | 局部重生由人工触发 | `image_worker.py:2-18`, `image_worker.py:38-74`, `image_worker.py:84-108`, `image_worker.py:111-158`, `image_worker.py:192-339` |
| 本地贴字 | image_worker 调用 `text_overlay` | PIL 字体、换行、颜色、芯片/标签绘制 | 无 | 无 | `text_overlay.py:23-91`, `text_overlay.py:124-231` |
| 审核台 | `python3 review_server.py [port]` | board/review/trace/API 路由、media 防目录穿越、死因枚举、上架编辑校验 | 间接调用 image_worker 生成 slot7；Ozon 属性 API | 图审：approve/regenerate/kill；slot7 人工尺寸；上架编辑 | `review_server.py:1-13`, `review_server.py:501-547`, `review_server.py:550-681`, `review_server.py:748-818`, `review_server.py:821-924` |
| 上架编辑 | 审核台 `/edit`; `listing_edit.save()` | 标题/详情/尺寸/重量校验、spec 回写、DB 镜像、清 need_confirm | 无 | 人工编辑即确认 | `listing_edit.py:2-10`, `listing_edit.py:19-75`, `listing_edit.py:78-87` |
| Ozon 属性 API | `ozon_client.get_attributes/get_attr_values` | 类目 ID 解析、属性缓存、错误包装 | 无 | 字典属性由编辑器人工选择 | `ozon_client.py:2-12`, `ozon_client.py:25-41`, `ozon_client.py:44-88` |
| 上架 worker | `python3 listing_worker.py --pid N [--dry-run]` | approved/listed 状态校验、payload 组装、必填校验、OSS 上传、import 轮询、幂等 offer_id | 无 | 需人工编辑后才能上架；dry-run 可验 payload | `listing_worker.py:2-13`, `listing_worker.py:32-66`, `listing_worker.py:69-85`, `listing_worker.py:88-134` |
| artifacts 同步/恢复 | `python3 sync_to_db.py` | candidates/sourcing/spec/gen 幂等回填、去重、状态推进、不回头 | 无 | trend/econ 跳过写明事件 | `sync_to_db.py:2-10`, `sync_to_db.py:47-185` |
| 测试 | `python3 -m pytest ...` | fixture 驱动，不消耗积分；覆盖闸门、定价、上架 payload、编辑器 | Mock 模型/外部调用 | 无 | `tests/test_identity_gate.py:1-120`, `tests/test_nexscope_flow.py:1-122`, `tests/test_pricing.py:1-97`, `tests/test_listing_worker.py:1-72`, `tests/test_listing_edit.py:1-88` |

### 2.2 调用关系梳理（源码验证版）

1. **选品链路**  
   `keyword_library.build()` 调 Nexscope 建库并写 `keywords`/`harvested_seeds`（`keyword_library.py:90-112`）；`select_products.main()` 消费合格词并导出 candidates 与 image-search tasks（`select_products.py:29-65`）。

2. **找货链路**  
   `sourcing_agent.main()` 读取最新 `image-search-tasks-*.json`，默认走 `search_vl()`（`sourcing_agent.py:297-336`）。`search_vl()` 用 VL 识别关键词，然后调用 1688 图搜脚本/HTTP，并按相似度决定 auto/review/no_source 或降级文字搜（`sourcing_agent.py:245-294`）。文字搜路径可再走 `identity_gate()`，逐个供应商调用 `vl_same_product()` 做视觉同一性确认（`sourcing_agent.py:110-186`）。

3. **内容/生图链路**  
   `content_agent.main()` 查本地类目树、调用 `llm_spec()`、执行 `validate()`，把问题写入 `_meta.validation` 并设置 `need_confirm`（`content_agent.py:163-190`）。`image_worker.generate_slots()` 读取 spec，调用 `dimensions.extract_dimensions()` 增强 slot7 来源，逐 slot 渲染 prompt、调用 qwen-image、再用 VL 文字闸和 PIL 贴字（`image_worker.py:192-339`）。

4. **审核/编辑/上架链路**  
   `review_server.review()` 从 DB 取 `review_pending` 和 `imaging` 队列，渲染 8-slot、校验结果和 approve/regenerate/kill 表单（`review_server.py:550-681`）。POST `/action` 调 `P.record_review()`，写 reviews 并推进状态（`review_server.py:868-892`, `pipeline.py:196-211`）。编辑器 POST `/edit` 调 `listing_edit.save()`（`review_server.py:803-818`, `listing_edit.py:38-75`）。`listing_worker.list_product()` 只允许 `approved/listed`，读取 spec 与人工编辑，上传图片到 OSS，调用 Ozon import 并轮询结果（`listing_worker.py:88-134`）。

5. **恢复/状态回填链路**  
   `sync_to_db.sync()` 是当前“恢复/串联”的实际中心：它读取最新 candidates/sourcing/spec/gen artifacts，写 metrics，按可用 artifacts 推进状态，并对未实现的 trend/econ 阶段以 event 说明跳过（`sync_to_db.py:47-185`）。这不是调度器，只是幂等同步脚本。

---

## 3. 每类逻辑的边界：哪些不该交给 LLM，哪些适合 Agent

### 3.1 不应改成 LLM 决策的确定性计算/闸门

这些环节应保持规则/代码/数据表驱动，LLM 最多做解释或建议，不能做最终判定：

1. **状态流转**：合法状态表和 `cmd_advance()` 的校验是数据一致性边界，不能让 LLM 决定能否跨状态（`pipeline.py:25-44`, `pipeline.py:160-171`）。
2. **定价公式与费用拆分**：佣金、物流、费率分母、币种、尺寸重量都是财务计算；当前 `pricing.calc()` 有公式、迭代和异常保护，应继续确定性执行（`pricing.py:2-19`, `pricing.py:59-114`）。
3. **Nexscope 积分熔断**：额度防烧钱，必须由 `spent_today()`/`call()` 硬拦截（`nexscope_client.py:30-57`）。
4. **Ozon 上架必填校验**：无图片、无尺寸重量、无价格不能上架；`build_item()` 的 ValueError 是安全边界（`listing_worker.py:32-66`）。
5. **禁忌词、长度、数字溯源**：适合机械校验，不应相信 LLM 自觉遵守；当前 `validate()` 已这样做（`content_agent.py:110-160`）。
6. **slot7 尺寸数字闸**：尺寸是平台审核和客诉风险，应维持“人工 source=manual 或数字可溯源才生成”的硬规则（`image_worker.py:183-189`, `image_worker.py:319-330`, `review_server.py:515-547`）。
7. **字典属性值**：Ozon 字典属性必须来自 API 字典值，不能让 LLM 自由填写（`ozon_client.py:79-88`, `docs/research/ozon-listing-api.md:39-49`）。
8. **OSS/Ozon 写操作权限**：真实上架和图片上传是外部写操作，必须受状态、人工确认、dry-run/限流保护；不能由 Agent 无约束调用（`listing_worker.py:69-134`）。

### 3.2 适合 Agent 规划/编排的环节

这些环节不是让 LLM“拍脑袋决策”，而是让 Agent 作为编排者选择已有工具、补齐缺失输入、触发人工节点：

1. **候选品处理计划**：给一个候选词/竞品，决定下一步该找货、补类目、重查趋势、进入人工确认，还是终止。当前分散在 CLI 和 `sync_to_db` 的状态推进中（`sync_to_db.py:156-185`）。
2. **找货策略选择**：根据图搜结果和相似度，在 raw/vl/text fallback/人工确认之间选择；当前已有局部规则，但没有统一任务层记录原因（`sourcing_agent.py:229-294`）。
3. **素材生成重试策略**：根据 slot 失败类型，只重生失败 slot、改 prompt、或转人工；当前 `image_worker` 支持 `--only` 和文字闸重试一次，但策略硬编码在 worker 内（`image_worker.py:192-339`）。
4. **审核后处置**：打回原因可以映射到具体重生任务或阈值调整；当前只记录 reviews/eval 标签，未自动生成后续任务（`pipeline.py:196-211`, `review_server.py:868-892`）。
5. **上架前补件**：如果缺尺寸、重量、属性、价格，Agent 可创建 human task 并阻塞，而不是让用户在多个页面/脚本间找缺口；当前散落在编辑器和 `listing_worker` 校验中（`listing_edit.py:19-75`, `listing_worker.py:32-66`）。
6. **上架后监控计划**：趋势告警、内容分、库存/价格同步、rating-by-sku eval 适合纳入长期 agent loop；当前只实现部分趋势检查（`trend_check.py:26-72`, `docs/research/ozon-listing-api.md:70-75`）。

---

## 4. Agent 化缺口：按产品级能力逐项分析

### 4.1 Planning：缺“显式计划”和“下一步选择”

**现状**：每个脚本知道自己的局部流程；没有“针对一个 product/run 的 plan”。状态机只是阶段枚举，不存下一步任务、依赖、重试预算或计划变更理由（`pipeline.py:25-44`, `sync_to_db.py:156-185`）。

**后果**：

- 用户需要知道何时跑 `select_products.py`、`sourcing_agent.py`、`content_agent.py`、`image_worker.py`、`sync_to_db.py`、`review_server.py`、`listing_worker.py`。
- 失败后系统不能自动判断“应重试同一工具、换 fallback、等人工，还是 kill”。

**最小补法**：新增一个只写 DB 的 `agent_runner.py plan/step`，读取 `products.state + metrics_json + artifacts`，生成并执行单步任务。不要重写 worker；把现有 CLI/函数当工具。

### 4.2 Tool abstraction：外部能力硬编码，缺统一工具协议

**现状**：

- 百炼 chat/image/VL 调用分别出现在 `content_agent.py`、`dimensions.py`、`image_worker.py`、`sourcing_agent.py`（`content_agent.py:81-107`, `dimensions.py:27-57`, `image_worker.py:84-141`, `sourcing_agent.py:110-139`, `sourcing_agent.py:189-226`）。
- Ozon、Nexscope、OSS、1688 skill 各自有不同调用和错误格式（`nexscope_client.py:38-57`, `ozon_client.py:25-32`, `listing_worker.py:69-85`, `sourcing_agent.py:36-50`）。

**缺口**：没有统一的 `ToolCall` 输入/输出 schema、重试分类、成本估算、幂等键、权限等级。

**最小补法**：先不引入大框架，只定义小表和薄封装：`tool_calls(id, product_id, tool, input_json, output_json, status, cost, error, started_at, finished_at)`。工具仍调用现有函数；只在 agent runner 外层记录。

### 4.3 Orchestration：脚本串联，不是可恢复任务编排

**现状**：`sync_to_db.py` 是 artifacts → DB 桥，并能不回头推进状态，但它不调 worker、不管理并发、不做 backoff（`sync_to_db.py:2-10`, `sync_to_db.py:47-185`）。审核台有局部 action，但不是全链路 orchestrator（`review_server.py:821-924`）。

**缺口**：缺 job queue、lease/lock、attempt、timeout、重试策略、幂等 key、阶段依赖。

**最小补法**：在 SQLite 加 `tasks` 表即可，不需要 Celery：`pending/running/succeeded/failed/waiting_human`，字段含 `product_id, kind, payload_json, attempts, not_before, locked_at`。先单进程 runner，后续再并发。

### 4.4 State / Memory：有状态机，但 memory 粒度不足

**现状**：状态在 `products.state`；事件在 `events`；审核标签在 `reviews`；业务快照混在 `metrics_json`；prompt_version 有列但 worker 记录不统一（`pipeline.py:68-102`）。spec、图片、报告存在文件系统（`sync_to_db.py:47-155`）。

**缺口**：

- 缺统一 run_id/attempt_id，无法把一次 LLM 输出、prompt、模型版本、输入图、成本、人工决策串成完整 trace。
- `metrics_json` 越来越像万能袋，后期难以查询和评估。

**最小补法**：保留 `metrics_json`，但新增 `runs/tasks/tool_calls/artifacts/evals` 几个窄表；先只写关键字段，不迁移旧数据。

### 4.5 Human-in-the-loop：有人工节点，但策略不可配置

**现状**：图审 approve/regenerate/kill 已写 reviews 并推进状态（`pipeline.py:196-211`, `review_server.py:550-681`）；slot7 可人工输入尺寸并 source=manual（`review_server.py:515-547`）；上架编辑保存即人工确认（`listing_edit.py:38-75`）。

**缺口**：

- 人工任务没有统一队列和 SLA；不同人工动作散在 review 页面、slot7 modal、编辑器、上架前错误里。
- 没有“何时必须人工、何时抽审”的策略层。
- 打回原因没有结构化映射到具体重生策略，只是状态回到 imaging（`pipeline.py:196-211`）。

**最小补法**：把 `reviews` 扩展或新增 `human_tasks`：`kind=image_review/listing_edit/slot7_dimension/source_confirm/category_confirm`，所有人工阻塞都进同一个队列；review_server 只是它的一个 UI。

### 4.6 Observability / Eval：有 eval 雏形，缺指标闭环

**现状**：reviews 表已记录审核 decision/reason，可作为 eval golden set（`pipeline.py:86-96`, `pipeline.py:196-211`）；slots-report 记录单次生图结果和成本（`image_worker.py:331-339`）；Nexscope 记 credit_log（`pipeline.py:97-101`, `nexscope_client.py:38-57`）；测试覆盖多个回归事故（`tests/test_identity_gate.py:1-120`, `tests/test_pricing.py:1-97`）。

**缺口**：

- 缺 dashboard 指标：每阶段通过率、每 prompt 版本通过率、每 slot 失败率、平均成本、从发现到上架耗时。
- 缺模型输出离线 eval：LLM 文案违规率、VL 同一性误杀/漏杀、图片文字闸误判、slot7 尺寸准确率。
- prompt_version 未贯穿所有 content/image outputs。

**最小补法**：先基于现有 events/reviews/tool_calls 建一个只读 `eval_report.py`，输出 Markdown/CSV；不要先上复杂 APM。

### 4.7 Failure recovery：有幂等种子，但缺系统化失败恢复

**现状**：

- DB 状态合法流转防手写错（`pipeline.py:25-44`, `pipeline.py:160-171`）。
- `sync_to_db.py` 重复运行安全，去重、不复活 dead、同货源原位更新（`sync_to_db.py:87-155`）。
- `image_worker` 支持局部 `--only` 重生，不覆盖整卡报告（`image_worker.py:192-339`）。
- `listing_worker` 使用稳定 `offer_id=ozp-{pid}`，重跑更新同一商品（`listing_worker.py:2-13`, `listing_worker.py:32-66`）。

**缺口**：

- 没有统一 retry/backoff/poison task/dead-letter。
- 外部 API 异常分类不一致；有的异常返回空，有的 raise，有的按保守方向处理（例如 dimensions 失败返回空，image 文本检测异常按有文字处理，Ozon HTTPError 包 RuntimeError；`dimensions.py:27-57`, `image_worker.py:111-141`, `ozon_client.py:25-32`）。
- 缺断点恢复时“上一次失败在哪个 tool_call、输入是什么”的查询入口。

**最小补法**：task + tool_call 表记录 attempt；runner 根据错误类型决定 retry、wait_human 或 kill。先覆盖付费/写操作/生图三个高风险工具。

### 4.8 权限/安全：有局部保护，缺产品级权限模型

**现状**：

- 配置从 `.env` 读入模块常量（`config.py:1-20`）；本次未读取 `.env`。
- Nexscope 有日积分熔断（`nexscope_client.py:30-57`）。
- review_server 绑定 127.0.0.1，media 路由有目录穿越防护（`review_server.py:821-857`, `review_server.py:923-924`）。
- listing_worker 有 `--dry-run`，且真实上架前要求 approved/listing_edits/图片/尺寸重量/价格（`listing_worker.py:88-134`）。

**缺口**：

- 没有工具权限等级：read-only、paid-read、image-generate、external-write、listing-write。
- 没有“真实上架二次确认/每日上架上限/店铺健康保险丝”的代码实现；ROADMAP 只列规划（`ROADMAP.md:58-69`, `ROADMAP.md:71-90`）。
- `.env` loader 会直接读文件并要求所有 key 存在，导入模块即可触发；这对测试/只读分析不友好，也增加误触外部能力风险（`config.py:5-20`）。

**最小补法**：在 agent runner 层做权限门，不先改所有 worker：默认只允许 read/dry-run；`--allow-external-write` 才能执行 OSS/Ozon import；把 listing task 设为必须人工 approve 后单步执行。

---

## 5. 最小可行目标架构（MVA：Minimum Viable Agent）

目标不是重写业务，而是在现有脚本外加一层可恢复、可审计、可控权的 Agent 编排壳。

```text
[Agent Runner / Planner]
    |
    | reads/writes
    v
SQLite: products / events / reviews / keywords / credit_log
        + tasks / tool_calls / artifacts / human_tasks / eval_runs
    |
    +-- Tools（薄适配，不重写业务）
        |-- select_products / keyword_library / trend_check
        |-- sourcing_agent.search_vl/search_text
        |-- content_agent.llm_spec + validate
        |-- image_worker.generate_slots
        |-- pricing.calc
        |-- listing_edit.save / ozon_client / listing_worker(dry-run/import)
        |-- sync_to_db（短期兼容旧 artifacts）
    |
    +-- Human UI
        |-- review_server 继续服务 board/review/editor/trace
        |-- 新增 human_tasks 队列视图
```

### 5.1 最小新增概念

1. **Task**：要做什么，如 `source_product`, `generate_spec`, `generate_images`, `review_image`, `listing_edit`, `list_product`。
2. **ToolCall**：一次真实工具调用的输入输出、成本、错误和模型版本。
3. **Artifact**：spec、图片目录、slots-report、candidate/sourcing report 的路径和哈希。
4. **HumanTask**：需要人处理的阻塞项，结果回写到 reviews/listing_edits/events。
5. **Policy**：哪些状态可自动执行，哪些必须人工，哪些需要权限开关。

### 5.2 目标状态流（保持现有状态名）

- `discovered → scored`：关键词漏斗确定性完成。
- `scored → sourced`：Agent 调找货工具；VL 同一性闸是工具内部校验；低置信进入 `human_task: source_confirm`。
- `sourced → checked`：趋势检查工具完成；告警进入人工，不让 LLM 判定市场好坏。
- `checked → profitable`：定价/单位经济确定性计算；缺尺寸/重量时创建 `human_task: listing_edit_or_dims`。
- `profitable → imaging`：LLM 只生成 spec，机械校验不过则 `need_confirm`。
- `imaging → review_pending`：生图工具完成；失败 slot 生成局部重试 task。
- `review_pending → approved/imaging/dead`：人工图审。
- `approved → listed`：只允许 dry-run 自动；真实 import 需要人工确认 + 权限开关。

---

## 6. 分阶段迁移建议

### Phase 0：不改业务代码的盘点固化（当前即可做）

- 固化本报告中的模块边界，避免把定价、状态、上架校验交给 LLM。
- 保持 `review_server.py`、`listing_worker.py`、`image_worker.py` 的现有入口不变。
- 只增加文档和运行手册：哪些命令是只读、哪些会付费、哪些会外部写。

### Phase 1：加最小 task/tool_call 记录层

最小代码范围：新增一个迁移脚本/runner，不碰业务 worker。

- 新增 SQLite 表：`tasks`, `tool_calls`, `artifacts`, `human_tasks`。
- `agent_runner.py step --pid N`：一次只执行一个安全 task。
- 所有外部调用先只包三类：Nexscope、百炼图/文/VL、Ozon/OSS。业务函数不大改，只在外层记录输入输出摘要和错误。
- 默认权限：不允许真实上架；listing 只跑 `--dry-run`。

验收：任一商品从 `scored` 到 `review_pending`，每步可在 DB 查到 task/tool_call/artifact。

### Phase 2：把人工节点统一成 human_tasks

- 将图审、slot7 尺寸、上架编辑、货源低置信确认都抽象成 human task。
- `review_server` 增加一个统一“待我处理”视图；原 review/editor 页面继续复用。
- 打回原因映射为后续任务：如“文案错误”→ generate_spec；“商品形变”→ regenerate specific slots；“侵权风险”→ kill/category/source review。

验收：所有阻塞都能在一个队列看到，处理后自动解锁下一 task。

### Phase 3：失败恢复和评估闭环

- task attempts/backoff/dead-letter。
- eval 报表：按 prompt_version、slot、类目、供应商统计通过率/成本/失败原因。
- 把 `reviews` 作为 golden set 反哺 prompt 和阈值，但阈值变更必须人工批准。

验收：一周运行后能回答“哪个 prompt/slot/类目最贵、最容易失败、为什么”。

### Phase 4：产品级 Agent 编排

- Planner 从“硬编码规则”升级为“LLM 建议 + 规则执行”：LLM 只提出下一步候选和解释，最终执行由 policy engine 校验。
- 增加账户健康/日上架/预算/429 保险丝。
- 支持 cron/daemon，每日自动推进到人工审核队列。
- 上架后接入 rating-by-sku、库存、价格、趋势，形成持续运营 loop。

验收：无人值守地产生待审队列，人只做审核/例外处理；真实外部写操作仍需权限和保险丝。

---

## 7. 风险与不确定性

1. **trend/econ 状态名已存在，但自动闸并未完整产品化**：`sync_to_db.py` 目前会用 event 明示跳过 trend/econ（`sync_to_db.py:156-185`）；`trend_check.py` 已实现告警但不是主流水线强闸（`trend_check.py:26-72`）；定价更多在审核台人工计算器中（`review_server.py:778-800`）。
2. **真实上架链路存在，但不等于安全可批量无人值守**：`listing_worker.py` 有最小上架链路和 dry-run，但缺全局限流、429 backoff、每日创建上限、库存设置闭环（`listing_worker.py:88-134`, `docs/research/ozon-listing-api.md:57-68`）。
3. **模型调用质量需要 eval 数据支撑**：已有 reviews 和 tests，但还没有按 prompt/model/version 的自动评估报表（`pipeline.py:86-96`, `tests/test_identity_gate.py:1-120`）。
4. **凭证加载模式偏脚本化**：导入 `config.py` 会读取 `.env` 并要求全部 key 存在（`config.py:5-20`）；产品化时应延迟加载并按工具权限读取。
5. **知识图谱是定位辅助，不是最终证据**：本报告关键事实已回到源码路径+行号验证；图谱用于发现模块和入口。

---

## 8. 最终结论

- **现状评估**：不是完整 AI Agent 产品；是一个已经比较扎实的“普通 Python 流程 + 局部 AI 调用 + 人工审核台 + SQLite 状态机”。它比一次性脚本强很多，因为有状态、事件、审核标签、幂等同步和关键闸门；但缺 Agent 产品最关键的 planner、统一工具协议、任务编排、可恢复执行、权限和 eval/observability。
- **演进原则**：不要把确定性计算改成 LLM 决策；应把 LLM 放在“生成、解释、候选计划建议”位置，把状态、费用、上架、合规、权限都留给规则和人工确认。
- **最小可行架构**：保留现有 worker，外加 `tasks/tool_calls/human_tasks/artifacts` 和一个单进程 `agent_runner`，先记录和编排，再逐步自动化。
- **最小迁移路径**：先不重写业务代码；用 SQLite task queue 包住现有入口，统一人工节点与 tool_call 记录，最后再引入 planner 和策略化权限。
