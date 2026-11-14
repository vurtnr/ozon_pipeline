# Ozon Pipeline 第一性原理三路评审

> 日期：2026-11-13。评审角色：Agent Harness 架构师、UI/UX 设计总监、跨境电商运营专家。  
> 方法：三方独立阅读源码、ROADMAP 与既有研究，再合并交叉结论。ComfyUI 不在本次范围。  
> 标记：`[code-verified]` 表示源码事实；`[external-verified]` 表示官方/一手资料；`[agent-suggested]` 表示评审建议，尚未由用户批准。

## 0. 总结论

当前最需要的不是继续增加 Agent 基础设施，而是恢复业务链路的真实性：

1. **趋势与经济状态不能代表尚未执行的检查。** `sync_to_db.py` 会在 trend/econ worker 未实现时仍推进到 `checked/profitable`；审核台还会显示“利润为正”。[code-verified]
2. **import 成功不等于商品可售。** 当前 `listing_worker.py` 在 import 成功后即写 `listed`，但库存、内容审核状态和真正可售状态未闭合。[code-verified]
3. **在修复这两个问题前，不应把 PG、LangMem、复杂记忆治理、事件评分或 Prompt 自动优化列为 P0。** 它们不能提高错误候选的商业价值。[agent-suggested]
4. **Web 自托管形态仍适合持续 Harness，但当前 UI 在审批后的可见性、错误恢复、并发审核和安全边界上尚未达到该形态的要求。**[code-verified]

建议把总原则改为：

> 先保证“每个状态都是真的、每笔成本有依据、每张卡最终可售且可复盘”，再逐步增加自主编排和学习能力。[agent-suggested]

---

## 1. 最高优先级发现

| 优先级 | 发现 | 证据 | 业务/系统后果 | 最小修正 |
|---|---|---|---|---|
| Critical | trend/econ 状态被空闸放行 | `sync_to_db.py:156-185`；`unit_econ.py` 不存在 | 未算账候选进入付费生图，`profitable` 状态失真 | 接 `pricing.calc` 硬闸；trend 告警进入人工处置；存量状态重算 |
| Critical | 审核台显示未核算的“利润为正” | `review_server.py` 的 `econ_tag` | 人工被误导批准 | 改为“未核算”；approve 必须绑定定价快照 |
| Critical | `listed` 不代表可售 | `listing_worker.py:88-134`；Ozon 官方上传文档 | 无库存/审核驳回时仍显示已上架，无法产生销售真值 | 轮询 moderation，设置 realFBS 库存，新增 `on_sale` |
| High | trend_check 批量查询与客户端已知限制冲突 | `trend_check.py` 与 `nexscope_client.py` 的批量实测注释 | 返回空数据仍可能计费，告警失效 | 改一词一查，受积分熔断约束；加入已知促销品回归 |
| High | 审核写入与状态推进不是原子操作 | `pipeline.py:196-211`；`ThreadingHTTPServer` | 双击/过期页签可能产生重复 review、非法状态推进或连接错误 | 单事务审核、条件 UPDATE、非法流转返回错误而非 `sys.exit` |
| High | approved 卡从 UI 消失 | `review()` 只查询 review_pending/imaging | 上架失败不可见，最高风险步骤无法恢复 | 看板增加“已过审/待上架/上架失败”区块 |
| High | 定价参数会系统性高估利润 | `pricing.py` 默认广告率 0；编辑器汇率 12.47 硬编码；缺国内运费/履约损耗提示 | 建议售价失真，佣金/汇率偏差可吞掉利润 | 准备金显式化、汇率带日期、成本快照入审计 |
| High | 类目近似可能污染佣金、属性、合规和流量 | `select_products.py` 的叶名映射；`keyword_library.py` refresh 覆写 seed | 错类目会引发定价错误、属性失败和认证风险 | 用竞品反查真实 category/type；新类目首验 |
| High | 货源被自动锁定 top1 | `sync_to_db.py` 只写 `suppliers[0]` | 高销量不等于可承接，MOQ/发货/断货风险未审 | 展示 top3–5，人工选源并记录采购模式 |
| High | 真实外部写缺硬保险丝 | `listing_worker.py` 无日限/429 退避；页面日限只展示 | Runner 自动化后可能超限或触发连续失败 | V1 日硬顶、429 退避、连续失败暂停 |

---

## 2. 对现有技术架构的重新裁决

### 2.1 PostgreSQL：合理方向，但不应与业务修复争优先级

此前 PG 报告推荐 PG-first；本轮架构评审认为当前 V1 规模（首批 ≤20 卡、单人、低并发）尚未达到 PG 的必要性门槛。

**合并结论 [agent-suggested]**：

- 若近期目标仍是验证首批商品的真实利润与可售闭环：保留 SQLite，先完成 WAL、busy timeout、原子审核和 UTC。
- 若已确定立即进入多 Worker、持续运行、远程 Web 审核：PG 是合理底座，七张新表可直接建在 PG，但仍需迁移现有六表。
- 不应把“PG 支持 SKIP LOCKED/pgvector”误当成当前必须迁移的理由；它们解决的是规模问题，不是业务真实性问题。

### 2.2 LangMem 与记忆层：推迟到存在足够经验数据之后

当前只有极少量真实审核与上架样本，复杂记忆召回、Prompt 优化和防污染体系缺少数据基础。

**合并结论 [agent-suggested]**：

- P0 不引入 LangMem，不建复杂 Memory 生命周期。
- 先积累 `reviews/tool_calls/events/真实销售结果`。
- 有 30–100 条可用经验后，再用自写提取 Prompt 与 LangMem 做对比。
- 无论采用 PG 还是 SQLite，业务事实与审核真值都不交给记忆框架管理。

### 2.3 PydanticAI / APScheduler：只保留当前真正需要的部分

- PydanticAI 可在需要 typed tools/结构化输出时引入，不必为了“Harness”名称接管现有 Runner。
- APScheduler 可采用内存调度、启动时注册 Job；任务持久化由 `tasks.not_before` 和业务表承担。
- 当前不需要持久化 JobStore、独立消息队列或 Temporal。

### 2.4 Runner：需要，但先缩到四张表

最小落地只保留：

- `tasks`：待执行工作、attempt、lease、幂等键；
- `tool_calls`：输入输出、模型、成本、错误；
- `artifacts`：产物路径与 hash；
- `human_tasks`：人工阻塞与审批。

`signals/budgets/prompt_versions/memory_items` 随对应功能落地再增加。预算初期可由 `credit_log + tool_calls` 聚合，避免双账本。

### 2.5 状态推进必须有唯一写者

当前 `sync_to_db` 按 artifacts 推进状态；拟议 Runner 按任务完成推进。两者并存会产生状态分叉。

**建议 [agent-suggested]**：

- Runner 的任务完成是唯一自动推进途径。
- `sync_to_db` 降级为 metrics/artifact 回填工具，不再自动 advance。
- `products.canonical_query` 取代代码中的别名映射。

---

## 3. 修订后的业务主流程

```text
① 选品方向与查询计划
   LLM 提方向/假设/预算 → 用户批准计划，不等于需求已验证

② 词库与候选
   真实指标查询 → 确定性漏斗 → 记录 selected_at，不提前烧毁词

③ 找货与货源比选
   1688/VL 搜索与同一性初筛 → top3–5 → 人工选源
   同时明确：轻备货还是接单后采购

④ 趋势核验
   单词重查与基线对比 → 告警由人工决定暂缓/继续/淘汰

⑤ 经济硬闸（花钱生成媒体之前）
   佣金 + 物流 + 汇率 + 国内运费 + 推广准备金 + 风险参数
   → 定价快照落库 → 不满足下限则终止

⑥ 文案与生图
   AI 生成 → 机械校验 → 文字/数字双闸 → 逐项处理 need_confirm

⑦ 图审与上架编辑
   approve 必须绑定经济快照；打回生成明确修复任务

⑧ 上架到可售
   dry-run → 人工批准 import → moderation 轮询
   → 设置 realFBS 库存 → on_sale / 驳回转人工

⑨ 最小反馈闭环
   rating-by-sku + trend + 周度人工录入订单/取消/迟发/实际成本
```

### V1 必须人工的事项 [agent-suggested]

1. 选品方向与数据预算；
2. 新类目确认；
3. 供应商与采购模式；
4. 趋势告警处置；
5. 定价快照确认；
6. 图审与逐项内容确认；
7. 真实 import 与库存设置；
8. 周度账户健康和成本复盘。

---

## 4. UI/UX 重审结论

### 4.1 当前做得正确的部分

- 图审三动作与死因枚举形成明确闭环；
- `manual > LLM` 的信任分级文案清晰；
- Trace、空态和打回后的恢复提示可复用；
- 现有本地 Web 资产可演进，不需要重做桌面端。

### 4.2 必须优先修复的问题

1. **approve 双击/过期页签安全性**：非法状态不能导致重复 review 或处理线程异常。
2. **approved 可见性**：卡片通过图审后必须仍能在“待上架/失败”区域找到。
3. **定价上下文**：汇率默认值、更新时间和成本假设必须可见。
4. **错误呈现**：编辑器 API/网络错误不能停留在永久“加载中”。
5. **指标口径**：全生命期过审率不能与“今日”指标并排而不标周期。
6. **无障碍基本项**：图片 alt、键盘按钮、模态 Esc/焦点归还、异步错误 aria-live。

### 4.3 最小信息架构

```text
顶栏：待办数 · 今日上架/上限 · 成本 · 30d 过审率

Tab 1：待我处理
  统一 human_tasks 收件箱；kind 是徽章，不拆七个队列
  listing_import 单独提高确认等级，显示风险与日限

Tab 2：流水线
  现有 board + 已过审/待上架 + 上架失败 + 失败任务重放

Tab 3：校准（有真实数据后再出现）
  Prompt 版本对比 · 记忆周抽审 · 阈值/信号源变更

单品 Trace：events + reviews + tasks/tool_calls + 人工重放
```

### 4.4 不建议做的界面

- 不做逐品选品滑卡审批；选品只审方向、阈值变更和预算。
- 不做七个独立人工队列。
- 不做 Prompt/记忆自由编辑器；只展示 diff、证据和审批动作。
- 不做通用 BI 仪表盘、多用户角色体系、主题切换或动效。
- 不在缺少真实数据时编造成本或利润指标。

---

## 5. 修订后的实施顺序

### P0：业务真实性与安全修复

- 删除假“利润为正”，接经济硬闸和定价快照；
- 修复 trend 单词查询与预上架核验；
- 修复审核原子性、并发与错误处理；
- 增加 approved/上架失败可见性；
- 修复类目来源、货源比选和汇率时效。

**验收**：已知负利润品不能进入生图；已知促销品能触发人工告警；并发审核无重复记录；任何 approved 卡都能从 UI 找到。

### P1：上架到可售与反馈

- import → moderation → stocks → `on_sale`；
- V1 日上架硬顶、429 退避和人工暂停条件；
- 上架后 rating/trend/订单与成本最小复盘。

**验收**：至少一张卡真实可售；审核驳回会进入人工队列；能够记录真实订单与成本结果。

### P2：最小 Harness

- 四表 + 单进程 Runner + 规则 Planner；
- 统一人工收件箱；
- 崩溃恢复、幂等、失败任务重放；
- 非本机访问前补认证与审计。

**验收**：kill -9 后无重复付费/外部写；单商品全链路可由 Runner 推进到人工节点。

### P3+：按数据触发高级能力

- 有足够信号与转化数据后再做事件评分；
- 有足够 reviews 后再比较 LangMem/自写记忆提取；
- 有足够 Prompt 样本后再做 canary；
- 多 Worker/远程审核成为真实需求时实施 PG。

---

## 6. 冲突与未验证事项

1. **PG-first 与 SQLite-first 是显式待决策项**，不是已达成一致的事实；本报告建议按实施目标选择。
2. Ozon 新罚则的具体分区数字来自二手解读，未作为确定性设计输入；账户健康监控必要性不受影响。
3. Nexscope 误差带来自 ROADMAP 自登记，尚缺真值标定。
4. 佣金库、NO_BRAND 跨类目通用性、1688 发货字段可用性仍需实测。
5. UI 评审基于源码和线框，未运行浏览器进行截图验收。
6. 本报告中的新流程、优先级和状态名均为 `[agent-suggested]`，实施前需用户确认。

## 7. 主要来源

- 本仓库：`pipeline.py`、`sync_to_db.py`、`review_server.py`、`listing_worker.py`、`pricing.py`、`trend_check.py`、`nexscope_client.py`、`keyword_library.py`、`select_products.py`、`sourcing_agent.py`、`listing_edit.py`、`content_agent.py`、`ROADMAP.md`。
- Ozon 官方 Seller API 英文镜像：商品上传、审核与可售状态说明  
  https://raw.githubusercontent.com/DragonSigh/ozon-seller-api-docs/master/uploading-and-updating-products-1.md
- PostgreSQL SKIP LOCKED / NOTIFY：  
  https://www.postgresql.org/docs/current/sql-select.html  
  https://www.postgresql.org/docs/current/sql-notify.html
- PydanticAI Harness / Durable Execution：  
  https://pydantic.dev/docs/ai/harness/permissions/  
  https://pydantic.dev/docs/ai/durable_execution/
- LangMem 概念指南：  
  https://langchain-ai.github.io/langmem/concepts/conceptual_guide/
- WCAG 2.2：非文本内容、键盘、状态消息等成功准则。
