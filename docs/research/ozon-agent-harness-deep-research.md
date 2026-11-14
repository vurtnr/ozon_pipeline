# Research: ozon-pipeline Agent Harness 持续运行架构深度研究

> 调研日期：2026-11-12（续作完成）。方法：先读 `docs/research/ozon-agent-architecture-assessment.md`、`docs/research/ozon-agent-github-frameworks.md`、`ROADMAP.md`，再回源码逐文件验证现状（pipeline.py / sync_to_db.py / keyword_library.py / trend_check.py / listing_worker.py / image_worker.py，并确认 `agent_runner.py` 不存在）；外部事实仅引官方文档/官方镜像/一手 API 资料。未读 `.env`，未改业务代码，未执行任何外部 API 写操作。个别来源（sqlite.org 官网直连）网络不可达时，改经检索服务获取官方页面原文并在文中标注。

---

## 0. 结论摘要

**推荐组合：自建 SQLite task runner（骨架）+ APScheduler（触发）+ PydanticAI（可选、渐进引入的 LLM 工具/结构化输出层）**。不引入 LangGraph/Temporal/Prefect/Dify 作为运行时，event bus 用 SQLite 表 + 轮询实现，不引入消息队列。Agent 形态是"规则 planner + 工具 + 确定性闸 + 人工审批"，LLM 只出现在生成、识别、建议位置，绝不自由循环。现状盘点确认：状态机/闸门/审核台/幂等种子已扎实，缺的是 `tasks/tool_calls/artifacts/human_tasks/signals/budgets/prompt_versions` 七张表和一个单进程 `agent_runner`——这是本报告全部设计的落点。

---

## 1. Agent Harness 选型

### 1.1 判定标准（本项目特殊性）

1. 业务链路是**显式状态机**（`pipeline.py:25-44` TRANSITIONS 表），每一步"下一步做什么"基本可由 `(state, metrics, artifacts)` 确定推出——不需要 LLM 每轮自主决定。
2. 单机、单人操作、首批 ≤20 卡、V2 目标日 100 卡（ROADMAP）——数据量级是 SQLite 级，不是集群级。
3. 高风险外部写（Ozon import、OSS 上传、付费生图）必须人工审批+熔断——HITL 是一等公民。
4. 现有 worker（content_agent/image_worker/sourcing_agent/pricing）是可复用资产——harness 的职责是**编排壳**，不是重写业务。

**明确不是让 LLM 自由循环**：LLM 的全部允许位置 = spec 生成、VL 同一性/尺寸识别、找货策略建议、打回原因→修复任务映射建议、prompt 修改建议、热词语义扩展建议。禁止位置 = 状态流转、定价计算、预算/熔断判定、工具权限授予、真实上架执行。policy engine 在 runner 层硬拦截（见 §2.11）。

### 1.2 候选对比（含既有结论 + 本轮官方文档复核）

| 维度 | 自建 SQLite runner | LangGraph | PydanticAI | Temporal | Prefect |
|---|---|---|---|---|---|
| 持续 scheduler/loop | 自写 tick + APScheduler | 无（需外挂） | 无（需外挂） | Cron + Workflow | Deployment/cron |
| durable orchestration | task 表 + lease + 幂等键（够用） | checkpointer 按 super-step 快照，SqliteSaver 偏本地开发，生产建议持久 checkpointer | 自身不负责持久；官方列 Temporal/DBOS/Prefect/Restate/AWS Lambda/Airflow 等后端 | 最强：workflow history + replay | 中强：task 事务化结果持久 |
| HITL | human_tasks 表（直观、贴合审核台） | `interrupt()` 暂停、恢复，官方支持 | deferred tools：`requires_approval=True`/`ApprovalRequired`，参数先校验再递延；`DeferredToolRequests/Results` 恢复 | Signal/Update/wait_condition | 非原生 |
| 工具接入现有函数 | 直接包现有函数，零迁移 | 需 LangChain 风格包装 | **最低成本**：typed tools + Pydantic 校验，model 无关（贴合 Qwen/百炼） | Activity 化，需 workflow/worker 结构 | `@task/@flow` 装饰 |
| 状态/记忆源冲突 | 无（复用 pipeline.db） | **重复建状态源**（checkpointer vs products.state） | 无（只做调用层） | 独立 history 存储 | 独立 server 存储 |
| observability/eval | 自建 eval_report（reviews 已是 golden set） | LangSmith 生态强 | Logfire/OTel + Pydantic Evals | Prometheus/OTel | UI 强 |
| 运维重量 | **零新服务** | 库轻但生态易变重 | 库轻；durability 后端才变重 | server+worker+namespace，重 | server/work pools，重 |
| 升级路径 | 平滑迁移到 Postgres/Temporal | 迁出成本中 | 可保留（Temporal/DBOS 后端官方支持） | — | — |

**支撑证据**：
- PydanticAI 官方：durable execution 需外接引擎（"run durably … on Temporal, DBOS, Prefect, Restate, AWS Lambda, Absurd, pgtask and Apache Airflow"）；deferred tools 明确支持"需用户先批准"的调用，且**参数先校验再递延**（"a tool call can only be deferred once its arguments have been validated"）；harness 提供 Permissions 能力。来源见 §9。
- LangGraph 官方：checkpointers 按 super-step 保存快照；`interrupt()` 暂停后"waits indefinitely until you resume"；time-travel 支持 replay/fork。SqliteSaver 生产边界沿用前次调研结论（官方 persistence 文档：生产用持久 checkpointer，SqliteSaver 偏本地开发）。
- Temporal/Prefect：沿用前次调研已核实的官方 README/docs（见 §9），本轮未重复抓取，标注为"前次一手来源"。

### 1.3 最终推荐组合与理由

**骨架：SQLite runner。** 每个候选都"能做"，但只有它能：零新服务、复用 pipeline.db 唯一事实源、贴合单机规模、失败模式可理解（一张表 + 一份 WAL 文件）。它的短板（单写者、无跨机恢复）在日百卡量级前不会成为瓶颈；升级触发条件显式写在 §8 风险 4。

**触发：APScheduler。** 官方支持 SQLAlchemyJobStore（表建在 SQLite 里，job 序列化持久）、cron trigger、`misfire_grace_time`（错过后宽限补跑）、`coalesce`（积压多次合并跑一次）、`max_instances`（防同 job 并发重叠）——恰好覆盖"持续运行"所需的全部语义，~50 行配置，不需要 Prefect server。

**LLM 层：PydanticAI（可选、渐进）。** 只用它的 agent/tool/结构化输出/evals 层，**不用它的 durability 后端**（那些后端正是我们要避免的重系统）。它的 deferred tools 模式（requires_approval + DeferredToolRequests/Results）与本项目 human_tasks 表语义一致，可作为 Phase 1.5 的实现捷径；不引入也不影响架构（human_tasks 表本身就是该模式的朴素实现）。MIT、model 无关，贴合百炼/Qwen。

**不引入**：LangGraph（checkpointer 与 products.state 双状态源，graph 重写风险）；Temporal/Prefect（运维重量 > 当前问题规模）；Dify（平台化重写，前次调研结论维持）。

### 1.4 event bus：不需要独立消息队列

判定：**不引入 Redis/Rabbit/Kafka。** `events` 表（已有）+ 新增 `signals` 表 + runner 每分钟 tick 轮询 = 事件总线。理由：单进程单写者（SQLite WAL 官方文档："only one writer at a time"，读者不阻塞写者）、日事件量级 <10³、SQLite 事务保证"事件产生+任务入队"原子性。升级触发：多机部署或日事件 >10⁴——那时通常同时该升级 Postgres，一并评估（§8）。

---

## 2. 持续运行架构细化

### 2.1 文字架构图

```text
                    ┌─────────────────────────────────────────────┐
                    │  APScheduler daemon（持久 jobstore=SQLite）   │
                    │  cron: 词库周建 / trend 周查 / signals 日采   │
                    │  every-1min: runner.tick / budget.reset      │
                    └──────────────┬──────────────────────────────┘
                                   │ 入队/触发
                                   v
  [外部信号源] ──collect──> signals 表 ──dedup/score──> tasks 表
  (Nexscope / Ozon Premium* / Wordstat* / 自店数据)      │
                                   │                     v
                    ┌──────────────v──────────────────────────────┐
                    │  agent_runner（单进程，N 并发 worker 线程）    │
                    │  1. 领取：pending 且 lease 过期，BEGIN IMMEDIATE│
                    │  2. policy gate：权限/预算/保险丝/人工审批检查  │
                    │  3. 执行 tool adapter（包现有函数，不重写）     │
                    │  4. 记录 tool_call(输入/输出/成本/模型/版本)   │
                    │  5. next_tasks() 规则 planner 生成后续任务     │
                    └──┬──────────┬──────────┬──────────┬─────────┘
                       v          v          v          v
                 pipeline.db   artifacts   human_tasks  保险丝
                 (products/    (文件系统+   (图审/slot7/  (budgets/
                  events/       sha256 索引) 编辑/货源/   日卡顶/429
                  keywords/                  prompt审批)  /账户健康)
                  reviews/credit_log)
                       │
                       v
                 review_server（现有 UI + "待我处理" human_tasks 视图 + 失败任务重放）

  LLM 允许出现的位置（全部经 tool adapter，输入输出落 tool_calls）：
    spec 生成 / VL 同一性 / 尺寸抽取 / 策略建议 / prompt 建议 / 热词扩展建议
  LLM 禁止位置（policy gate 硬拦截）：
    状态流转 / 定价 / 预算熔断 / 权限授予 / 真实上架执行
  * = 待开通/待验证数据源，未假设已存在
```

### 2.2 trigger/scheduler（APScheduler 职责表）

| job | 频率 | 行为 | 失败语义 |
|---|---|---|---|
| runner.tick | 1 min | 领取到期 task 执行（含 lease 回收） | 单 tick 失败不影响下轮 |
| signals.collect | 日 | 拉取各信号源→signals 表（dedup 后） | 源级隔离，单源失败写 event |
| keywords.build | 周 | Nexscope 类目种子轮换建库（已有 harvested_seeds 游标） | 预算内，熔断时顺延 |
| trend_check.run | 周（BATCH=5 批量） | 在监词重查对比基线（已有实现） | 告警写 events |
| premium_queries.pull | 日 | *待开通*：getProductQueriesDetails 采集自店搜索词 | 未开通则 job 不注册 |
| budgets.reset | 日 | budgets 周期重置 | 幂等 |
| backup.db | 日 | `VACUUM INTO` 快照（可选，P4） | — |

### 2.3 planner：规则优先，LLM 只做建议

`next_tasks(product, config) -> [Task]` 是**纯函数**，映射表驱动：

```text
scored        -> [source_product]                        # 找货
sourced       -> [trend_check_gate]                      # 软闸：告警则挂 human_task
sourced+trend -> [unit_econ_gate]                        # 硬闸：pricing.calc 确定性
profitable    -> [generate_spec] -> [generate_images]
imaging 失败   -> [regenerate_slots(only=failures)] ×1   # 再失败 -> human_task
review 打回    -> reason 映射: 文案错误->[generate_spec]；商品形变->[regenerate_slots]
                 侵权风险->[human_task(source_confirm)]；利润不足->[dead]
approved      -> [listing_dryrun] ->（人工批准后）[listing_import] 单步
listed        -> [post_launch_watch]（V2）
```

LLM 介入仅限两类：(a) 策略选择类任务内部（如找货 raw/vl/text fallback 已有局部规则，升级为"LLM 建议+规则执行"）；(b) 打回原因→任务映射的参数建议。两者输出都写 `suggestions`，经 policy 校验后才生成 task——**planner 主控永远是规则**。

### 2.4 task queue 与 DB 最小概念（七张新表）

```sql
tasks(
  id, product_id, kind,            -- source_product|trend_gate|econ_gate|generate_spec|
  payload_json,                    --  generate_images|review_dispatch|listing_dryrun|listing_import|
  state,                           --  signals_collect|prompt_eval|post_launch_watch
                                   --  pending|running|succeeded|failed|waiting_human|dead
  attempts, max_attempts,
  idempotency_key UNIQUE,          -- kind:product_id:hash(payload_json)
  priority, not_before,            -- backoff 下次可跑时间
  lease_until, locked_by,          -- 租约：worker 心跳续租；崩溃后过期自动可被领取
  cost_estimate, error, parent_task_id, dead_letter_of,
  created_at, updated_at)

tool_calls(id, task_id, product_id, tool, permission_level,
  input_json, output_json, model, prompt_version, effective_prompt,
  cost, error, started_at, finished_at)

artifacts(id, product_id, task_id, kind, path, sha256, meta_json, created_at)

human_tasks(id, product_id, kind,   -- image_review|slot7_dimension|listing_edit|
  payload_json, sla_hours,          --  source_confirm|category_confirm|prompt_approval|
  state, decision, decided_by,      --  threshold_approval
  decided_at, created_at)

signals(id, source, kind, entity, payload_json, region,
  credibility, observed_at, expires_at, dedup_key UNIQUE, score)

budgets(id, scope, scope_ref, period, spent, limit, updated_at)
  -- scope: global|tool:nexscope|tool:bailian|listing:cards
prompt_versions(id, slot, template_text, model, status,   -- draft|canary|active|retired
  approved_by, metrics_json, created_at)
```

追踪层级：**run**（一次 tick 批次，可用 APScheduler job id + 时间戳标识，不单建表）→ **task**（tasks 行）→ **attempt**（tasks.attempts 计数，明细并入 tool_calls 序列）→ **tool_call**（每次外部调用一行）。刻意不做 run 表：tick 批次没有独立生命周期语义，避免过度设计；需要时由 tasks.created_at 分组即得。

### 2.5 幂等 / 并发 / lease

- **幂等**：idempotency_key 唯一约束防重复入队；外部写沿用已验证的 `offer_id=ozp-{pid}`（listing_worker 重跑=更新同一商品）；sync_to_db 的"不回头、同货源原位更新"原则保持。
- **并发领取**：`BEGIN IMMEDIATE; UPDATE tasks SET state='running', lease_until=:now+ttl, locked_by=:w WHERE id=:id AND state IN ('pending','running') AND (lease_until IS NULL OR lease_until < :now)` —— 依赖 SQLite 写串行化保证领取原子性。
- **lease**：worker 长任务每 30s 心跳续租；崩溃后 lease_until 过期即被其他 worker 回收。**SQLite WAL 事实（官方文档，本轮经检索服务取回原文）**："readers do not block writers and a writer does not block readers"，但 "since there is only one WAL file, there can only be one writer at a time"；且崩溃恢复、最后连接关闭等场景可能返回 SQLITE_BUSY——所以：写事务必须短、busy_timeout 必设、并发 worker 建议 2-4 个。
- **超时**：每 kind 配 timeout；付费工具（生图/找货）timeout 后 task 标 failed 并把 tool_call 标 error，不重试付费调用超过 max_attempts=2。

### 2.6 dead-letter

attempts≥max_attempts 或人工 kill → `state='dead'`，记 dead_letter_of 与 error 摘要。review_server 增"失败任务"视图：按 error 分类聚合 + 一键重放（state→pending，not_before=now，attempts 归零，原 task 保留为 dead_letter_of 指向）。付费失败/外部写失败默认不自动重放，必须人工点击。

### 2.7 审批权限

permission_level 五级：`read`（Nexscope 词查/类目树）→ `paid-read`（Nexscope 建库/类目热销）→ `image-gen`（百炼图/VL/chat）→ `external-write`（OSS 上传）→ `listing-write`（Ozon import/stocks/prices）。规则：

1. runner 默认只放行 read + image-gen + 已审批链路内的 paid-read；`--allow-external-write` 启动参数显式开启 external-write。
2. `listing-write` 永远需要：approved 状态 + human_task(listing_edit) 已确认 + human_task(listing_import) 批准 + 单步执行 + 日上限（§2.8）。
3. PydanticAI deferred tools（requires_approval=True）与 human_tasks 表一一对应：模型调用被递延 → human_tasks 落一行 → 审核台决定 → DeferredToolResults 回填恢复。不引入 PydanticAI 时，同一流程由 runner 检查 human_tasks 实现。

### 2.8 模型预算与店铺保险丝

- **预算**：budgets 表统一记账。nexscope_client 现有日熔断（spent_today 估算 + 硬拦截）保留为 tool:nexscope 的实现；百炼按 slots-report 实测成本累计进 tool:bailian；新增全局日预算熔断——超限后 runner 只执行 read 任务并挂 human_task 通知。
- **店铺保险丝**（ROADMAP V2 规划的具体化）：
  1. 日上架硬顶：启动时调 `/v4/product/info/limit` 取 `daily_create`（本店后台实测 100，官方镜像确认该 endpoint 返回 total/daily_create/daily_update），运行中计数比对，超顶挂 human_task。
  2. 429 退避：ozon_client 统一捕获，指数退避 + 连续 N 次 429 → 全局暂停 listing 类 task。
  3. 账户健康：取消率/迟发率/审核驳回率周检（V1 人工，V2 自动），任一异常 → 自动暂停上新 task 入队，只保留人工处置通道。

### 2.9 graceful shutdown / recovery

- **shutdown**：SIGTERM → runner 停止领取新 task → 等待在跑 task 完成或 lease 到期 → APScheduler `shutdown(wait=True)` → 退出。在跑的付费调用不可中断（tool_call 落库为准）。
- **recovery**：重启后先跑 lease 回收（所有 running 且 lease 过期 → pending）；幂等键 + offer_id 保证重跑无副作用重复。trend_check/sync_to_db 已验证重复运行安全，同一原则推广到全部 task。

### 2.10 核心状态流

```text
product:   discovered→scored→sourced→checked→profitable→imaging→review_pending
           →approved→listed→syncing→winning→…（TRANSITIONS 不变，含 dead 旁路）
task:      pending→running→succeeded
                  ├→failed→pending(backoff: not_before=now+2^n·base)…→dead
                  └→waiting_human→(human 决定)→pending | succeeded | dead
human_task: open→decided(approve/reject/edit)→回写 reviews/listing_edits/events 并解锁后继 task
signal:    raw(collected)→scored→nominated(生成关键词任务)→consumed|expired
prompt_version: draft→golden_pass→canary→active→retired（回滚=active 指回旧版）
```

---

## 3. 业务环节 AI 介入矩阵

图例：闸=确定性/人工硬边界；建议=LLM 输出仅候选+理由，经 policy 校验。`schema` 给核心输入输出（省略 `_meta`）。触发器均指 agent_runner task kind。

| # | 环节 | 现有确定性功能 | AI 可做 | AI 不可做（闸） | 触发器 | 评估指标 | 人工边界 | 失败降级 |
|---|---|---|---|---|---|---|---|---|
| 1 | 市场/季节/节日/突发感知 | 无（仅 trend_check 词级基线对比） | 节假日配置表匹配、突发事件信号解析建议 | 事件本身不触发任何上架；只提名 | signals.collect | 事件→候选→上架转化率、误报率 | 新信号源接入需审批；节假日表人工维护 | 解析失败→信号标 low credibility 挂起 |
| 2 | 热词库检索与扩展 | keyword_library 漏斗+TTL+去重（确定性） | LLM 语义扩展（同义/场景词变体）| 扩展词必须过 Nexscope/Wordstat 指标验证才入库 | keywords.build / signals.nominate | 扩展词命中率（进候选比例）、零积分命中率 | 词库白名单/类目黑名单变更需审批 | API 失败→只用本地库（词级指标是慢变量，TTL 周级） |
| 3 | 选品 | select_products 词级漏斗+类目黑名单+已消费去重 | 候选解释、竞品摘要 | 漏斗阈值判定（数值比较） | scored 入队 | 候选→上架→出单命中率 | 阈值校准需审批（ROADMAP V3） | Nexscope 失败→顺延 not_before |
| 4 | 竞品/趋势 | trend_check 基线对比（跌超 -50% 量/-20% 价告警） | 告警归因摘要、竞品卡差异解读 | 趋势判定=数值对比；告警不自动改状态（V1 现状保持） | trend_check.run | 告警准确率（人工标注）、促销脉冲拦截率 | 处置（降 价/下架/kill）人工 | API 失败→跳过本轮，写 event |
| 5 | 1688 找货 | 1688 Ocean skill 图搜/文字搜、销量排序、图搜失败降级 | VL 识别采购关键词 | 货源选择最终确认（0.80-0.90 相似度→human_task） | source_product | 出货源率（实测 5/6）、采购词识别准确率 | 低置信必人工 | 图搜失败→VL→文字搜→human_task（现有三级降级保留） |
| 6 | 同一性 | identity_gate 相似度分档 | vl_same_product 视觉对拍 | 0.80-0.90 区间必须人工；<0.80 自动 no_source | source_product 内 | 误杀率/漏杀率（golden: tests/test_identity_gate + 人工标注） | 区间人工确认 | VL 异常→保守判 review（现有行为） |
| 7 | 单位经济 | pricing.calc 公式/佣金档迭代/非正分母保护 | 成本结构解释 | **全部**：佣金/物流/汇率/准备金计算，LLM 零介入 | econ_gate | 皂液器判负等回归用例（tests/test_pricing） | 费率参数人工输入/审批 | 缺尺寸重量→human_task(listing_edit)，不入此闸 |
| 8 | 文案 | 类目查表、禁忌词、长度、slot 完整性、数字溯源、need_confirm | qwen-plus 生成标题/描述/8-slot spec | validate() 机械校验不过→need_confirm 强制人工 | generate_spec | 违规率、need_confirm 率、人工修改率 | 校验问题必人工；俄语母语抽审 | LLM 失败→重试 1 次→human_task(人工写文案) |
| 9 | prompt 自动生成优化 | prompt 模板渲染（image_worker 内） | prompt 修改建议（diff+理由）、effective prompt 改写记录 | **激活/回滚必须审批**；文字双轨/数字溯源闸不变 | prompt_eval | 按 prompt_version 通过率/成本（§5） | prompt_approval human_task | golden 回归不过→版本留 draft |
| 10 | 生图 | prompt 渲染、slot2 白底原图优先、文字闸、slot7 数字闸、--only 局部重生 | qwen-image I2I 出图、VL 文字检测 | 文字闸异常按"有文字"处理（保守）；数字不可溯源→slot7 跳过 | generate_images | slot 一次通过率、单卡成本、文字闸误判 | 局部重生由人工触发（现有） | 失败 slot 重试 1 次→human_task |
| 11 | 图片质检 | 文字闸（VL）+ 数字闸 + slots-report | VL 辅助质检（形变/杂物描述） | **图审决定权人工**（approve/regenerate/kill） | review_dispatch | 打回率 by 原因枚举 | 全检起步（ROADMAP：图审全检） | VL 质检异常→跳过，只走人工 |
| 12 | 审核打回修复 | reviews 表 decision/reason 枚举（6 死因） | 打回原因→修复任务映射建议 | 映射结果由 planner 规则执行；修复不跳过闸 | review_dispatch 后继 | 修复一次通过率、平均修复时长 | 侵权风险类 kill 人工终审 | 映射无匹配→human_task(其他) |
| 13 | 上架属性映射 | ozon_client 类目/属性/字典值（禁止自由文本填字典） | 属性值候选建议（俄语→字典值匹配） | 字典值必须来自 API 字典；is_required 校验 | generate_spec / listing_dryrun | 单类目映射准确率（ROADMAP 验收 ≥90%） | 字典属性人工选择（现有编辑器） | 属性缺失→dry-run 校验拦截→human_task |
| 14 | 定价/库存 | pricing.calc；库存同步 **V1 不做**（ROADMAP 明确） | 价格建议（仅在 econ 通过后，且 Must 过准备金公式） | 定价公式；库存清零/恢复 V2 才自动化 | econ_gate / (V2) stocks_sync | 定价与实际成交偏差、断货响应时长 | 价格人工确认后 import | 无 |
| 15 | 上架后反馈 | trend_check 在监词告警；reviews 标签 | 评论/评分摘要、查询词报告解读 | 处置动作人工；Premium queries 未开通=数据盲区（如实标注） | post_launch_watch (V2) | winning 品识别延迟、告警→处置时长 | 下架/降价人工 | 数据源缺失→降级为仅 trend_check+reviews |

**贯穿规则**：所有 AI 输出带 model+prompt_version 落 tool_calls；所有"建议"类输出落 suggestions 不直接生效；所有指标可用 reviews（golden set）+ slots-report + credit_log 现有数据推导，P1 起用只读 `eval_report.py` 聚合。

---

## 4. 事件驱动选品深入设计

### 4.1 信号源分级（不假设 API 已存在）

| 级别 | 信号源 | 内容 | 可信度/时效 | 状态 |
|---|---|---|---|---|
| A 可信已有（生产可用） | Nexscope 关键词漏斗/类目热销 | 月搜/竞争/均价/退货率/top3 | 序数级可信；ROADMAP 登记销量估算误差 2.8–6.3×——只作排序不作绝对值；周级时效 | 已接入 |
| A | keyword_library 本地库 + trend_check 基线 | 词级慢变量 | 高（本地资产化） | 已接入 |
| A | Ozon 官方类目树（7,363 叶）+ 属性字典 | 类目/属性结构 | 高 | 已接入 |
| A | 自店 reviews/eval 标签 + credit_log | 人工判定、成本 | 高（一手事实） | 已接入 |
| B 官方存在、待开通/待验证 | Ozon Premium API `getProductQueries` / `getProductQueriesDetails` | 自店搜索词：queries_count/shows/clicks/ctr/avg_position/conversion_rate/query_frequency | 官方镜像（salacoste，本仓库惯用高置信镜像）；**需 Premium/Premium Plus 订阅**；ROADMAP V3 在评估 4,990₽ 档；本店订阅状态未确认 | 待开通 |
| B | `getAnalyticsData`（/v1/analytics/data） | 收入/加购/转化/类目位置等指标 | 镜像口径：无订阅仅近 3 个月数据且 1 次/分钟；Premium Plus 无限制 | 待验证（ endpoint 可先用无订阅档试） |
| B | Ozon 评论/评分按 SKU 接口 | 自店售后信号 | 官方（ozon-listing-api.md §70-75 已调研） | 待接线 |
| C 外部官方、未接入 | **Yandex Wordstat API** | 俄语搜索需求：`/v1/topRequests`、`/v1/dynamics`、`/v1/regions`、`/v1/getRegionsTree`（官方文档确认四方法，POST JSON，OAuth token + ClientId） | 官方一手；已迁入 Yandex Cloud Search API 体系（官方说明"所有能力现于 Yandex Search API 的 Wordstat API"）；配额由 Yandex Cloud 文档载明（同步请求按小时/秒计） | 未接入；俄语商品词相关性预期高，接入成本中等（OAuth+ClientId 申请流程官方有四步说明） |
| D 待验证/不建议依赖 | Google Trends | 全球趋势 | **无正式开放 API**：官方仅 alpha 且需申请审批（2025-07 起）；pytrends 为非官方且 GitHub 已归档（2025-04）——不可作为生产依赖 | 不建议 |
| D | Ozon 站外社媒/TG/新闻（突发事件） | 突发需求 | 无官方 API；LLM 解析噪声大 | PoC 级人工触发，不自动入队 |
| — | 俄罗斯节假日/季节表 | 新年(1/1-1/8)、祖国保卫者日(2/23)、妇女节(3/8)、五一/胜利日(5月)、俄罗斯日(6/12)、统一日(11/4)、开学季(8 月末) | 公开日历事实，无需 API | 手工维护为配置表（`data/ru_calendar.json`），提前 6-8 周提名 |

### 4.2 信号 schema 与处理管线

```text
采集(collect) -> 去重(dedup_key=hash(source,entity,params)) -> 评分
  score = credibility(0-1,源级) × exp(-λ·age_days)(λ≈0.1/日,节日事件λ更高)
          × region_fit(RU=1.0) × compliance_mask(EAC 黑名单类目=0)
  -> expires_at 到期作废 -> 每日预算内取 top-N 提名
```

**硬规则：信号永不直接创建上架任务，只提名候选热词。**

```text
signal(节日:3/8 妇女节) -> 候选热词生成
   ├─ 规则源：ru_calendar 配置的场景词模板（人工维护，可信）
   ├─ LLM 语义扩展：变体/场景/收礼人词（输出 suggestions，逐词待验证）
   └─ 合流 -> 逐词验证：keyword_library 本地命中（零积分）
        或 Nexscope 单词查询（付费，受 budgets 限额+日熔断）
   -> select_products 词级漏斗（确定性） -> 候选商品
   -> 趋势软闸（trend_check：告警→human_task，不自动 kill——V1 现状如实保留）
   -> 经济硬闸（pricing.calc：分母非正即拦，确定性）
   -> human_task(选品确认，首批全检) -> 进 found/找货链路
```

### 4.3 时效/地域/合规/预算细则

- **时效**：季节类信号提前 6-8 周；突发类 TTL≤14 天（expires_at）；词级指标 TTL 周级（keyword_library 已有）。
- **地域**：Ozon 主市场=俄罗斯；Wordstat `/v1/regions` 按区域拆分；signals.region 字段统一 `RU`（未来哈萨克等再扩）。
- **合规**：复用类目黑名单（EAC 误闯=ROADMAP 高风险项）+ content_agent 禁忌词表；新增 signals.compliance_mask 在评分阶段直接过滤；LLM 扩展词必须过机械校验（字符集/长度）再进验证。
- **预算**：每信号源每日采集配额（budgets: tool:nexscope 延用现有日熔断）；每日提名上限（默认 10 词，首批 ≤20 卡总闸兜底）；Premium 档未开通前 P2 只跑 A 级源 + 节假日表。

---

## 5. 生图 prompt 自优化深入设计

### 5.1 闭环

```text
生成：prompt_versions(active) 渲染 -> qwen-image I2I -> 文字闸/数字闸 -> slots-report
                                    │（记录 effective_prompt：官方 prompt 智能改写若启用，
                                     须把改写后实际 prompt 一并落 tool_calls，保可追溯）
反馈：slots-report（成本/跳过原因/文字闸重试）+ reviews（approve/regenerate/kill+死因枚举）
      -> eval_report.py 按 prompt_version×model×slot 聚合：一次通过率/打回率/单卡成本
优化：LLM 生成 prompt 修改建议（diff+理由，引用失败样本 evidence）-> prompt_versions(draft)
      -> golden set 离线回归（文字闸+数字闸自动分 + 人工抽审）
      -> 人工审批(prompt_approval) -> golden_pass -> canary(10% 新卡，按 product_id 哈希)
      -> 达最小样本(≥30 卡或 2 周) -> active；旧版 -> retired
回滚：canary 打回率超阈值(如 >active 版 1.5×) 自动退回 active 指针 + human_task 通知
```

### 5.2 golden set 与实验规则

- golden set 从 reviews 中 approve 样本抽样固定（首轮 ≥30 卡，覆盖 ≥3 类目），存输入（spec+1688 原图+prompt_version）与期望要点；每版本回归=同一批输入重跑+自动闸评分+人工抽审 10 张。
- 模型升级（如 qwen-image-3.0 → 3.0-pro）：官方文档确认两型号均支持文生图+编辑（I2I）、3.0-pro 支持负向提示词、单次最多 6 输出、2048×2048；升级必须作为新 prompt_version+model 组合走同一回归通道，不允许热切换。
- **AI 建议、确定性闸与人工审批的边界（重申）**：LLM 只产 diff 建议；文字双轨（俄语大字由 text_overlay 本地贴，AI 不画）、slot7 数字溯源、白底/合规检查三条确定性闸永不因 prompt 优化而绕过；激活/回滚必有人工审批记录。

---

## 6. 持续学习与 memory、防自污染

### 6.1 四层 memory

| 层 | 载体（全部已有或已列七表） | 生命周期 |
|---|---|---|
| 业务事实 | products/keywords/类目树/pricing 参数 | 长期，人工审批才可改 |
| 事件 | events + signals | 中期，带 TTL/衰减 |
| 短期执行状态 | tasks/tool_calls/artifacts/human_tasks | 单品全链路期间 |
| 长期 eval 经验 | reviews（golden set）+ eval_report 聚合 + prompt_versions.metrics + suggestions | 长期，只进"建议区" |

### 6.2 防错误经验自污染（六条硬规则）

1. **建议区隔离**：LLM 的一切"经验/阈值/prompt 修改"写 suggestions 表，schema 上无权 UPDATE 阈值常量、prompt_versions.status、类目白名单、kill_reason 枚举。
2. **审批即生效**：任何规则变更必须 human_task（prompt_approval/threshold_approval）批准，记录批准人+依据的 eval 数据指针。
3. **证据链强制**：经验条目必须关联 review_id/tool_call_id；无证据不入库；LLM 自评（模型给自己输出打分）不作为指标来源，只信人工标签与真实销量/退款。
4. **可作废**：经验条目带 valid 标志，每周人工抽 10 条审；发现污染批量置 invalid（不物理删，保审计）。
5. **写入预算**：每日 LLM 生成经验条数上限（默认 20），防自我强化循环刷量。
6. **回归护栏**：阈值变更后必须重跑受影响 golden 用例（tests/ 现有回归集 + eval_report），退化即自动回滚建议。

---

## 7. 分阶段实施与验收（含 V1 缺口如实处理）

| 阶段 | 内容 | 可验证验收 |
|---|---|---|
| **P0 PoC**（1-2 天） | 七表迁移（先 tasks/tool_calls/artifacts/human_tasks）+ `agent_runner step --pid` + 3 工具（Nexscope 只读、pricing.calc、mock LLM） | ① 任一商品 scored→review_pending 每步 DB 可查 task/tool_call/artifact；② kill -9 后重启，过期 lease 被回收，重跑幂等（无重复候选/生图）；③ 默认权限下 listing 类 task 被拒 |
| **P1 单商品全链路**（1 周） | 规则 planner 全链路推到 review_pending→approved→listed；human_tasks 统一队列；打回原因→任务映射 | ① 一条命令单商品 discovered→listed(dry-run)，人工只在审核台；② **trend/econ 缺口如实标注**：trend_check 保持"告警写 events 不改状态"（现状），econ 接 pricing.calc 硬闸，缺口以 event 明示不虚报；③ ≥3 种死因可自动生成修复任务；④ dry-run 通过后真实 import 走单步人工批准 |
| **P2 事件驱动选品**（1-2 周） | signals 表 + 节假日配置表 + A 级源采集 + 事件→词→品管线；Premium/Wordstat 若开通则接 B/C 级源（未开通则不接，报告标注盲区） | ① 注入测试事件→自动产出候选词→漏斗→过硬闸→审核台，全程 trace 可查；② 同事件重放零重复（dedup_key）；③ 词库预算熔断可演示；④ 信号→上架转化与误报率首版报表 |
| **P3 prompt 优化**（1 周） | prompt_versions + golden set ≥30 卡 + eval_report + 审批流 + canary | ① prompt 候选版本离线回归报告可对比旧版；② 未审批版本无法成为 active（权限测试）；③ canary 打回率超阈自动回滚（演示） |
| **P4 多并发持续运行**（1-2 周） | APScheduler daemon + 2-4 并发 worker + 全部保险丝 + eval 看板 + 日备份 | ① 连续 7 天无人值守，审核台唯一人工触点（ROADMAP V2 出口标准）；② 日卡硬顶/预算熔断/429 退避各触发一次并正确暂停；③ WAL 模式下 ≥3 worker 无死锁无重复领取；④ 任一 worker 崩溃自动恢复无副作用重复 |

**V1 缺口对齐（不夸大现状）**：trend 闸=软闸+告警+人工处置（现状如此，V2 才自动处置）；econ 闸在 P1 才接线（现为审核台人工计算器）；库存同步/价格监控 V1 明确不做（ROADMAP），上架后反馈在 Premium 未开通前仅 reviews+rating+trend_check 三源，数据盲区在报表中显式标注。

---

## 8. 风险与未验证假设

1. **Nexscope 数据为估算**（ROADMAP：误差 2.8–6.3×）——只作序数；真值回归标定未做，事件评分的 credibility 据此保守取值。
2. **Premium 订阅未确认**——B 级信号源可能整级不可用；P2 验收含降级路径（仅 A 级源）。
3. **Ozon 官方文档 antibot 403**——事实依赖镜像（salacoste GitHub 镜像/Apifox spec/官方 TG 存档），存在镜像滞后风险；关键 endpoint 首用时实测校验（仓库已有"实测纠正旧路径"先例）。
4. **SQLite 并发天花板**：官方 WAL 文档明确"only one writer at a time"，并列出崩溃恢复/连接关闭等 SQLITE_BUSY 边界场景；写事务必须短。>4-6 并发 worker、日任务 >10⁴、或多机部署 → 升级 Postgres 并重新评估 Temporal（届时 PydanticAI durability 后端可平接）。
5. **Wordstat 未接入**：词质量、配额消耗、费用、OAuth 申请周期均未验证；不阻塞主线（A 级源先行）。
6. **Google Trends 不可依赖**：官方 API 仅 alpha 申请制，pytrends 非官方且已归档——禁止进入生产依赖。
7. **qwen-image 官方 prompt 智能改写**可能与本项目自优化叠加导致实际生效 prompt 不可追溯——统一落 `effective_prompt` 字段；若改写行为不可控则在 tool adapter 中显式关闭。
8. **经验自污染**是长期最大风险：任何"自动改阈值"都会放大早期误差——六条硬规则（§6.2）是产品级约束，不是建议。
9. **单机无异地冗余**：P4 加入 `VACUUM INTO` 日备份；跨机容灾不在此版本范围。
10. **成本口径**：slots-report/credit_log 的成本为本地估算，未与百炼/Nexscope 账单核对过差异率。

## Missing evidence（本轮明确未验证项）

- Temporal/Prefect 官方页面本轮未重新抓取，沿用前次调研（ozon-agent-github-frameworks.md）已核实的一手来源，版本可能已有更新。
- DBOS 是否强制 Postgres 未验证（PydanticAI 官方仅确认其以数据库 checkpoint 实现持久化）；本报告未依赖此细节。
- Ozon Premium API 方法签名来自高置信官方镜像而非 docs.ozon.ru 原站（antibot），字段以首次实测为准。
- sqlite.org 直连失败，WAL 结论经检索服务取回官方页面原文（§2.5 已引原文），标注为间接获取的一手内容。
- Yandex Wordstat 具体商品词质量与配额消耗：无实测数据。

## Sources

**保留（一手/官方）**
- 现状源码（本仓库直接读取）：pipeline.py（状态机/表结构）、sync_to_db.py（trend/econ 跳过明示）、keyword_library.py、trend_check.py（告警不改状态）、listing_worker.py（dry-run/offer_id 幂等）、image_worker.py（双闸/slots-report）；`agent_runner.py` 确认不存在（ENOENT）。
- PydanticAI 官方：Durable Execution overview（https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/）；Deferred Tools（https://pydantic.dev/docs/ai/deferred-tools/）；Harness Permissions（https://pydantic.dev/docs/ai/harness/permissions/）；Request Input（https://pydantic.dev/docs/ai/capabilities/request-input/）。
- LangGraph 官方：Checkpointers（https://docs.langchain.com/oss/python/langgraph/checkpointers）；Interrupts（https://docs.langchain.com/oss/python/langgraph/interrupts）；Use time-travel（https://docs.langchain.com/oss/python/langgraph/use-time-travel）。
- APScheduler 官方：User guide（https://apscheduler.readthedocs.io/en/stable/userguide.html）；SQLAlchemyJobStore（https://apscheduler.readthedocs.io/en/stable/modules/jobstores/sqlalchemy.html）；CronTrigger（https://apscheduler.readthedocs.io/en/stable/modules/triggers/cron.html）。
- SQLite 官方：Write-Ahead Logging（https://sqlite.org/wal.html）；Isolation（https://sqlite.org/isolation.html）。
- Ozon Seller API（官方镜像，本仓库既定高置信源）：Premium API——getAnalyticsData/getProductQueries/getProductQueriesDetails 签名与订阅限制（https://salacoste.github.io/ozon-daytona-seller-api/daytona-ozon-api-docs/categories/26-premium.md）；官方 TG 公告频道（https://t.me/s/OzonSellerAPI）；社区维护 460 方法索引（https://github.com/etozhearut/ozon-seller-api）。
- Yandex Wordstat 官方：API 结构与四方法（https://yandex.com/support2/wordstat/en/content/api-structure）；接入步骤（https://yandex.ru/support2/wordstat/en/content/api-wordstat）；配额（https://github.com/yandex-cloud/docs/blob/master/en/_includes/search-api-limits.md）。
- 阿里云 Model Studio 官方：qwen-image 文生图/编辑 API（https://www.alibabacloud.com/help/en/model-studio/qwen-image-generation-and-editing-api-reference）；模型矩阵 qwen-image-3.0/3.0-pro、负向提示词、6 输出、prompt 智能改写（https://docs.modelstudio.console.alibabacloud.com/en/model-studio/image-model）。
- Temporal 官方（前次一手来源）：Python SDK README（https://raw.githubusercontent.com/temporalio/sdk-python/main/README.md）；message passing（https://docs.temporal.io/develop/python/workflows/message-passing）；observability（https://docs.temporal.io/develop/python/platform/observability）。
- Prefect 官方（前次一手来源）：opensource（https://www.prefect.io/opensource）；flows（https://www.prefect.io/docs/v3/concepts/flows）；work pools（https://www.prefect.io/docs/v3/deploy/infrastructure-concepts/work-pools）。
- 本仓库既有调研（源码级现状证据）：docs/research/ozon-agent-architecture-assessment.md；docs/research/ozon-agent-github-frameworks.md；docs/research/ozon-listing-api.md；ROADMAP.md。

**拒绝/降权**
- pytrends 及 Google Trends 三方综述（ctriolab/scrapfly/sekin）——仅用于确认"官方 API 仅 alpha、pytrends 已归档"这一负面事实，不作为设计输入。
- wiki.optimism.ru / 4eker.ru / habr / 博客类 Ozon 分析——二手，仅交叉参考，不采信。
- 各类"Agent 框架榜单"——任务明令禁止，未使用。

## Next steps

1. P0 PoC 直接开工（七表迁移 + agent_runner step + 3 工具），验收脚本即 §7 P0 三条。
2. 申请 Yandex Wordstat API（OAuth+ClientId 四步）并小样本验证俄语商品词质量，结果回填 §4.1 C 级源可信度。
3. 确认 Ozon Premium 订阅档位（4,990₽ 档评估），决定 P2 信号源广度。
