# Research: ozon-pipeline Agent Harness「SQLite→PostgreSQL + LangMem 记忆管理」架构影响评估

> 调研日期：2026-11-12。方法：先读 `docs/research/ozon-agent-harness-deep-research.md`、`docs/research/ozon-agent-memory-architecture.md`、`docs/research/ozon-agent-github-frameworks.md`，再回源码核验（pipeline.py 全文、review_server.py 头部、ROADMAP.md 头部；`agent_runner.py`、`memory.py` 读取返回 ENOENT——七张 harness 表与 memory_items 均为设计稿）。外部事实全部引本轮直接抓取的官方一手页面（PostgreSQL 官方文档 SELECT/NOTIFY、pgvector 官方 README、PydanticAI 官方 durable_execution / harness permissions / harness pgtask、LangMem 官方概念指南与 PyPI/官方 issue、LangGraph 官方 stores/add-memory 文档、Mem0 官方文档），前次调研已核实的一手来源标注为"前次一手"。未读 `.env`，未改业务代码，未执行外部写操作。
> 注意：本仓库不在 GitHub 上（GitHub API 按仓库名查询 404），所有核验均基于本地工作区。

---

## Summary

**"替换"实为"首次实现"：七张 harness 表、`memory_items`、`agent_runner` 全部不存在于代码，因此不存在真实的 SQLite→PG 迁移阵痛，只存在"七表落在哪里"的选型分叉。** 本报告推荐：**PostgreSQL 先行（P0 的七表+runner 直接建在 PG 上，跳过 SQLite 中间态），LangMem 最后（仅作可选后台提取功能核、pin 0.0.30、保留自写提取 prompt 降级路径），记忆正本始终是自建 `memory_items`，不采用 LangGraph Store 作为事实源。** 官方一手证据支撑核心判断：PG 官方明文认可 SKIP LOCKED 用于"多消费者访问类队列表"；PydanticAI 官方 harness 已把 Postgres 队列（pgtask）列为官方 durability 路径；LangGraph 官方生产建议 PostgresSaver/PostgresStore；pgvector 官方提供 exact+ANN。而 LangMem 最新 PyPI 版本仍是 0.0.30（2025 年 10-11 月前后），2026 年无发版，维护状态存官方 issue 佐证——不宜承载核心事实。

---

## 0. 现状回源核验（源码级证据，本轮直接读取）

| 对象 | 状态 | 证据 |
|---|---|---|
| `pipeline.py` 六表 | **已实现** | products（含 metrics_json/prompt_version，UNIQUE(query,ozon_id)）/ events / reviews（decision+6 死因枚举）/ keywords（consumed_at 去重）/ credit_log / harvested_seeds；TRANSITIONS 状态机；`_migrate` 为 PRAGMA 驱动的增量迁移；`record_review` 供审核台调用（pipeline.py 全文 232 行） |
| `review_server.py` | **已实现** | 917+ 行，stdlib http.server，审核台/board/review/product trace/POST action |
| 各业务 worker | **已实现**（前次调研源码级确认） | select_products / sourcing_agent / content_agent / image_worker / listing_worker / ozon_client / nexscope_client / keyword_library / trend_check / sync_to_db |
| 七张 harness 表（tasks/tool_calls/artifacts/human_tasks/signals/budgets/prompt_versions） | **设计稿** | schema 全文在 harness 深研报告 §2.4；仓库内无对应建表代码 |
| `agent_runner.py` | **不存在** | 读取返回 ENOENT |
| `memory_items` + 六动词 `memory.py` | **设计稿** | `memory.py` 读取返回 ENOENT；schema 在 memory 架构报告 §2 |

**对本次决策的直接含义**（研究者推断）：由于 harness 层零代码，"替换 SQLite"的成本模型完全不同于常规迁移——PG 方案的对比基线是"尚未发生的 SQLite 实现"，PG 的增量运维成本只需与"未来二次迁移的返工成本"比较。

---

## Findings

### 1. PydanticAI 当前官方 Harness 架构（最新一手，替代前次报告中"仅 core + deferred tools"的认知）

**Claim:** PydanticAI 已发布官方 Harness 库（`pydantic-ai-harness`），提供能力层（Permissions、FileSystem、Shell、ACP、Approval Reviewer、Tool Call Judge、Guardrails、Ask User、Code Mode）、StepPersistence（快照每步到自选存储，支持 resume/fork/replay）与九个 durable execution 引擎；**pgtask 是官方 Postgres 队列 durability 路径**。但 Harness 处于 0.x，官方声明小版本间 API 可能变更。**Sources:** [Durable Execution](https://pydantic.dev/docs/ai/durable_execution/)、[Harness Permissions](https://pydantic.dev/docs/ai/harness/permissions/)、[pgtask Durability](https://pydantic.dev/docs/ai/harness/pgtask/)。**Support:** direct evidence（本轮全文抓取）。**Confidence:** high。

关键边界（对本项目）：
- 九引擎 = Temporal / DBOS / Prefect / Restate / AWS Lambda / Absurd / **pgtask** / Azure Durable Task / Apache Airflow；"Durability is not storage"：一个 run 的崩溃续跑需要引擎，而保存/恢复/fork 用 StepPersistence 即可，无需引擎。
- **Permissions 能力只覆盖两类工具**：官方原文 "File writes are the FileSystem tools write_file, edit_file, and create_directory … Commands are the tools whose metadata declares code_arg_language='shell'… **Every other tool runs in every mode.** Gate a tool of your own with `requires_approval=True`, a ToolGuardrail, or a ToolCallJudge."——即业务工具（listing-write/image-gen/paid-read）的权限分级仍需自建 policy gate，官方 Permissions 不直接替代本项目 permission_level 五级设计。
- `requires_approval=True` → 参数校验后才递延（前次一手），审批结果在 durable workflow 内保存在消息历史 ToolReturnPart 元数据中。
- pgtask：任务状态存 Postgres 表，step 级 checkpoint；**官方明示 at-least-once 语义**："A result is saved only after the call finishes. If the worker crashes after a tool has done something but before its result is saved, the tool runs again. Write tools … so that running them twice does no harm."——与本项目"外部写幂等（offer_id）+ 人工审批单步执行"的设计要求一致。

### 2. PostgreSQL 队列/事件语义（官方原文，直接回答"能否替代独立 queue/event service"）

**Claim:** SKIP LOCKED 的官方语义是官方明文认可的"多消费者类队列"模式，但有"不一致视图"警告；LISTEN/NOTIFY 是事务性、按提交顺序、无持久投递保证的通知机制——只能作轮询的延迟优化，不能替代表为主的事件总线。**Sources:** [SELECT（The Locking Clause）](https://www.postgresql.org/docs/current/sql-select.html)、[NOTIFY](https://www.postgresql.org/docs/current/sql-notify.html)。**Support:** direct evidence（官方原文，逐条核验）。**Confidence:** high。

官方原文要点（本轮 raw 抓取核验）：
- SKIP LOCKED："any selected rows that cannot be immediately locked are skipped. Skipping locked rows provides an **inconsistent view of the data**, so this is not suitable for general purpose work, **but can be used to avoid lock contention with multiple consumers accessing a queue-like table**."——这正是任务领取的官方适用场景；警告意味着**必须配合 idempotency_key 唯一约束 + lease 心跳**兜底（官方只保证跳过被锁行，不保证任务恰好被领一次）。
- SKIP LOCKED 仅作用于行锁，表级 ROW SHARE 锁照常获取；不能与聚合/WITH TIES 等上下文并用（FETCH WITH TIES 明文禁止 SKIP LOCKED）。
- NOTIFY："if a NOTIFY is executed inside a transaction, the notify events are **not delivered until and unless the transaction is committed**"；"messages from different transactions are delivered **in the order in which the transactions committed**"；payload 默认 **<8000 字节**；通知队列上限约 **8GB**，长事务阻塞清理；同事务同 channel 同 payload 去重；执行过 NOTIFY 的事务不能 two-phase commit。
- **研究者推断（官方页未直述的缺口）**：NOTIFY 是即发即忘——无监听会话在线时通知不会保留、无 ack/重投语义。因此 event bus 必须以 `events/signals` 表为事实源、NOTIFY 仅用于"把 runner 的 1 分钟轮询降为秒级唤醒"，轮询兜底不可删。独立消息队列（Redis/Rabbit/Kafka）在单机日事件 <10³ 量级仍无必要——前次结论维持，且现在有官方 SKIP LOCKED 语义背书。

### 3. pgvector 官方语义与适用边界（能否替代独立向量服务）

**Claim:** pgvector（当前 v0.8.7，Postgres 13+）提供精确检索（完美召回）与 HNSW/IVFFlat 近似索引；对本项目"千条级记忆"用精确检索即可，pgvector 完全可替代 sqlite-vec，且不需要 Qdrant 等独立向量服务。**Sources:** [pgvector README](https://github.com/pgvector/pgvector)。**Support:** direct evidence（本轮全文抓取）。**Confidence:** high。

官方要点与边界：
- 默认精确 NN、完美召回；加近似索引后"same queries 可返回不同结果"（官方明示），需按官方建议用精确检索对照监控 recall。
- HNSW 查询性能-召回权衡更好但构建慢/更耗内存；IVFFlat 构建快但查询权衡差；过滤是**索引扫描后才应用**（10% 匹配率 + ef_search=40 平均只剩 ~4 行），0.8.0+ 提供 iterative scan 缓解；多租户建议分区或分表。
- 矢量列随数据同库：JOIN、事务、WAL 复制与 PITR 官方支持。千条级记忆应**直接精确检索**（无索引、无调参），近似索引是 >10⁴ 条后的优化项——此判断为研究者基于官方"默认精确=完美召回"语义的推断。

### 4. LangMem 接管什么、不接什么（官方概念指南一手核验 + 维护状态事实）

**Claim:** LangMem 官方定位是"提取/整理/优化 prompt 的功能核 + 可选的 LangGraph Store 有状态层"；它不提供业务事实、任务状态、审核真值、权限审批等任何治理语义。**其有状态层绑定 LangGraph BaseStore/PostgresStore，而 Store 的条目模型（namespace+key+value+时间戳）无法表达 candidate 审批、证据链、supersede 软删**——所以 Store 不能当记忆正本。**Sources:** [LangMem 概念指南](https://langchain-ai.github.io/langmem/concepts/conceptual_guide/)、[LangGraph Stores](https://docs.langchain.com/oss/python/langgraph/stores)、[langmem PyPI](https://pypi.org/project/langmem/)、[langmem issue #125](https://github.com/langchain-ai/langmem/issues/125)。**Support:** direct evidence + 研究者推断（冲突分析部分）。**Confidence:** high（功能边界）/ medium（维护状态，见 Contradictions）。

| LangMem 接管（可作为功能核引入） | LangMem 不接管（必须留在自建层） |
|---|---|
| 记忆提取（create_memory_manager：接受对话+现有记忆→LLM 决定 insert/update/consolidate） | 业务事实 L1（products/keywords/artifacts）——工具/人工审批后落库，LLM 零写权 |
| 记忆形态学（semantic/episodic/procedural；profile 单文档 vs collection 多条） | 任务状态 L2（tasks/tool_calls/human_tasks）——runner 与 policy gate 专属 |
| 写入时机分层（hot path vs background formation，官方对比表：background 无延迟影响、批量模式分析） | 审核真值 L5（reviews golden set）——人工点击即真值，LLM 自评不作指标来源 |
| prompt 优化建议（create_prompt_optimizer，metaprompt 等） | prompt 激活/回滚 L6——仍走 prompt_versions draft→审批→canary 通道 |
| namespace 元组约定（`(org, {user_id}, app)` 可含模板变量） | 权限/预算/熔断（budgets/permission_level）——无任何内置审批流（官方文档无此能力） |

官方明文："**These core functions do not depend on any particular database or storage system.**"（功能核）；"Use these if you're using LangGraph Platform or LangGraph OSS"（有状态层 = Store Managers/Memory Tools，绑定 BaseStore）。

**BaseStore/LangGraph Store 语义（一手）**：namespace 是字符串元组、**前缀匹配**（`("alice",)` 命中所有子命名空间）；条目 = value dict + key + namespace + created_at/updated_at；PostgresStore 搜索按 `updated_at` 倒序，官方明示 "Do not rely on a specific order across implementations"；生产用 PostgresStore/AsyncPostgresStore（langgraph-checkpoint-postgres，psycopg3，pgvector 可选语义检索，PoolConfig 官方建议小池 1-5 适配低并发）。**冲突分析（研究者推断）**：Store 条目没有 status/evidence/approved_by/supersedes/valid_until 字段，也没有"LLM 只能写 candidate"的行级权限概念——若把记忆正本放进 Store，防污染六规则（建议区隔离/审批即生效/证据链强制/可作废/写入预算/回归护栏）失去 schema 载体。故正本必须是自建 `memory_items`，Store 至多作为未来"检索 API 形态"的可重建投影。

**LangMem 维护状态（决策相关事实）**：PyPI 最新版本 0.0.30（MIT，©2025 LangChain）；官方 issue #125（2025-10-07 请求发布 0.0.30 以兼容 langgraph 1.0.0a1，2025-11-07 关闭 completed）说明 0.0.30 是为 langgraph 1.0 兼容补发的版本；截至本轮（2026-11）无更新的 PyPI 版本。第三方对该仓库维护状态的判断相互矛盾（见 Contradictions）。

### 5. 推荐集成方式：自建 schema（PG）+ LangMem 功能核（延后、pin、可降级）

**Claim:** 推荐组合 = **PG 上自建全部 harness schema（七表+memory_items）→ runner/policy gate/HITL 自建（官方 Permissions 不覆盖业务工具）→ LangMem 功能核在记忆层 P2 作为可选后台提取器（pin 0.0.30，产出一律落 `memory_items` candidate）→ pgvector 作 memory_items 的可重建向量索引 → 不采用 LangGraph Store 为事实源、不引入独立 queue/vector 服务。** **Support:** 研究者推断，逐条锚定本轮官方一手证据。**Confidence:** high（作为架构推荐）/ 相应事实各自 high。

理由链：
1. **模型兼容**：Qwen/百炼经 openai 兼容端接入——PydanticAI 官方 README 称 model 无关（前次一手）；LangMem 沿 LangChain init_chat_model 串（官方概念指南代码示例用 anthropic/init_chat_model），Qwen 接法可行但零实测，引入前必须用百炼实测提取质量。
2. **HITL**：human_tasks 表为唯一审批队列；若引入 PydanticAI，`requires_approval=True`/ApprovalRequired 与 human_tasks 一一映射（官方 deferred approval 流程），审计留 tool_calls。
3. **防污染**：六规则原样迁移到 PG schema（无语义变化）；memory_items 加 `embedding vector` 列 + `embedding_model` 标注，向量属可重建索引，`forget` 改行状态不删正本。
4. **可追溯**：tool_calls（model/prompt_version/effective_prompt/input/output/cost）不变；LangMem 提取作业本身也记一条 tool_call（tool='langmem.extract'），产物带 evidence_json。
5. **迁移锁定评估**：memory_items schema 自持、embedding 可全量重嵌、LangMem 只出现在后台 job 内部 → 可随时降级为"自写提取 prompt"（前次报告已论证多数收益相同）。反向锁定风险最大的是"记忆正本进 LangGraph Store"——条目模型与治理字段不匹配，迁出需要整表翻译，明确排除。
6. **备选官方 harness 组合**：若团队想要"官方生产级任务队列"，PydanticAI Harness 的 pgtask（PG 队列 + step checkpoint + at-least-once）是官方支持的 PG 原生路径，可与自建七表二选一或叠加（pgtask 管 run 内步骤 checkpoint，自建 tasks 表管业务任务/审批/预算——二者职责不同）；但 Harness 0.x API 变更声明是真实成本，建议等 P1 稳定后再评估引入，P0 仍用自建 runner（~百行）。

### 6. 七张表 + memory_items 在 PG 上的具体落地影响（逐项）

**Claim:** SQLite→PG 对既有设计几乎全是"简化"而非"改设计"：领取、幂等、事务、并发语义均由 PG 原生提供；需要重写的只有 DDL 方言与 `_migrate` 机制。**Support:** 研究者推断，锚定官方语义与现有源码。**Confidence:** high。

| 维度 | SQLite 方案（前次设计） | PostgreSQL 方案 | 变化性质 |
|---|---|---|---|
| 七表 DDL | INTEGER AUTOINCREMENT、TEXT 时间戳、TEXT 存 JSON | BIGINT IDENTITY、TIMESTAMPTZ、JSONB（可 GIN 索引） | 方言重写，无语义变化 |
| 领取 | `BEGIN IMMEDIATE` + UPDATE…WHERE lease 过期（依赖写串行化） | `UPDATE tasks SET state='running'… WHERE id IN (SELECT id … FOR UPDATE SKIP LOCKED) RETURNING *` | **官方队列语义**，天然支持任意多 worker；仍需 idempotency_key+lease 兜底（§2 官方警告） |
| 幂等 | idempotency_key UNIQUE | 同 + `ON CONFLICT DO NOTHING`（官方 UPSERT） | 等价 |
| 事务 | 单写者事务、busy_timeout、短写事务纪律 | 标准 ACID；"事件产生+任务入队"原子性照旧 | **简化**（删 busy_timeout 纪律） |
| 并发 | 官方 WAL："only one writer at a time"，建议 2-4 worker | 写并发无此限制；连接池注意（LangGraph 官方 PoolConfig：小池 1-5 适配低并发——可作参考值） | **解锁** |
| lease/心跳 | 30s 续租、过期回收 | 逻辑不变；`lease_until` 用 TIMESTAMPTZ 比较更稳 | 不变 |
| event bus | events 表+1min tick 轮询 | 同左；可选 NOTIFY 降延迟（事务提交后投递、payload<8000B、按 commit 序、无监听即丢→轮询兜底必留） | 可选优化 |
| APScheduler | SQLAlchemyJobStore 建在 SQLite | 官方 SQLAlchemyJobStore 支持 SQLAlchemy 兼容 DB，连接串换 PG 即可（前次一手来源 + 语义推断；jobs 为序列化存储，跨库不可直接复制，切换后需重建 jobstore） | 简化 |
| 备份 | `VACUUM INTO` 日快照 | pg_dump / WAL 归档 / PITR（官方备份文档体系；pgvector README 明示走 WAL 支持复制与 PITR） | **升级** |
| `_migrate` | PRAGMA 驱动增量迁移 | 版本化迁移脚本（alembic 或编号 SQL），首次需要 | 新增小工具 |
| 部署 | 零新服务 | +1 服务（Docker/systemd 单容器） | **唯一真实新增成本** |
| 记忆向量 | sqlite-vec（暴力检索、pre-v1 锁版本） | pgvector 精确检索起步（无索引无调参），>10⁴ 条再建 HNSW | **简化**（删锁版本焦虑） |

### 7. 新旧方案收益/成本对比与实施顺序

**Claim:** 分开判断：**PostgreSQL 值得现在上（P0 直接落 PG），LangMem 不值得现在引入（记忆层 P2 再按实测决定）。** **Support:** 研究者推断，锚定 §1/§2/§3/§4/§6 事实。**Confidence:** high。

| 维度 | 旧方案：SQLite 七表+sqlite-vec（+自写提取） | 新方案：PostgreSQL+pgvector（+LangMem 功能核延后） |
|---|---|---|
| 收益 | 零新服务、单文件备份、失败模式简单 | 官方队列语义（SKIP LOCKED）、官方向量（pgvector）、官方记忆/harness 生态底座（LangGraph PostgresSaver/Store、pgtask）、写并发解锁、PITR 备份 |
| 复杂度 | 极低；但未来触发升级时要返工 DDL/领取 SQL/迁移脚本/向量库 | 中低：单容器 PG + 版本化迁移；换取"零返工路径" |
| 团队/运维 | 几乎无 | 会 pg_dump/会看日志/会重启容器即可；1 人单机可承受 |
| 性能/规模 | 单写者；>4-6 并发 worker 或日任务 >10⁴ 即触顶（前次结论） | 同规模下余量大；千条记忆精确检索够用 |
| 可靠性 | WAL 崩溃恢复 + SQLITE_BUSY 边界（前次一手） | ACID+复制/PITR；at-least-once 语义需幂等兜底（两种方案都需要） |
| 记忆层 | 自写提取或 LangMem 功能核（无存储依赖，两方案通用） | 同左 + 可选 BaseStore/PostgresStore 检索投影（非事实源） |

**实施顺序（若不同时做）**：P0 七表+agent_runner **直接建在 PG**（含 memory_items 表，向量列留空）→ P1 单商品全链路+human_tasks 审批闭环 → P2 记忆层：自写提取 prompt 跑通 candidate→审批→recall；用百炼 Qwen 对 20-30 条真实 reviews 做"LangMem 功能核 vs 自写 prompt"提取对比，合格才引 LangMem（pin 0.0.30）→ P3 prompt 优化（create_prompt_optimizer 同一批实测后决定）→ P4 扩容（此时 PG/pgvector/NOTIFY 全部就绪，无返工）。若团队拒绝引入任何新服务：SQLite 方案与前次设计完全不变、LangMem 功能核照样可用，升级触发条件（>4-6 worker / 日任务 >10⁴ / 多机）维持原文。

### 8. 数据迁移、回滚、测试与分阶段验收

**Claim:** 现有六表数据量小（单机 SQLite），迁移是一次性单向切换 + 只读回退，不需要长期双写。**Support:** 研究者推断（基于六表规模与单写者现状）。**Confidence:** high（策略层）/ 具体行数为零实测。

- **迁移**：六表按既有 schema 导出（JSON/CSV）→ COPY 进 PG（id 保留）；行数与抽样字段逐表校验；`data/pipeline.db` 改名冻结为只读快照保留 ≥2 周（回滚 = 指回旧文件 + 旧代码路径）。**不做长期双写**：双写引入一致性协调成本，六表规模下单向切换+快照回退更简单可靠（研究者推断）。
- **测试**（pytest 断言，全部不改业务代码即可写）：① 多线程/多进程并发领取同一批任务零重复（SKIP LOCKED + idempotency_key 双保险）；② kill -9 后过期 lease 被回收且重跑无副作用重复；③ NOTIFY 无监听者时流程不阻塞（轮询兜底生效）；④ memory 六动词权限：LLM 写→candidate、未审批永不入 recall、无 evidence 拒写、supersede 链可查、日写预算熔断；⑤ pgvector：删向量列→由 content 全量重嵌→recall 结果一致（事实源不变量）；⑥ 现有 tests/ 回归全绿。
- **分阶段验收**：沿用前次报告 P0-P4 验收清单，追加 PG 特定项（上表①③⑤）；P4 的"7 天无人值守"验收在 PG 下追加"pg_dump 日备份可恢复演练"。

---

## Contradictions

1. **LangMem 维护状态，第三方评估互相矛盾**：PyPI 一手事实是 0.0.30 为最新版（2025 年发布，2026 无新版本）；GitHub 官方 issue（langchain-ai/langgraph #6980，2026-03 开、2026-10 关）称"langmem repo appears unmaintained, last commit October 2025"；而第三方博客（rywalker.com，2026-02/06 更新）称"repo active, commits as recent as June 7, 2026"。**处理**：以 PyPI 无新版本 + 官方 issue 为决策依据（ pin 版本、留降级路径），第三方评论仅作风险信号，不采信其结论。
2. **LangMem 发布时间口径不一**：第三方分别称 0.0.30 发布于 2025-09-09 / 2025-10-27；官方 issue #125 时间线（10-07 请求发布、11-07 关闭 completed）与后者吻合、与前者不符。**处理**：报告中只陈述"0.0.30，2025 年 10-11 月前后，2026 无更新"。
3. **PydanticAI 官方"Permissions/HITL 宣传"与覆盖边界**：Harness 文档宣传 person-approval 能力，但 Permissions 明文只检查 FileSystem 写工具与 Shell 命令工具，其余工具"runs in every mode"——本项目五级 permission_level 仍须自建。这不是矛盾而是能力边界，记录以免误读。
4. **pgtask at-least-once 与"不重复付费调用"宣传**：官方同时宣传"crash 后不重复调用模型/工具"（已保存 step 复用）与"tool 副作用先发生、结果未保存时会重跑"（at-least-once）——两段并存，采用时必须以幂等设计兜底，不能只信前者。

## Missing evidence

- Qwen（百炼 openai 兼容端）在 LangMem 0.0.30 提取 prompt 下的抽取质量：零实测（P2 决策点）。
- LangGraph PostgresStore 在本项目的实际检索质量与 pgvector index 配置：未实测（当前不依赖该路径）。
- APScheduler SQLAlchemyJobStore 在 PG 上的 job 序列化/恢复行为：沿用前次一手来源 + 官方页面语义推断，本轮未重新抓取。
- PostgreSQL 官方备份文档（backup.html）本轮未抓取原文，仅引用其官方 URL 体系，未引具体数字。
- 六表现有数据行数未统计（未连库执行只读查询），迁移工作量估计基于 schema 复杂度而非行数。

## Sources

**保留（本轮一手，直接抓取）**
- PostgreSQL SELECT（The Locking Clause，SKIP LOCKED 官方原文）— https://www.postgresql.org/docs/current/sql-select.html
- PostgreSQL NOTIFY（事务投递/顺序/payload/队列语义官方原文）— https://www.postgresql.org/docs/current/sql-notify.html
- pgvector 官方 README（v0.8.7，exact+HNSW/IVFFlat、iterative scan、WAL/PITR、多租户边界）— https://github.com/pgvector/pgvector
- PydanticAI Durable Execution（九引擎、durability≠storage、StepPersistence）— https://pydantic.dev/docs/ai/durable_execution/
- Pydantic AI Harness Permissions（模式/边界/0.x 版本政策官方原文）— https://pydantic.dev/docs/ai/harness/permissions/
- Pydantic AI Harness pgtask Durability（PG 队列、step checkpoint、at-least-once 规则官方原文）— https://pydantic.dev/docs/ai/harness/pgtask/
- LangMem 概念指南（类型学、profile/collection、hot path vs background、功能核无存储依赖、namespace）— https://langchain-ai.github.io/langmem/concepts/conceptual_guide/
- langmem PyPI（版本/license 一手）— https://pypi.org/project/langmem/ ；langmem 官方 issue #125（0.0.30 发布时间线）— https://github.com/langchain-ai/langmem/issues/125
- LangGraph Stores（BaseStore 五方法契约、namespace 前缀匹配、PostgresStore 排序/生产建议/自定义 store SQL 官方原文）— https://docs.langchain.com/oss/python/langgraph/stores
- LangGraph add-memory（生产用 PostgresSaver/PostgresStore 官方建议）— https://docs.langchain.com/oss/python/langgraph/add-memory
- AsyncPostgresStore/PoolConfig 官方参考（pipeline/pool 语义）— https://reference.langchain.com/python/langgraph.store.postgres/aio/AsyncPostgresStore/from_conn_string
- Mem0 官方文档（pgvector 为官方向量后端之一；OSS 默认本地 Qdrant+SQLite history——轻量对比用）— https://docs.mem0.ai/components/vectordbs/dbs/pgvector 、https://docs.mem0.ai/open-source/overview
- langchain-ai/langgraph issue #6980（LangMem 维护状态官方仓库一手信号）— https://github.com/langchain-ai/langgraph/issues/6980
- 本仓库源码（本轮直接读取）：pipeline.py（全文）、review_server.py（头部）、ROADMAP.md（头部）；agent_runner.py / memory.py（ENOENT 证明不存在）

**保留（前次调研一手，本轮未重复抓取，标注沿用）**
- SQLite WAL（"only one writer at a time"）— https://sqlite.org/wal.html
- APScheduler SQLAlchemyJobStore — https://apscheduler.readthedocs.io/en/stable/modules/jobstores/sqlalchemy.html
- LangGraph Persistence/Interrupts、PydanticAI README/Deferred Tools、Temporal/Prefect、Mem0/Graphiti/Letta README/LICENSE —— 见 docs/research/ozon-agent-harness-deep-research.md §9 与 ozon-agent-memory-architecture.md §7 的来源清单

**拒绝/降权**
- anchorterminal.com、caura.ai、rywalker.com（LangMem 维护状态第三方评测）——互相矛盾，仅作风险信号记录，不作设计输入
- 各类框架榜单/二手综述——任务明令避免堆砌，未使用

## Next steps

1. 决策确认：PG-first（P0 七表直接落 PG）vs 维持 SQLite 零新服务——本报告给出默认推荐 PG-first，但运维偏好属父会话决策。
2. P2 前置实测：百炼 Qwen 对 20-30 条真实 reviews 跑"LangMem 0.0.30 功能核 vs 自写提取 prompt"对比（提取准确率/噪声率/成本），结果回填 §7 顺序表。
3. 若采纳 PG：先写版本化迁移工具 + §8 测试清单的 ①③ 两条（并发领取、NOTIFY 兜底），这是新方案独有的两个失败模式。

---

## Acceptance

- criterion-1（简明结论+残余风险）：Summary 给出"PG 先行、LangMem 最后、memory_items 为唯一事实源"的直接回答；Contradictions 4 条 + Missing evidence 5 条即残余风险。
- 外部事实全部一手：PostgreSQL 官方文档两条（含 SKIP LOCKED 逐字原文）、pgvector 官方 README、PydanticAI 官方三页、LangMem 官方概念指南/PyPI/官方 issue、LangGraph 官方 stores/add-memory、Mem0 官方文档；第三方仅标记矛盾未采信。研究者推断均已逐处标注。
