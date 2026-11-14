# Research: GitHub 开源 Agent/编排项目用于 ozon-pipeline MVA 演进

> 调研日期：2026-11-12。范围：只读官方 GitHub README、官方文档、官方源码/配置等一手来源；未读取密钥，未执行业务代码、外部 API 写操作或仓库业务代码修改。

## Summary

结合 `docs/research/ozon-agent-architecture-assessment.md` 的 MVA，当前最小落地不应先引入重型平台，而应保留现有 SQLite 状态机与 Python worker，在外层加 `tasks/tool_calls/human_tasks/artifacts` 薄编排层；若要引入库，优先考虑 **PydanticAI** 做 typed tools / structured output / eval / tracing，谨慎参考 **LangGraph** 的 checkpoint + interrupt 模式。**Temporal/Prefect/Dify/OpenAI Agents SDK** 都有可借鉴能力，但当前更适合作为参考架构或后续阶段选型，不建议立即替换现有业务流水线。

## Findings

### 1. 推荐排序与结论

| 排名 | 项目 | 结论 | 最小用途 |
|---:|---|---|---|
| 1 | **PydanticAI** | **可作为库引入** | 把现有 Python 函数包装为 typed tools；统一 Pydantic 输入输出、模型切换、eval/OTel；不接管 SQLite 状态机。 |
| 2 | **LangGraph** | **参考架构 / 谨慎作为库引入** | 学习 graph state、checkpoint、interrupt/HITL；若后续需要显式 agent graph，可试点单商品流程，不要一次重写全部 worker。 |
| 3 | **Temporal** | **参考架构；不建议当前立即引入** | 长任务、精确恢复、Signal/Update/HITL 的最终形态参考；等单机 SQLite runner 跑稳后再评估。 |
| 4 | **Prefect** | **参考架构；可选调度/可观测层** | 批处理/定时/工作池/运行 UI；不是 Agent brain。当前用它会比 SQLite runner 重。 |
| 5 | **OpenAI Agents SDK** | **参考 guardrails/HITL/tracing；不建议当前主引入** | 函数工具、审批暂停、RunState、guardrails 设计可学；但项目现有 Qwen/百炼、多平台自托管优先，OpenAI 生态功能会带来锁定。 |
| 6 | **Dify** | **参考产品形态；不建议嵌入当前系统** | 可参考 Workflow/RAG/LLMOps/UI 和自托管部署，但作为平台引入会重写/旁路现有 Python+SQLite 资产。 |

### 2. 维度对比

| 维度 | LangGraph | PydanticAI | OpenAI Agents SDK | Temporal | Prefect | Dify |
|---|---|---|---|---|---|---|
| 现有 Python 函数作为 tools | 中：节点/工具需要接入 LangChain/LangGraph 风格 | **低**：`@agent.tool` 包普通函数，签名/docstring 生成 schema，参数校验 | 低：FunctionTool/装饰器可包函数 | 中：函数变 Activity，需 workflow/worker 结构 | 低-中：`@task/@flow` 装饰普通函数 | 高：通常通过 HTTP/API/插件/工作流节点接入，不适合直接复用本地函数 |
| 持久化/可恢复任务 | checkpoint/store；官方称 durable execution 和恢复 | 自身 StepPersistence；durable execution 依赖 Temporal/DBOS/Prefect 等 | RunState 可序列化，sessions；外部副作用仍需幂等 | **强**：durable orchestration engine，workflow history | 中强：flow/task 状态、retries、result persistence | 平台内 workflow/logs，依赖其服务栈 |
| HITL | **强**：interrupt 暂停，checkpoint 后恢复 | 有 deferred tools / HITL approval | **强**：tool approval interruptions + RunState | Signal/Update/wait_condition 可实现强 HITL | 有手动/状态控制但非 agent 原生 | 平台 UI/工作流能力强，但与现有 review_server 重叠 |
| SQLite/自托管 | 有 SqliteSaver 示例，生产更偏 Postgres/LangSmith | 可本地运行；持久化 store 自选；OTel 可自托管 | SQLite session 示例；默认 OpenAI tracing/Responses 生态 | 需 Temporal server；自托管可行但重 | 自托管 server；默认服务和 UI 较重 | Docker Compose 自托管，Postgres/Redis/向量库/沙箱等多服务 |
| 权限/guardrails | interrupt + 图节点策略；需自建权限模型 | guardrails/capabilities 可组合；仍需业务 policy | **强**：input/output/tool guardrails、approval | 需在 workflow/activity 层自建 | 需自建业务权限 | 有企业/RBAC 信息，但社区版与业务权限集成成本高 |
| tracing/eval | LangSmith 生态强；开源 OTel需另接 | **强**：Logfire/OTel、Pydantic Evals | 内置 tracing；OpenAI tracing 体验强 | Prometheus/OTel/Visibility API | UI/日志/状态/事件强 | LLMOps、Opik/Langfuse/Phoenix 集成 |
| 生产运维重量 | 中：库轻，LangSmith/Agent Server 可变重 | 低-中：库轻；durability 后端可选 | 低-中：SDK轻；OpenAI功能依赖外部服务 | **高**：server、worker、namespace、history 运维 | 中-高：server/cloud、worker、work pools | **高**：完整应用平台多容器 |
| license | MIT badge（README） | MIT（pyproject） | MIT（pyproject） | MIT badge（README） | Apache 2.0 | Dify Open Source License，基于 Apache 2.0 但有附加条件 |
| 模型厂商锁定 | 中：LangChain 多模型，但 LangSmith 生态绑定明显 | **低**：README 称 almost every model/provider，string swap | 中-高：README 称 provider-agnostic，但 Hosted tools/Responses/Tracing 偏 OpenAI | 无模型绑定 | 无模型绑定 | 多模型，但绑定 Dify 平台抽象 |

### 3. 分项目评估

1. **Claim:** PydanticAI 是当前最适合“小步引入”的库：它把 Python 函数工具、Pydantic 类型、结构化输出、模型抽象、eval/OTel 放在一个 Python SDK 内，接入成本最低。**Sources:** [PydanticAI README](https://raw.githubusercontent.com/pydantic/pydantic-ai/main/README.md), [pyproject license](https://raw.githubusercontent.com/pydantic/pydantic-ai/main/pyproject.toml). **Support:** direct evidence. **Confidence:** high.  
   - README 直接展示 `@agent.tool` 普通函数接收 `RunContext`、函数签名/docstring 成为工具 schema、参数先校验、返回 typed output。  
   - README 明确“Any model, one Python API”“Virtually every model and provider”，对现有百炼/Qwen、未来 OpenAI/Claude/Ollama 切换更友好。  
   - 对 ozon-pipeline：`pricing.calc`、`content_agent.validate`、`sourcing_agent.search_vl/search_text` 等可先包成 typed tools；但状态流转、上架、定价仍由现有确定性代码和 SQLite policy 控制。

2. **Claim:** LangGraph 最值得作为 Agent 编排参考，尤其是 checkpoint、SQLite saver、interrupt/HITL；但直接替换现有状态机风险较高。**Sources:** [LangGraph README](https://raw.githubusercontent.com/langchain-ai/langgraph/main/README.md), [Persistence docs](https://docs.langchain.com/oss/python/langgraph/persistence), [Interrupts docs](https://docs.langchain.com/oss/python/langgraph/interrupts). **Support:** direct evidence + researcher inference. **Confidence:** high.  
   - README 称其是“low-level orchestration framework for building stateful agents”，支持 durable execution、HITL、memory、LangSmith tracing。  
   - Persistence 文档说明 checkpointers 保存 thread graph state，stores 保存跨 thread 数据；并提示生产用持久 checkpointer，`SqliteSaver` 更偏本地开发。  
   - Interrupt 文档展示 `interrupt()` 暂停、同一 `thread_id` 恢复、审批/编辑状态/工具调用中断等模式。  
   - **研究者推断：** ozon 已有 `products.state/events/reviews/metrics_json`，若全量迁到 LangGraph 会重复建状态源；更小做法是借鉴其 interrupt 模式，在现有 `human_tasks` 表实现。

3. **Claim:** OpenAI Agents SDK 的 HITL、guardrails、tool approval、RunState 很适合参考，但作为主框架会和现有 Qwen/百炼、多模型自托管诉求产生摩擦。**Sources:** [OpenAI Agents SDK README](https://raw.githubusercontent.com/openai/openai-agents-python/main/README.md), [Tools docs](https://openai.github.io/openai-agents-python/tools/), [Guardrails docs](https://openai.github.io/openai-agents-python/guardrails/), [HITL docs](https://openai.github.io/openai-agents-python/human_in_the_loop/), [pyproject license](https://raw.githubusercontent.com/openai/openai-agents-python/main/pyproject.toml). **Support:** direct evidence + interpretation. **Confidence:** medium-high.  
   - README 称 SDK provider-agnostic，支持 tools、guardrails、HITL、sessions、tracing。  
   - HITL 文档说明 `needs_approval` 暂停工具调用，`RunState` 可序列化后恢复，并警告外部副作用需要目的端幂等键。  
   - Guardrails 文档区分 input/output/tool guardrails，可在工具执行前后拦截。  
   - **解释：** ozon 的真实上架、OSS 上传、付费生图非常适合学习此 approval pattern；但 Hosted tools、Responses、OpenAI tracing 等最佳体验仍偏 OpenAI，当前不宜把核心流程绑定进去。

4. **Claim:** Temporal 是最强的生产级可恢复执行引擎，但当前 MVA 直接引入运维重量过大。**Sources:** [Temporal Python SDK README](https://raw.githubusercontent.com/temporalio/sdk-python/main/README.md), [Python developer guide](https://docs.temporal.io/develop/python/core-application), [Workflow message passing](https://docs.temporal.io/develop/python/workflows/message-passing), [Observability](https://docs.temporal.io/develop/python/platform/observability). **Support:** direct evidence + interpretation. **Confidence:** high.  
   - README 定义 Temporal 为 distributed/scalable/durable/highly available orchestration engine，用于 asynchronous long-running business logic。  
   - Message passing 文档提供 Query/Signal/Update；Signal 可改 workflow state，Update 可改状态并返回结果，适合人工审批与状态控制。  
   - Observability 文档支持 Prometheus metrics、OpenTelemetry tracing、Visibility/Search Attributes。  
   - **解释：** 若未来每日大批量商品并发、跨天等待人工、worker 重启恢复成为硬需求，Temporal 是强候选；但现在单机 SQLite MVA 阶段先用 `tasks/tool_calls` 表足够。

5. **Claim:** Prefect 更适合 Python 数据/批处理工作流调度与可观测，不适合作为 Agent 决策核心。**Sources:** [Prefect official open source page](https://www.prefect.io/opensource), [Flows docs](https://www.prefect.io/docs/v3/concepts/flows), [Work pools docs](https://www.prefect.io/docs/v3/deploy/infrastructure-concepts/work-pools), [Write workflow docs](https://www.prefect.io/docs/v3/develop/write-tasks), [LICENSE](https://raw.githubusercontent.com/PrefectHQ/prefect/main/LICENSE). **Support:** direct evidence + interpretation. **Confidence:** high.  
   - 官方页面强调“Just add a decorator”“Durable Execution”“Observed by default”“Event-driven Automation”“Work Pools”，license 为 Apache 2.0。  
   - Flows 文档说明 `@flow` 跟普通 Python 函数相似，但自动跟踪状态、参数校验、retries、timeouts、deployment。  
   - Work pools 文档说明 worker/work pool/queue/concurrency 的部署调度能力。  
   - **解释：** 如果 ozon 后续要每日定时跑 trend_check、批量生成、失败重试 UI，Prefect 可考虑；但 MVA 的 per-product 状态机和 HITL 不需要先上 Prefect server。

6. **Claim:** Dify 是产品平台参考，不适合当作 ozon-pipeline 内部库引入。**Sources:** [Dify README](https://raw.githubusercontent.com/langgenius/dify/main/README.md), [Self-host docs](https://docs.dify.ai/getting-started/install-self-hosted/docker-compose). **Support:** direct evidence + interpretation. **Confidence:** high.  
   - README 定义 Dify 为 open-source LLM app development platform，包含 AI workflow、RAG pipeline、agent capabilities、model management、observability、Backend-as-a-Service；Docker Compose 自托管。  
   - README 的 Agent 特性包括 sandbox、命令/文件、skills、Marketplace tools、MCP/API；License 为 Dify Open Source License，基于 Apache 2.0 但有附加条件。  
   - 自托管文档显示默认栈含 PostgreSQL、Weaviate、沙箱、Docker Compose、多个 secret/服务配置。  
   - **解释：** 这会引入一套完整平台和 UI，与现有 `review_server`、SQLite 状态机、worker 函数边界冲突；可参考其产品体验，不建议迁移业务核心。

### 4. 结合本仓库 MVA 的最小落地建议

1. **不要先重写业务 worker，也不要把确定性逻辑交给 LLM。** 继续让 `pricing.calc`、状态流转、Ozon 上架校验、禁忌词/数字溯源、Nexscope 积分熔断做硬规则。  
2. **Phase 1：先在 SQLite 加薄任务层。** 新增 `tasks/tool_calls/artifacts/human_tasks`；单进程 `agent_runner step --pid N` 一次执行一个 task；每次调用现有函数前后记录 input/output/error/cost/model。无需 Temporal/Prefect。  
3. **Phase 1.5：引入 PydanticAI 只包 LLM/agent-facing 工具。** 用 Pydantic model 定义工具入参/出参，把 `content_agent.llm_spec + validate`、`sourcing_agent` 策略选择、`image_worker` 重试建议作为 typed tools；不要让 agent 直接执行 `listing_worker --real`。  
4. **Phase 2：用 LangGraph/OpenAI Agents SDK 的 HITL 模式做本地实现。** 不直接引入也可以：`human_tasks` 表存审批 payload，`review_server` 统一渲染；真实上架/OSS/付费高成本工具必须 human approval + idempotency key。  
5. **Phase 3：当任务跨天、并发和恢复成为瓶颈，再二选一：** 需要严肃 durable execution 选 Temporal；主要要调度 UI、cron、批处理和 work pools 选 Prefect。  
6. **Dify 仅用于产品形态参考。** 可借鉴它的 workflow UI、模型管理、LLMOps，但不要把 ozon 的本地 Python 函数和 SQLite 状态机塞进 Dify 平台。

## Contradictions

1. **OpenAI Agents SDK 的“provider-agnostic”与实际生态绑定并存。** README 明确称 provider-agnostic、支持 OpenAI Responses/Chat Completions 和 100+ LLMs；但官方工具文档中的 hosted tools、Responses shell/container、tracing/HITL 示例大量依赖 OpenAI Responses/Tracing 生态。结论：基础函数工具可多模型，最佳体验和高级工具有 OpenAI 锁定风险。  
2. **LangGraph 自称 durable execution，但本地 SQLite persistence 更偏开发用途。** README 强调 durable execution；Persistence 文档提示生产用 persistent checkpointer，并列 PostgresSaver 为生产建议、SqliteSaver 为 local file-based development。结论：可参考，不应误以为开箱即企业级队列。  
3. **Dify “open-source” 与 license 附加条件并存。** README 称 open-source LLM app platform，但 license 是 Dify Open Source License based on Apache 2.0 with additional conditions。结论：商用前需单独法务审查。

## Missing evidence

1. 未执行各框架 benchmark，也未在 ozon-pipeline 中做 PoC；接入成本判断来自官方 API 形态与本仓库 MVA 结构的研究者推断。  
2. `source_check` 自动校验 license 声明时返回了二手来源和“不明确”状态；最终 license 判断改用已抓取的官方 README/pyproject/LICENSE 内容人工核对。  
3. 未检查各项目当前 PyPI 版本兼容 ozon-pipeline 的 Python 版本和依赖锁冲突；真正引入前需 `uv/pip` dry-run。  
4. 未审查 Dify License 附加条款全文；只记录官方 README 的 license 类型，不给法律意见。

## Sources

- LangGraph README — https://raw.githubusercontent.com/langchain-ai/langgraph/main/README.md — 官方定位、durable/HITL/memory/tracing、MIT badge。  
- LangGraph Persistence — https://docs.langchain.com/oss/python/langgraph/persistence — checkpointer/store、SqliteSaver/PostgresSaver、生产持久化边界。  
- LangGraph Interrupts — https://docs.langchain.com/oss/python/langgraph/interrupts — HITL interrupt、resume、工具审批/编辑状态模式。  
- PydanticAI README — https://raw.githubusercontent.com/pydantic/pydantic-ai/main/README.md — tools、typed output、模型抽象、eval、OTel、durable engine 集成。  
- PydanticAI pyproject — https://raw.githubusercontent.com/pydantic/pydantic-ai/main/pyproject.toml — MIT license、Python 版本、extras。  
- OpenAI Agents SDK README — https://raw.githubusercontent.com/openai/openai-agents-python/main/README.md — agents/tools/guardrails/HITL/sessions/tracing 定位。  
- OpenAI Agents SDK tools docs — https://openai.github.io/openai-agents-python/tools/ — FunctionTool、hosted/local tools、approval 边界。  
- OpenAI Agents SDK guardrails docs — https://openai.github.io/openai-agents-python/guardrails/ — input/output/tool guardrails。  
- OpenAI Agents SDK HITL docs — https://openai.github.io/openai-agents-python/human_in_the_loop/ — tool approval、RunState、恢复与幂等风险。  
- OpenAI Agents SDK pyproject — https://raw.githubusercontent.com/openai/openai-agents-python/main/pyproject.toml — MIT license、依赖与 extras。  
- Temporal Python SDK README — https://raw.githubusercontent.com/temporalio/sdk-python/main/README.md — durable orchestration engine、workflow/activity/worker、MIT badge。  
- Temporal Python developer guide — https://docs.temporal.io/develop/python/core-application — Python SDK 官方入口与核心概念。  
- Temporal message passing — https://docs.temporal.io/develop/python/workflows/message-passing — Query/Signal/Update、人工审批和状态变更可用模式。  
- Temporal observability — https://docs.temporal.io/develop/python/platform/observability — Prometheus/OTel/Visibility/Search Attributes。  
- Prefect open source page — https://www.prefect.io/opensource — Python orchestration、durable execution、observability、Apache 2.0。  
- Prefect flows docs — https://www.prefect.io/docs/v3/concepts/flows — `@flow`、状态、retries、timeouts、deployment。  
- Prefect work pools docs — https://www.prefect.io/docs/v3/deploy/infrastructure-concepts/work-pools — worker/work pool/queue/concurrency/self-hosted。  
- Prefect workflow docs — https://www.prefect.io/docs/v3/develop/write-tasks — `@task/@flow`、timeouts、cache/retry 配置。  
- Prefect LICENSE — https://raw.githubusercontent.com/PrefectHQ/prefect/main/LICENSE — Apache 2.0 全文。  
- Dify README — https://raw.githubusercontent.com/langgenius/dify/main/README.md — 平台定位、Workflow/RAG/Agent/LLMOps、自托管、license。  
- Dify self-host docs — https://docs.dify.ai/getting-started/install-self-hosted/docker-compose — Docker Compose、自托管服务栈、硬件/安全配置。

## Next steps

1. 做一个不改业务 worker 的 1-2 天 PoC：SQLite `tasks/tool_calls/human_tasks` + `agent_runner step --pid`，只包 `pricing.calc`、`content_agent.validate`、一个 mock LLM 工具。  
2. 若要试引入第三方库，先只加 PydanticAI 到隔离 PoC 分支，验证依赖、Qwen/百炼 provider 接法、typed tool 记录和 eval 输出。  
3. 同步设计真实写操作权限：`listing_worker` 默认只 dry-run；外部写必须 human approval + idempotency key + 每日限额。
