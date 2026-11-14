# Research: ozon-pipeline Agent Harness 记忆层设计与 GitHub 开源项目调研

> 调研日期：2026-11（本轮）。方法：先读 `docs/research/ozon-agent-harness-deep-research.md`、`docs/research/ozon-agent-architecture-assessment.md`、`ROADMAP.md`，再回源码核验 `pipeline.py` 现有表结构与设计边界（确认 `agent_runner.py` 不存在，七张新表仍是设计稿）；外部事实全部引 GitHub 官方 README/LICENSE 一手文件与官方文档站（docs.mem0.ai、langchain-ai.github.io、alexgarcia.xyz 官方博客、pgvector/qdrant 官方仓库），不用二手榜单。未读 `.env`，未改业务代码，未执行外部写操作。个别 LICENSE 直连 404 时改经检索服务定位官方文件路径并标注。

---

## Summary

现有系统的事实源是 `pipeline.py` 的六张 SQLite 表（products/events/reviews/keywords/credit_log/harvested_seeds），signals/tasks/tool_calls/artifacts/human_tasks/prompt_versions 七张表仍是 harness 调研中的设计稿、尚未落地（`agent_runner.py` 确认不存在）。本报告设计：**在七表之上加一张自建 `memory_items` 表（SQLite 仍是唯一事实源，向量只作可重建索引）+ remember/recall/forget/supersede/compact 六个最小 API + 六条防自污染规则**；外部项目全部按"参考架构/可库引入/不建议引入"三档归类：Graphiti/Zep、Letta、Cognee、Mem0 OSS 记忆管线作参考架构，LangMem 功能核（MIT、无存储依赖）是唯一建议可作可选库引入者，向量检索用进程内 sqlite-vec（MIT/Apache 双许可、纯暴力检索，千条级够用），不为记忆层引入 Qdrant/Neo4j/Postgres 任何重服务。

---

## 0. 现状回源核验（源码级证据）

**Claim:** 现有 DB = 六张表 + prompt_version 列；七张 agent 表未实现。**Support:** 直接证据（本轮读取源码）。

| 对象 | 现状 | 源码证据 |
|---|---|---|
| `products` | 状态机主表：query/ozon_id/source_1688_id/state/death_reason/**metrics_json**/**prompt_version**，UNIQUE(query,ozon_id) | `pipeline.py:30-43` |
| `events` | 状态流转事件：product_id/from_state/to_state/note | `pipeline.py:44-51` |
| `reviews` | 审核 eval 标签：kind/asset_path/decision(approve\|regenerate\|kill)/reason（死因枚举 6 类） | `pipeline.py:52-62`, `pipeline.py:104-106`, `pipeline.py:196-206` |
| `keywords` | 词级指标 + consumed_at 消费去重 + fetched_at | `pipeline.py:63-78` |
| `credit_log` / `harvested_seeds` | 积分台账 / 类目种子游标 | `pipeline.py:79-89` |
| `signals/tasks/tool_calls/artifacts/human_tasks/prompt_versions(+budgets)` | **设计稿**，schema 全文在 harness 深研报告 §2.4，代码不存在 | `docs/research/ozon-agent-harness-deep-research.md` §2.4；`agent_runner.py` 读取返回 ENOENT |

**设计边界（承前两份调研，记忆层必须继续遵守）**：状态流转、定价、预算熔断、权限授予、真实上架执行永远不让 LLM 决策；LLM 输出一律落 `suggestions`/`tool_calls`，经 policy 校验与人工审批才生效；人工节点统一 human_tasks 队列。记忆层不得成为绕过这些闸门的旁路。

---

## Findings

### 1. 记忆分层：六层定义、权威来源、读写者与 LLM 写入权

（分层模型为研究者设计，锚定在已核验的现有/规划表上；类型学参考 LangMem 官方 semantic/episodic/procedural 三分法——来源见 §7。）

| 层 | 内容举例 | 权威来源（唯一事实源） | 保留周期 | 写者 | 读者 | LLM 可写？ |
|---|---|---|---|---|---|---|
| **L1 业务事实** | 选品结果、货源、价格、类目树、素材 sha256 | `products/keywords/source_1688_id` + `artifacts(path,sha256)`；Ozon 类目树 JSON | 永久（审计） | 工具/人工审批后的落库 | 全部 worker、planner | **否**。LLM 输出必须过 `validate()`/闸门/人工确认才落库（字典属性必须来自 Ozon API 字典） |
| **L2 工作记忆** | 当前商品的任务/调用/成本/审批态 | `tasks/tool_calls/artifacts/human_tasks`（七表设计稿）+ `products.metrics_json` | 单品全链路期间，done 后归档只读 | runner/tool adapter | runner、审核台、eval | **否**。LLM 建议也以 tool_call 输入输出原样留痕，不混入事实 |
| **L3 episodic 事件经历** | 趋势告警、打回事件、外部信号、节假日事件 | `events` + `signals(expires_at,credibility,dedup_key)`（设计稿） | signals 带 TTL（节日提前 6-8 周，突发 ≤14 天）；events 永久 | 采集任务/审核台/趋势 job | planner、评估、事件选品管线 | **否**。LLM 只做解析/归因摘要（落 suggestions），原始事件必须来自 API/人工 |
| **L4 semantic 语义知识** | 类目→俄语词映射经验、供应商可靠性、合规注意 | 新表 `memory_items(layer='semantic')`（本报告 §2 设计） | 长期，带 valid 窗口与 supersede 链 | 工具/人工直接写 active；LLM 只写 candidate | 生成类工具（文案/找货/选品） | **受限：是**——LLM 可写 `status='candidate'` 新条目，带 evidence；转 active 必须人工审批 |
| **L5 长期 eval 经验** | golden set 标签、通过率/成本聚合、阈值经验 | `reviews`（人工点击即真值）+ `eval_report` 聚合 + `prompt_versions.metrics_json` | 永久（golden set 是资产） | 人工审核台、只读聚合脚本 | prompt 优化、阈值校准 | **否**。LLM 自评不作为指标来源，只信人工标签与真实销量/退款 |
| **L6 prompt 经验** | prompt 版本、生效 prompt、失败模式 | `prompt_versions(draft→golden_pass→canary→active→retired)` + `tool_calls.effective_prompt` | 版本历史永久 | LLM 写 diff 建议 → 人工审批 → runner 激活 | image_worker/content_agent | **受限：是**——只产 diff 建议，激活/回滚必须审批 |

**核心不变量：SQLite 仍是事实源，向量检索不是事实源。** `memory_items` 表行是记忆的正本；`vec_*`（sqlite-vec）表只存 embedding+rowid 映射，属可重建索引——`forget` 必须改 SQLite 行状态，删除向量表条目不构成遗忘；vec 表损坏可由 `memory_items` 全量重嵌。

### 2. 数据模型与最小接口（本报告设计 = 研究者推论，贴现有 pipeline.py 风格）

**最小 schema（沿用现有"窄表+JSON"惯例）：**

```sql
CREATE TABLE IF NOT EXISTS memory_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dedup_key TEXT UNIQUE,            -- hash(namespace,scope,scope_ref,kind,content)
  layer TEXT NOT NULL,              -- semantic|episodic|eval_note（L1/L2/L5 不入此表）
  scope TEXT NOT NULL,              -- product|category|store|global
  scope_ref TEXT,                   -- product_id / 类目名 / store 常量 'default'
  kind TEXT NOT NULL,               -- supplier|mapping|compliance|threshold_note|prompt_tip
  content TEXT NOT NULL,            -- 记忆正文（自然语言，中文，俄语词原样保留）
  data_json TEXT DEFAULT '{}',      -- 结构化字段（supplier_id、映射表等）
  confidence REAL DEFAULT 0.5,      -- 0-1：来源等级×样本量×新鲜度，写入时算好
  evidence_json TEXT NOT NULL,      -- [{type:'review'|'tool_call'|'event'|'listing',id}] 无证据拒写
  status TEXT DEFAULT 'candidate',  -- candidate|active|superseded|invalid|expired
  supersedes_id INTEGER REFERENCES memory_items(id),
  valid_from TEXT NOT NULL, valid_until TEXT,   -- TTL；NULL=长期
  created_by TEXT NOT NULL,         -- 'llm:qwen-plus@pv12' | 'human:yz' | 'tool:eval_report'
  approved_by TEXT, approved_at TEXT,           -- candidate→active 必填
  embedding_model TEXT,             -- vec 表重建线索；embedding 本体不在此表
  hit_count INTEGER DEFAULT 0, last_hit_at TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
```

**最小 API（`memory.py`，六个动词）：**

| API | 语义 | 权限/防污染点 |
|---|---|---|
| `remember(item, evidence)` | 写入 | LLM 调用强制 `status='candidate'`+`created_by='llm:*'`，无 evidence 抛错，受 `budgets(scope='memory:write', 每日≤20)` 熔断；工具/人工可直写 active（事实性条目） |
| `recall(query, scope, k, budget)` | 读回 | 先 SQL 过滤（status='active'、valid 窗口、scope 匹配含向上冒泡 product→category→store→global），再按需向量/FTS 预召回，重排后返回 content+evidence+置信度；写回 `hit_count/last_hit_at` |
| `forget(id, reason)` | 软删 | `status='invalid'`，保留审计；物理删除只发生在 compact |
| `supersede(new, old)` | 换新 | 单事务：new→active、old→superseded、互写 supersedes 链；同 kind+scope 允许多条 active 时 recall 必须全部返回并标注冲突 |
| `promote(id, by)` / `reject(id)` | 审批 | 对应 human_tasks(kind='memory_approval')；candidate 无审批永不进 recall 结果 |
| `compact(scope)` | 整理 | 合并近重复、删 expired/invalid 条目、重建 vec 索引；输出迁移报告 |

**何时写/何时读/如何注入：**

- **写时机**：确定性事实（工具落库）同步写；经验类一律**后台提取**（审核决定落库后、任务完成后触发提取 job——对应 LangMem 官方"background formation 不影响响应时延"的分层），LLM 热路径同步写只允许确定性字段。harness 调研 §6.2 的六条防污染规则原样适用于本表。
- **读时机**：planner 组装任务时注入，不在 LLM 循环内反复检索。
- **注入方式**：拼入该工具调用的输入 JSON 或 system prompt 独立小节，标题固定为「经验（来自 memory_items，仅供参考，不覆盖任何硬闸门/阈值/公式）」，防 LLM 把经验当规则。
- **token 预算**：单次 recall 注入 ≤500 token、单任务累计 ≤2k；超预算按排序分截断。
- **排序**：`score = 0.4·confidence + 0.3·exp(-0.01·age_days) + 0.2·evidence_strength(review>listing>tool_call>event) + 0.1·hit_recency`，scope 距离越近加成越高（product>category>store>global）。
- **冲突处理**：同 kind+scope 冲突条目不下隐式结论——recall 全量返回并带 `conflict=true` 标记，由调用方（规则 planner 或人工）裁决；LLM 检测到矛盾只上报，不自行改写他条（写权只在 supersede，且走审批）。过期 `valid_until < now` 由日 job 批量置 `expired`。

### 3. 全链路实际用例（六场景）

| 场景 | recall 什么（示例） | 何时写 / 写什么 | 防错误经验自污染 |
|---|---|---|---|
| **① 选品计划与事件经验** | 该类目历史"节日词→实际出单"转化记录、误报信号类型、已 kill 的词及原因（scope=category） | 每次选品闸门放行/拦截落 events（事实）；周度 eval 聚合出"类别×转化率"经验条目（tool:eval_report 写，active） | 转化结论只认真实销量/退款，不信 Nexscope 估算绝对值（误差 2.8–6.3×，ROADMAP）；LLM 归因只能写 candidate |
| **② 找货供应商经验** | 1688 供应商历史表现：同一性通过率、发货可靠性、复购标记（scope=global，kind=supplier，data_json 存 supplier_id） | `source_product` 任务完成后从 sourcing report 提取；人工 source_confirm 的决定直接生成高置信条目 | 供应商打分必须关联 sourcing_report/tool_call evidence；单一订单不出经验，≥3 单才 allow active；LLM 只写 candidate |
| **③ 文案/属性映射** | 类目→俄语属性值映射经验、禁忌词新案例、类目字典高频值（scope=category） | 人工在审核台改正文案/属性时，diff 记录为高置信映射（human 写，active）；后台 job 从 reviews reason=文案错误 聚合模式 | 映射必须逐条可回查 review_id；字典值经验只作候选建议，最终仍必须命中 Ozon API 字典（现有硬闸） |
| **④ 生图 prompt 优化** | 历史失败模式：哪类 prompt 语句导致形变/文字闯入、哪类 slot 一次通过率高（layer=eval_note） | prompt_versions 机制已覆盖版本本体；记忆层只存跨版本"模式"结论（eval_report 写） | 任何模式结论必须附 slots-report/reviews 数据指针；prompt 改动走 draft→审批→canary 通道，记忆不直接改 prompt_versions |
| **⑤ 审核打回修复** | 打回原因→有效修复动作的历史成功率（如"商品形变→只重生 slot3 成功率 85%"） | 每次修复闭环完成（regenerate→再次 review）后写 episodic+聚合条目 | 映射建议落 suggestions，planner 仍按规则执行；无匹配历史→human_task(其他)，不臆造修复路径 |
| **⑥ 上架后反馈** | winning 品特征、下架/降价的处置效果、Premium 查询词解读结论（V2；Premium 未开通前只有 reviews+trend_check 三源，盲区如实标注） | post_launch_watch 数据落 events/metrics；处置效果结论由人工确认后写 | 处置结论必须关联 listing/event evidence；Premium 数据盲区不允许 LLM 用想象补齐 |

### 4. 存储与检索：SQLite 最小实现与向量取舍（官方依据）

**Phase 1（默认）：纯 SQLite + JSON + 简单排序。** 条目量预估数百到数千，`memory_items` 上建 `(layer,status,scope)` 索引后 ORDER BY score 足够；FTS5（SQLite 官方内置扩展，参考 sqlite.org/fts5.html）在需要关键词检索时启用——俄语按 unicode61 分词在小规模下可用，需实测。

**何时需要 embedding：** 当 (a) 条目 >1~2k 且 recall 靠关键词命中率下降，或 (b) 出现"俄语同义/场景变体"式模糊查询（热词语义扩展、找货关键词），再引入向量。触发即上 **sqlite-vec**：

- 官方定位："extremely small, 'fast enough' vector search SQLite extension that runs anywhere"，纯 C 无依赖、vec0 虚表与 fts5 同库共存、MIT/Apache-2.0 双许可；**当前只有暴力检索**（作者原文 "Only brute-force search for now"），10 万条 1536 维内可保 <100ms 量级，百万条浮点向量超时——对本项目"单店千条级记忆"是甜点区。pre-v1 有 breaking changes，需锁版本。（来源：官方 README + 作者 v0.1.0 发布文 + LICENSE-APACHE，§7）
- **pgvector**：PostgreSQL License、精确+HNSW/IVFFlat、成熟——但只有项目升 Postgres 后才有意义（升格触发条件沿用 harness 报告 §8.4：>4-6 并发 worker/日任务>10⁴/多机）。现在为记忆层单独引 Postgres 违反"零新服务"原则。
- **Qdrant**：qdrant-client 官方 README 明示 local mode（`:memory:`/path=）"useful for development, prototyping and testing"，规模化要切 server 模式（独立 Docker 服务）——**不为记忆层引入**。注意 Mem0 OSS 默认就用"本地 Qdrant /tmp/qdrant"（等效进程内库）+ SQLite history，若未来选 Mem0 OSS 可接受此形态，但多一个 qdrant-client 依赖。

**推荐：memory_items（正本）→ FTS5（可选关键词索引）→ sqlite-vec（可选、锁版本、可重建）**，检索融合借鉴 Mem0 新算法官方描述的"多信号并行（语义+BM25+实体）再融合"思路，用 SQL 过滤+FTS5+vec 三路召回后按 §2 排序公式重排。

### 5. GitHub 项目比较（全部经官方 README/LICENSE/官方文档一手核验）

| 维度 | **Mem0** | **Zep/Graphiti** | **Letta (MemGPT)** | **LangMem** | **Cognee** | （发现）Memori |
|---|---|---|---|---|---|---|
| 仓库/license | mem0ai/mem0，**Apache-2.0** | getzep/graphiti，**Apache-2.0**（Zep 本体是商业 managed/BYOC） | letta-ai/letta → **letta-ai/letta-code**（Apache 系；Python V1 server 已归档至 archive 分支） | langchain-ai/langmem，**MIT** | topoteretes/cognee，**Apache-2.0** | MemoriLabs/Memori，Apache-2.0 |
| Python/本地函数接入 | ✅ `pip install mem0ai`，`Memory()` 几行即用 | ✅ `graphiti-core`，**但需外接图库 Neo4j 5.26/FalkorDB/Neptune**（Kuzu 已弃用；FalkorDB Lite 内嵌要 Python≥3.12） | ❌ 已转 TypeScript 产品（npm），Letta Cloud 默认；非 Python 库 | ✅ 纯函数核（create_memory_manager/prompt_optimizer）不绑存储 | ✅ `cognee[gliner]` 本地免 LLM 可跑，Python 3.10-3.14 | ✅ Python SDK，但主流程要 MEMORI_API_KEY（云） |
| SQLite/自托管 | OSS：默认 OpenAI gpt-5-mini + text-embedding-3-small + **本地 Qdrant(/tmp/qdrant)** + SQLite history；vector store 官方列表为 qdrant/pgvector/chroma/redis/milvus…（**无 SQLite 向量后端**）；自托管 server 默认 Postgres+pgvector | 自托管=自己运营图数据库+检索层 | letta server 本地可跑，但是整只 agent harness 产品 | 有状态层绑定 LangGraph BaseStore：InMemoryStore(重启丢失)/AsyncPostgresStore，**无官方 SQLite Store**；核心函数层任意存储可用 | 图+向量双栈；官方明示"整套跑在单 Postgres"是 demo 特性，生产版是付费产品 | BYODB 可选自带库，但云中心化 |
| 时序/证据/删除 | 新算法（2026-04）官方：**单遍 ADD-only 提取（无 UPDATE/DELETE）、实体链接、多信号检索、时序推理**；基准分来自 managed platform，官方自注 OSS "not identical numbers" | **最强**：事实带有效期窗口、变更时旧事实 invalidated 而非删除、双时态、episodes 全程可溯源（Zep 论文 arXiv:2501.13956） | memory blocks 可由 agent 自改（sleeptime"dreaming"），MemFS 用 git 追踪上下文 | 官方三分 semantic/episodic/procedural；profile vs collection；hot path vs background 写入对比表；namespace 元组 (org,{user_id},app) | remember/recall/improve/forget 四动词 + session distillation（会话蒸馏固化为知识） | entity/process/session 三级 + attributes/events/facts… 增强在云端做 |
| HITL/权限 | 无记忆审批流（自托管 server 有 API key 鉴权，属传输层） | 无（参考 server 官方自曝"无鉴权/无注入防护，不可暴露"） | permissions 面向工具动作，非记忆条目 | 无内置，需外接 | 多用户/权限在 API 层，默认单用户 | 云侧配额管理 |
| 依赖/运维 | 中：向量库+嵌入+提取 LLM 管线，服务化需 Docker | **重**：图数据库服务 + 检索自建 | 重（产品级 harness + 云） | **轻**：pip 包+所选 LLM；LangGraph 仅在有状态层需要 | 中重：dlt 成为核心依赖，管线黑盒，表结构不可控 | 轻 SDK 但云依赖 |
| 模型锁定 | 提取靠 LLM；可换 openai 兼容（litellm/ollama 等），Qwen 可试但官方未列首选 | 官方警告：非 OpenAI/Anthropic/Gemini 提供方"可能导致输出 schema 错误与摄取失败"，小模型尤甚 | 其自有运行时 | 沿 LangChain 模型串，qwen 可接 | 默认 OpenAI，可换 Ollama/其他 provider | 支持列表含主流，Qwen 未列 |

**三档归类：**

- **参考架构（采纳思想不引系统）**：Graphiti/Zep——事实有效期窗口 + invalidated-not-deleted + episodes 溯源，是本报告 supersede/evidence_json/valid_until 的理论蓝本；Letta——memory blocks 分块与"agent 可自改记忆但必须留审计"对应我们的 candidate+审批；Mem0——多信号融合检索、ADD-only+时序推理、user/session/agent 分级对位 scope 设计；Cognee——remember/recall/improve/forget 动词命名与会话蒸馏。
- **可库引入（唯一推荐）**：LangMem **功能核**（MIT、create_memory_manager/create_prompt_optimizer 不依赖任何存储），作可选后台提取器，产物落自家 memory_items；不用其 BaseStore/工具层。备选：不引依赖、自写约百行提取 prompt（多数收益相同）。
- **当前不建议引入**：Mem0 OSS 整包（向量库+提取管线自带存储栈，与"SQLite 唯一事实源"冲突；graph memory 官方明示 Platform-only 不在 OSS；官方基准分是平台版数字）；Letta（TS 产品+云默认）；Zep（商业）；Cognee（依赖重、schema 黑盒）；Memori（云 API 依赖）。

**明确推荐组合：自建 memory_items + 六动词 API（SQLite 正本）→ LangMem 功能核作可选后台提取器（或自写提取 prompt）→ Graphiti 式时序/证据模型（概念采纳）→ FTS5/sqlite-vec 分阶段检索。** 每一块都可被替换且不产生数据迁移锁定（embedding 可重建，表 schema 自持）。

### 6. 分阶段 PoC 与可验证验收（不改业务代码）

| 阶段 | 内容 | 可验证验收（全部写成 pytest/脚本断言） |
|---|---|---|
| **P0（1-2 天）** | 迁移脚本建 `memory_items`+`budgets(memory:write)`；`memory.py` 六动词；独立测试夹具库，不接业务 worker | ① 无 evidence 的 remember 抛错；② LLM 主体写后 status=candidate，直接 UPDATE active 的路径被 API 拒绝；③ supersede 后 recall 只回新条目且旧条目链可查；④ forget 为软删、审计可查；⑤ 超日写预算（20 条）熔断 |
| **P1（1 周）** | 写时机接线：reviews 落库→后台提取 job→candidate→human_tasks(memory_approval)（审核台加待审批视图）；recall 注入 generate_spec/source_product 工具输入（≤500 token） | ① 30 条小 golden 查询集人工标注 recall top3 相关性 ≥80%；② 未审批 candidate 永不出现在 recall 结果（权限测试）；③ 注入经验后现有 tests/ 回归全绿；④ 每条 recall 结果携带 evidence 指针且可跳转 |
| **P2（1 周）** | 引入 sqlite-vec（锁版本）+ 三路融合重排；过期自动失效 job | ① 向量开/关的 recall 命中对比报告（≥30 条样本）；② 删除 vec 表→由 memory_items 全量重建→recall 结果一致（事实源不变量）；③ 过期条目自动 expired 不再返回；④ 冲突条目 conflict=true 正确标注 |
| **P3（持续）** | 防污染演练与效果度量：注入假经验→周抽检捕获→批量 invalid→验证 recall 不再返回；eval_report 增加"记忆命中→采纳→审核通过率变化"漏斗 | ① 污染注入 24h 内被抽检流程标记；② 假经验清理后 eval 漏斗无残留影响；③ 连续 2 周记忆写入全部可溯源（created_by+evidence 100% 非空） |

### 7. 风险、未验证假设与官方来源

**风险/未验证（如实标注）：**
1. Letta 已整体转向 TypeScript 产品形态（本轮 README 一手确认），Python 版本文档大量失效——若未来要用其概念，只能按 letta-code 新文档重查。
2. Mem0 OSS 官方配置文档未列 SQLite 向量后端（本轮 docs 一手确认）；历史版本包内是否有 SQLiteVec 实现未验证，若坚持 Mem0 需读包源码。
3. Mem0 官方自注：新算法基准分数来自 managed platform，OSS 结果"directionally similar but not identical"——**不得**直接引用其 LoCoMo 92.5 等数字作为 OSS 预期。
4. sqlite-vec：pre-v1 breaking changes、ANN 未实现（官方 roadmap #25）、二值量化 ~95% 精度为作者经验值非承诺。
5. 俄语 FTS5 分词与 Qwen 走各家库的 openai 兼容路径（Graphiti 官方明示非首选提供方可能 schema 失败；Mem0 未列 Qwen）——凡引入外部库的提取/嵌入环节都需先用百炼 Qwen 实测。
6. LangMem 社区较小、发版少，若引入需 pin 版本并准备好"自写提取 prompt"降级路径。
7. 本项目记忆条目量级可能永远到不了"需要向量"的阈值——P2 的存在不等于必然启用，以 P1 的命中率数据决定。
8. Graphiti 参考其官方论文的双时态字段名（valid_at/invalid_at）属文献概念引用，未核对其源码 schema 细节。

**官方来源（全部一手）：**
- Mem0：README https://github.com/mem0ai/mem0/blob/main/README.md ；LICENSE(Apache-2.0) https://github.com/mem0ai/mem0/blob/main/LICENSE ；OSS 总览 https://docs.mem0.ai/open-source/overview ；配置（向量库列表/服务端默认 pgvector）https://docs.mem0.ai/open-source/configuration ；Python 快速开始（默认组件清单）https://docs.mem0.ai/open-source/python-quickstart.md ；OSS v2→v3 迁移说明 https://docs.mem0.ai/migration/oss-v2-to-v3
- Graphiti/Zep：README https://github.com/getzep/graphiti/blob/main/README.md ；LICENSE(Apache-2.0) https://github.com/getzep/graphiti/blob/main/LICENSE ；论文 https://arxiv.org/abs/2501.13956
- Letta：主仓库 README（archive 分支说明）https://github.com/letta-ai/letta/blob/main/README.md ；letta-code README https://github.com/letta-ai/letta-code/blob/main/README.md ；MemGPT 论文 https://arxiv.org/abs/2310.08560
- LangMem：README https://github.com/langchain-ai/langmem/blob/main/README.md ；LICENSE(MIT, ©2025 LangChain) https://github.com/langchain-ai/langmem/blob/main/LICENSE ；概念指南 https://langchain-ai.github.io/langmem/concepts/conceptual_guide/
- Cognee：README https://github.com/topoteretes/cognee/blob/main/README.md ；LICENSE(Apache-2.0, ©2024 Topoteretes UG) https://github.com/topoteretes/cognee/blob/main/LICENSE
- sqlite-vec：README https://github.com/asg017/sqlite-vec/blob/main/README.md ；LICENSE-APACHE https://github.com/asg017/sqlite-vec/blob/main/LICENSE-APACHE ；作者 v0.1.0 发布文（暴力检索/性能/量化）https://alexgarcia.xyz/blog/2024/sqlite-vec-stable-release/index.html
- pgvector：README https://github.com/pgvector/pgvector/blob/master/README.md ；LICENSE(PostgreSQL License) https://github.com/pgvector/pgvector/blob/master/LICENSE
- Qdrant 客户端：README（local mode 定位）https://github.com/qdrant/qdrant-client/blob/master/README.md
- Memori：README https://github.com/MemoriLabs/Memori/blob/main/README.md
- SQLite FTS5（设计引用，未本轮抓取）：https://sqlite.org/fts5.html
- 本仓库：`pipeline.py`（六表/schema/_migrate/record_review）；`docs/research/ozon-agent-harness-deep-research.md`（七表设计稿、防污染六规则、分层雏形 §6）；`docs/research/ozon-agent-architecture-assessment.md`（状态机/闸门边界）；`ROADMAP.md`（V2/V3 保险丝与自优化规划）

## Contradictions

1. **Mem0 冲突处理新旧口径**：README 新算法段明确"单遍 ADD-only、无 UPDATE/DELETE、记忆累积不覆写"，而其"Memory operations"文档仍宣传 full CRUD API——两者并存，说明 OSS API 面与默认算法行为可能不一致；采用前必须实测 add 同一事实两次的落库行为。
2. **Mem0 基准分**：官方同时展示高分数字并自注"分数来自 managed platform，OSS 不会有相同数字"——营销数字与 OSS 实际能力存在官方承认的落差。
3. **Cognee "单 Postgres 全栈"**：README 宣传可全栈跑单 Postgres，同段又自警"该形态为 demo 特性，生产版是付费产品"——官方自认功能与产品边界矛盾，按"不可依赖"处理。
4. **Letta 命名漂移**：MemGPT→Letta→Letta Code（npm/TS）+ Python server 归档，官方 README 与历史文档互相指路——检索其资料时必须按新仓库时间线取材。

## Missing evidence

- Mem0 OSS 在新 ADD-only 算法下，`update/delete/history` 实际行为与保留度（未读包源码/未实测）。
- sqlite-vec 当前版本（v0.1.9 之后）是否已落地 metadata 过滤与 partition key 的完整语义（README 已宣传"metadata, auxiliary, or partition key columns"，与 2024 博客 roadmap 的未落地状态存在时间差，需按所用版本实测）。
- Qwen（百炼 openai 兼容端）在 Mem0/LangMem 提取 prompt 下的抽取质量：零实测。
- 俄语文本在 FTS5 unicode61 下的检索质量：零实测。
- Graphiti 双时态字段在源码中的精确 schema（论文级引用，未核源码）。

## Next steps

1. P0 先行：`memory_items` 迁移脚本 + `memory.py` 六动词 + 五条断言测试（不碰业务代码）。
2. 用百炼 Qwen 跑 20 条真实 reviews 的提取实测（LangMem 功能核 vs 自写 prompt 对比），选定后台提取器并 pin 版本。
3. 实测俄语 FTS5 召回质量，决定 P2 是否真的引入 sqlite-vec。

---

## Acceptance

- criterion-1（简明结论+残余风险）：Summary 给出推荐组合，§7/Missing evidence 给出 8 项风险与 5 项未验证假设。
- 外部事实全部一手来源（官方 README/LICENSE/官方文档），二手榜单未使用；研究者设计与直接证据已分层标注。
