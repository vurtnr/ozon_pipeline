# Research: ozon-pipeline 引入 ComfyUI 作为专业生图 Worker 的适配性评估

> 调研日期：2026-11。方法：先读本地源码与设计稿（image_worker.py / text_overlay.py / dimensions.py / review_server.py 全文，prompts/slot4_usage_v3.txt，docs/research/ozon-agent-harness-deep-research.md、ozon-postgres-langmem-harness.md），再以 ComfyUI 官方一手来源核验外部事实：Comfy-Org/ComfyUI 仓库 LICENSE/README/server.py 源码/openapi.yaml、docs.comfy.org 官方文档（routes/messages/api-examples/workflow-api-format/v2 API）、Comfy-Org/ComfyUI-Manager README+LICENSE+官方安全通告、模型官方 HuggingFace/GitHub 页面及其 LICENSE 文件。未读 `.env`/密钥，未改业务代码，未执行真实生图或外部写操作。二手教程一律未采信（详见 Sources 拒绝清单）。

---

## 0. 结论摘要

**值得接入，但只值得"薄接入"：把 ComfyUI 作为第二 image-gen worker 以独立 HTTP 服务形态接入 harness 的 task/tool_calls/artifacts 框架，从单个 slot（建议 slot4 使用演示）开始最小 PoC，qwen-image-3.0（百炼 API）全量保留为默认路径与 fallback，不替换 8-slot 编排、文字双轨、数字溯源闸和 review_server 审核流。** ComfyUI 真正补足的是 qwen-image API 拿不到的三件事：局部重绘（inpainting 保商品像素级不变）、ControlNet 式构图/姿态/深度控制、以及多阶段后处理（抠图→重绘→超分）在一张 GPU 上的免费组合——这正对 slot4 手部畸形和商品一致性两大痛点。代价是一整套新运维面：GPU/CUDA 环境、模型权重（数十 GB）、自定义节点供应链风险（已有官方通告的 RCE 前科）、无内建认证的 HTTP 服务、以及 GPL-3 生态与各模型差异巨大的许可矩阵（**FLUX.1 dev 系列明令禁止商用，RMBG 系列非商用——不能按"能下载=能商用"假设**）。推荐形态：本机/同宿主机 GPU + ComfyUI 官方 HTTP API（/prompt、/ws、/history、/view、/upload/image），adapter 层实现 submit/poll/result/cancel/retry 并把 workflow 版本、seed、模型 hash 写入 artifacts 元数据；触发条件与分阶段建议见 §8。

---

## 1. 现状盘点（源码级，本轮直接读取）

| 环节 | 现状 | 源码证据 |
|---|---|---|
| 生图模型 | 百炼 `qwen-image-3.0` I2I（HTTP `/images/generations`），928x1232（3:4），$0.033/张（$0.03 + 参考图 $0.003），`prompt_extend=False`，negative_prompt 双档 | image_worker.py `call_api`、`PRICE_PER_IMAGE` |
| 8-slot 编排 | slot1 封面/slot3 卖点/slot4 使用/slot5 场景/slot6 细节/slot8 场景条走 AI；slot2 白底**优先供应商原图本地贴白底（零 AI 成本零形变）**，原图带文字才回退 AI；slot7 尺寸图条件生成 | `SLOT_TEMPLATES`、`_supplier_white_bg`、slot7 分支 |
| 文字双轨制 | AI 只画场景+图标；大标题/徽章/说明带由 text_overlay.py 本地 PIL 贴字（Manrope Bold，西里尔零错误，字号自适应+按亮度选字色） | text_overlay.py 全文 |
| 确定性文字闸 | VL 检测成品是否带字（含包装/道具载体，产品丝印豁免）→ 加强措辞重试一次 → 仍带字判失败不交付；VL 异常按"有文字"保守处理 | `_vl_has_text`、`STRICT_NO_TEXT`、`_GEN_TEXT_Q` |
| 数字溯源闸 | slot7 每个数字必须在 1688 标题/类目/OCR（dimensions.py，qwen3-vl-plus 提取供应商营销图标注，best-effort）中找到出处；review_server 提供 `/slot7` 人工输入通道（source=manual 最高信任级） | `_measurements_traceable`、dimensions.py、review_server.py `gen_slot7` |
| 风格参考机制 | **已停用**（模版图自带文字会随 I2I 迁移，slot4 连续 4 次失败根因；代码保留待干净模版） | image_worker.py `STYLE_REFS: dict = {}` 注释 |
| 局部重生 | `--only slotX` 单 slot 重出，不打乱已审图；审核台"打回重生"=回 imaging 状态，重跑 image_worker 后 sync 送回待审 | `generate_slots(only=...)`、review_server.py `/review?regen` |
| 审核流 | pipeline.db 唯一事实源；imaging→review_pending 为唯一人工闸；approve/regenerate/kill 三动作落 reviews（6 死因枚举）；`/media/<f>` 出图（防目录穿越）；过审率进顶栏保险丝指标 | review_server.py `review()`、`/action`、`/media` |
| Harness 框架 | tasks/tool_calls/artifacts(sha256+meta_json)/human_tasks/budgets/prompt_versions 七表+agent_runner 均为**设计稿未实现**（两份前研报告源码级确认）；permission_level 五级中生图属 `image-gen`；付费调用 max_attempts=2；prompt_versions 走 draft→golden_pass→canary→active→retired 审批通道 | harness 深研报告 §2.4/2.7/2.8、PG 报告 §0 |

**含义**：现有链路的"专业能力缺口"不在编排/闸门/审核（这些已扎实），而在**生成引擎本身**——qwen-image API 是黑盒单次 I2I，无法表达"只改手部、其余像素不动""按深度图锁构图""抠出商品再合成新背景"这类约束。ComfyUI 恰好只补这一层。

---

## 2. ComfyUI 能补什么（逐项判定：适合商品图 / 不必要或高风险）

ComfyUI 官方 README 自述：内置 inpainting/outpainting、masks/compositing、upscaling、segmentation、depth estimation 工具；原生支持 Qwen Image、Qwen Image Edit、Flux.1/Flux.2、SDXL 等模型；官方示例库 comfy.org/workflows 提供模板；**核心执行器带缓存语义——"if you submit the same graph twice only the first will be executed"，只重执行图中有变化的部分**（README「Notes」节，原文）。

| # | 能力 | 判定 | 说明 |
|---|---|---|---|
| 1 | **节点化 workflow（JSON 可版本化、图级缓存、部分重执行）** | ✅ 适合 | workflow API-format JSON 可入 git/prompt_versions 通道审批；改 prompt 只重执行下游节点（官方语义）。与现有 prompt_versions draft→canary→active 通道天然契合 |
| 2 | **局部重绘/inpainting（mask 保商品、只改手部/场景）** | ✅ 适合（PoC 首选） | slot4 模板自述失败模式"手部畸形（AI 通病——翻车则改成无手悬浮状态重试）"；inpainting 可把商品区域 mask 保护、只重绘手/环境，直接命中痛点，且比全图 I2I 更满足模板"product must stay pixel-identical"的硬要求 |
| 3 | **ControlNet/姿态/深度控制（锁构图、锁比例）** | ⚠️ 有条件适合 | 对 slot7 尺寸图（标注线/数字排版）和 slot3 卖点网格构图有潜在价值；**但许可证是硬约束**：SD1.5/SDXL 系 ControlNet 沿 CreativeML Open RAIL(-++-M) 可商用，而 InstantX 官方 Qwen-Image-ControlNet-Union 模型卡仅声明 "All copyright reserved"、无开源许可授予——商用合规性未确立（见 §5 许可矩阵）。先按"可实验、暂不入生产"处理 |
| 4 | **背景替换/产品一致性（抠图→合成）** | ✅ 适合（第二优先） | BiRefNet（MIT，代码+权重）或 SAM 2（Apache-2.0，官方 README 明示 checkpoints/demo/training 均 Apache-2.0）抠商品 → 合成新背景 + 局部重绘融合，替代 qwen 全图 I2I 的"重画商品"风险。**注意 RMBG-1.4/2.0 是 Bria 非商用许可（官方 README：商用需与 BRIA 另签协议）——不要选** |
| 5 | **超分 upscale** | ⚠️ 低优先 | Real-ESRGAN（BSD-3-Clause）可用且免费；但 Ozon 槽位规格已定 928x1232，qwen 输出直接达标，超分收益主要在放大细节 slot（slot6）。**不必要先做** |
| 6 | **多模型并行/择优** | ✅ 适合（结构性收益） | 同一 harness task 可提交不同 workflow（Qwen-Image-Edit Apache-2.0 本地版 vs SDXL RAIL++-M vs 闭源 API），出图带 model+seed 元数据进 artifacts，reviews golden set 可做模型 A/B——这是 API 黑盒做不到的 |
| 7 | **多阶段后处理管线（生成→抠字→贴字→质检）** | ✅ 适合，但**贴字必须留在 PIL** | 现有 text_overlay 的西里尔零错误双轨制是本管线最宝贵的资产，绝不能交给扩散模型或第三方节点；ComfyUI 侧只做生成与图像域处理，PIL 贴字作为 adapter 出图后的既有后处理不变 |
| 8 | **Comfy 官方闭源 API 节点（partner nodes：Nano Banana 等）** | ❌ 不必要/高风险 | 官方 README 提供 `--offline`/`--disable-partner-nodes` 关闭；引入等于把成本黑盒和外部依赖重新拉回来，与引入 ComfyUI 的初衷相悖 |
| 9 | **视频/3D/音频等非生图能力** | ❌ 不必要 | 与 8-slot 商品图无关，不为此扩环境 |

---

## 3. 推荐集成形态

**3.1 服务形态：独立 worker 进程 + HTTP API，不做 Python 库内嵌。**
- 官方形态就是自托管 HTTP 服务：`python main.py` 起 aiohttp 服务（默认 8188 端口），docs.comfy.org 明确"The server defines a series of get and post methods … in server.py"，README 明确"It integrates seamlessly into production pipelines with our API endpoints"。
- 库内嵌（import comfy）无官方支持路径，且会把 GPL-3 代码与本仓库 Python 耦合，升级即碎。HTTP 边界天然隔离：qwen worker 与 comfy worker 同为 harness 的 tool adapter，接口对称。
- 进程监督由现有部署方式（launchd/systemd/手动）承担；ComfyUI 崩溃不拖垮 agent_runner。

**3.2 部署位置：本地 GPU 优先；远程 GPU 为扩容选项；Comfy Cloud 仅作对照实验。**
- **本地 GPU**：零边际 API 成本、数据不出本机、延迟可控；PoC 首选。硬件门槛见 §5（未验证假设：本机是否有可用 CUDA GPU 待确认）。
- **远程 GPU（租用）**：按小时计费的裸 GPU + 自装 ComfyUI，适合 PoC 通过后提量；但引入模型下载带宽、安全暴露面（见 §5.10）和按小时烧钱的新预算类别。官方 README 提到官方付费云 **Comfy Cloud**（www.comfy.org/cloud）——可作为"无 GPU 时的对照实验"选项，但它把 workflow 放到官方云执行，成本模型回归 API 计费，不解决主权诉求。
- **注意 Docker 无官方支持产品镜像**：主仓库历史上明确拒绝 Docker 支持（PR #9305 被维护者关闭，理由"maintainers have decided to reject Docker support altogether"）；2026-10 有 Docker 配置 PR（#17259）与官方 CI 容器仓 Comfy-Org/comfyui-ci-container，但均非面向生产的官方支持镜像。容器化需自建（venv + 权重卷挂载），别假设有官方镜像可用。

**3.3 与现有系统的关系（保持四不变）**：
1. **与 agent_runner**：ComfyUI adapter 是新的 task kind（如 `generate_images_comfy`），输入输出落 tool_calls（模型/workflow 版本/seed/成本=0 或 GPU 时长），产物落 artifacts（sha256+meta_json）；policy gate 照常按 `image-gen` 权限级放行——**权限/预算/HITL 语义不变**。
2. **与 review_server**：零改动。ComfyUI 产物与 qwen 产物同样写 `data/gen/<assets>/slotX.png`，审核台 `/media`、三动作、打回重生、slot7 人工通道全部照旧工作。
3. **与现有 qwen worker**：**保留 qwen 为默认与 fallback**。qwen 是已验证、$0.033/张、零运维的基线；ComfyUI 是"失败模式补丁"（slot4 手部、构图控制）与"成本/主权"期权。路由规则：spec/模板不带 `worker: comfy` 标记时一律走 qwen；comfy 失败/超时 → 自动回落 qwen 并落 event。
4. **文字双轨/数字溯源闸不变**：ComfyUI 出图同样过 VL 文字闸（闸是 worker 外层逻辑，不属于 qwen），slot7 数字闸与 `--only` 局部重生语义不变。

---

## 4. 官方 API 集成细节（全部经一手来源核验）

**4.1 workflow JSON / API format**（docs.comfy.org《Workflow API Format》）：前端 `File → Export Workflow (API)` 导出 API 格式——数字节点 ID 为 key、`class_type`+`inputs`+`_meta.title`、无布局数据；与 UI 保存格式（nodes/links）不同，**提交必须用 API 格式**。官方 api-examples 页给出标准样例（KSampler/CheckpointLoaderSimple/CLIPTextEncode/VAEDecode/SaveImage）。

**4.2 提交与队列**（docs.comfy.org《Server Routes》）：`POST /prompt`，body `{"prompt": <api格式>, "client_id": <uuid>}`（官方 websockets_api_example.py 显示客户端也可自带 `prompt_id`）；同步校验后返回 `prompt_id` + `number`（队列位次），校验失败返回 `error` + `node_errors`。`GET /prompt` 查队列状态。

**4.3 输入图上传**（server.py 源码，本轮直读）：`POST /upload/image` multipart 字段 `image`（文件）、`type`（input/temp/output，默认 input）、`subfolder`、`overwrite`（"true"/"1"）。**不 overwrite 时：内容 hash 与同名文件一致则去重不重写，否则自动改名 `name (1).ext`** —— adapter 应总是带固定名+overwrite=true 以保证幂等重跑不产生 (1)(2) 垃圾文件。返回 `{"name","subfolder","type"}`，LoadImage 节点以该三元组引用。服务端有 commonpath 目录穿越防护。`POST /upload/mask` 可基于 `original_ref` 生成 alpha mask（inpainting 输入）。openapi.yaml 另载明上传限制：≤50MB、单边 ≤16384px、≤64MP，超限 HTTP 400。

**4.4 完成判定与取图（官方推荐模式）**（docs.comfy.org《API Examples》，源码 websockets_api_example.py）：
- `GET /ws?clientId=<uuid>` 建 WebSocket；监听 `executing` 消息且 `data.node is null && data.prompt_id == 自己的` → 执行完成；然后 `GET /history/{prompt_id}` 取 `outputs[node_id].images[]`（filename/subfolder/type），再 `GET /view?filename=…&subfolder=…&type=…` 下载字节。
- **事件全集**（docs.comfy.org《Server Messages》）：`execution_start`、`progress`（value/max）、`executed`、`executing`（node 或 null=完成）、`execution_cached`、`execution_success`、`execution_error`、`execution_interrupted`、`status`（exec_info.queue_remaining）。二进制帧为预览图（前 8 字节头），不消费可忽略。
- HTTP-only 轮询模式（Method 1）也可行：提交后轮询 `/history/{prompt_id}`，适合不想维护 ws 长连的 adapter——**推荐实现为"轮询为主 + ws 为延迟优化"，与 harness 报告对 NOTIFY 的处理原则一致**。

**4.5 错误结构**（openapi.yaml `ExecutionError` schema）：`node_id/node_type/exception_type/exception_message/traceback/current_inputs/current_outputs` —— 可直接映射到 tool_call.error 与死信分类。

**4.6 取消/队列管理**：
- `POST /interrupt` 停止当前执行（routes 官方表）。
- `POST /queue` 传 `{"delete":[prompt_id…]}`——**openapi 官方注释：虽名为 delete 实为转 cancelled 状态，且 pending 与 running 都会取消（旧文档"只删 pending"不成立）**；该端点已标 deprecated，官方建议迁移到 jobs 命名空间：`POST /api/jobs/cancel` 与 `POST /api/jobs/{job_id}/cancel`（后者幂等：已终态再取消返回 200 `cancelled:false`）。
- `POST /free` 可主动卸载模型释放显存（routes 表）。

**4.7 幂等与至少一次执行风险（决策关键）**：
- **经典 /prompt 无幂等键**：adapter 超时重试 = 重复入队重复执行。服务端唯一的天然去重是**图缓存**（README："if you submit the same graph twice only the first will be executed"——同图同 seed 第二次直接命中缓存），但这不构成业务幂等保证。**幂等必须由 harness 层承担**：task idempotency_key（kind:product_id:hash(payload)）+ adapter 侧"每次尝试先 GET /history/{client 生成的 prompt_id}"（客户端可自带 prompt_id，官方示例支持）→ 重试先查历史再提交，避免重复生图；即使重复执行，产物落盘按 sha256 幂等写入 artifacts，与 pgtask at-least-once 语义（PG 报告 §1）完全一致：**外部副作用必须设计成重跑无害**。
- **新 v2 jobs API 已在本地服务器落地**（仓库内 openapi.yaml，双路径路由 bare + `/api/…` 别名）：官方 v2 文档明确 **Idempotency-Key 语义为 single-use reject-on-duplicate（重复 key 直接拒绝，不重放结果）**、job 可按 ID 轮询直到 expires_at、输出 URL 短时效；`/history` 已标 deprecated（由 `/api/jobs` 取代，"planned for removal no earlier than a future major release"）。**结论：adapter 面向经典 /prompt //history //view //upload 接口实现（全版本稳定），同时记录 v2 jobs 的 Idempotency-Key 能力，等 ComfyUI 发布稳定大版本后再评估切换**——现在追新 API 会踩 0.x 期变更。

**4.8 超时与超时后**：ComfyUI 无服务端任务超时（排队+执行直到完成或 interrupt）；超时是 adapter 概念。执行慢节点有 `progress` 事件可作心跳；无 progress 且超 adapter timeout → 标 failed、task 走 backoff、prompt_id 查历史防重复（4.7）。付费失败不自动重放超过 max_attempts=2（harness 既定规则；本地 GPU 边际成本≈0，但 GPU 时间是真实资源，同规则适用）。

---

## 5. 技术难点（逐项）

**5.1 环境/GPU/CUDA**。README 官方口径：Python 3.13 支持最好；torch ≥2.7，NVIDIA 20 系以上要求 cu130+；最低"4GB vram + 8GB ram 可跑大模型（异步权重流式）"但那是极端下限。Qwen-Image-Edit 官方 diffusers 文件清单显示 diffusion 模型 ~20.4GB + 文本编码器 ~9.4GB（社区转换后 fp8/量化可减半，但量化质量未验证）——**24GB 显存是舒适线，12GB 需量化+offload**。本机 GPU 型号未核实（未验证假设，PoC 前必须确认 `nvidia-smi`/`torch.cuda`）。

**5.2 模型权重下载与存储**。数十 GB 级；HuggingFace 在国内网络不可达时需镜像（Manager 官方支持 `HF_ENDPOINT`/`GITHUB_ENDPOINT` 反代环境变量——README 原文）。磁盘规划：weights 与 ComfyUI 代码分离目录，用官方 `extra_model_paths.yaml.example` 机制指向独立卷，便于升级/迁移/备份策略独立。

**5.3 自定义节点依赖与供应链安全（最高风险项）**。官方一手事实链：
- ComfyUI 官方博客《2025 Jan Security Update》：已发现恶意节点（ComfyUI_LLMVISION、ultralytics 事件），Registry 上线时配套"发布限制 + 发现漏洞即可封禁"机制；docs.comfy.org/registry/standards 列明注册表安全标准。
- ComfyUI-Manager README 官方安全策略：`security_level = strong|normal|normal-|weak`（normal- 在 `--listen` 非 127.* 时自动禁高风险功能）；**`allow_git_url_install` / `allow_pip_install` 两个专用开关默认 false、仅 loopback 监听生效**——任意 git URL 装"节点"=任意代码执行面，官方已把它从 security_level 解耦成显式 opt-in。
- 官方安全通告：CVE-2025-67303（Manager<3.38 + ComfyUI<0.3.76 配置篡改→降级 security_level→RCE 链，腾讯玄武实验室报告；补丁=ComfyUI v0.3.76+ / Manager 3.38+ 配置迁入受保护 `/__manager/` 路径）；GHSA-562r-8445-54r2（Manager config CRLF 注入，<4.0.5/3.39.2 受影响）；官方 advisories 页另列 /view SVG 存储型 XSS、/userdata XSS、LoadImage 路径穿越等多条。
- **工程对策**：固定 ComfyUI 版本 ≥0.3.76 且 Manager ≥4.0.5；自定义节点白名单制（不装 PoC 必需之外的任何节点）；节点 pack 固定 commit hash（snapshot 机制可导出锁定清单）；`network_mode=offline` 或出网白名单；启动参数禁用 git-url/pip 安装面。

**5.4 workflow 可移植性与节点/模型版本**。节点接口无稳定 API 承诺：README 官方声明"Commits outside of the stable release tags may be very unstable and break many custom nodes"；官方发布节奏=核心约双周一版+每周发布周期，frontend 独立仓库双周合入。对策：workflow JSON 与节点清单一起进版本库（prompt_versions 通道），artifacts 元数据记录 workflow_version + ComfyUI 版本 + 节点 pack 版本；升级先跑 golden 回归（用固定 seed 出固定 slot 比对）。

**5.5 prompt/seed 可复现**。官方支持：KSampler 等节点 seed 显式入图；README："Dragging a generated png … will give you the full workflow including seeds"（生成 PNG 内嵌完整 workflow+seed 元数据）。adapter 提交时显式固定 seed（由 harness task 生成）→ 同 workflow 同 seed 同权重理论可复现；但跨 ComfyUI 版本/权重文件变化不保证逐像素一致——复现边界写进 artifacts 元数据（模型文件 hash）。

**5.6 显存与并发**。单 ComfyUI 实例是**串行队列**（一个工作流执行完才下一个；多 prompt 排队）；并发=多实例（多端口、共享或分卡权重）。README 官方缓存（同图重复提交跳过执行）反而降低重复开销。日百卡 ×8 slot 的量级下，单卡串行 + qwen 兜底足够，无需为并发过度设计。

**5.7 冷启动与长任务恢复**。首载模型从磁盘进显存是分钟级冷启动；`/free` 可卸载。**ComfyUI 无内置持久化恢复**：进程重启丢队列（running 任务结果只在 history/输出目录，排队中任务丢失）。对策：harness task lease 30s 心跳（既有设计）覆盖——崩溃后 lease 过期回收重跑，adapter 靠客户端 prompt_id 查 history 去重；正在跑的任务被 kill 属"结果未知"，按 4.7 幂等原则重跑无害。

**5.8 文件路径与容器卷**。官方目录约定：models/{checkpoints,vae,loras,controlnet,…}、input/、output/、user/；`--output-directory`/`--input-directory`/`--user-directory` CLI 可重定向；`extra_model_paths.yaml` 挂外部卷。若容器化：input/output/weights 三卷必须持久挂载，容器重启不丢产物（artifacts sha256 入库后文件本体仍是事实源）。

**5.9 远程访问安全（部署红线）**。官方通告（GHSA-95pq-hr8p-f5g7）风险矩阵：`--listen 0.0.0.0` 外部暴露=**HIGH**，反向代理无鉴权=MEDIUM，默认仅 localhost=**NOT AFFECTED**；官方 workaround 表=去 0.0.0.0 / 防火墙 / **反代加认证** / IP 白名单。server.py 源码默认绑定回环并带 origin 校验中间件（Host/Origin 不符 403）；`--enable-cors-header` 会放宽。第三方分析（HelixGuard）明确指出 ComfyUI 本身无内建认证——**结论：ComfyUI 只监听 127.0.0.1，与 agent_runner 同宿主机通信；跨机必须走带认证的反代/隧道，并在 policy gate 里把它当 external-write 级敏感面对待**。

**5.10 许可证与商品图合规（不能想当然）**。ComfyUI 本体 GPL-3.0-only（LICENSE 文件一手确认）——通过 HTTP API 独立进程调用不构成本仓库代码的衍生分发义务，但要避免把 ComfyUI 代码拷进本仓库；ComfyUI-Manager 同为 GPL-3（LICENSE.txt 一手确认）。**模型许可矩阵（全部官方一手来源）**：

| 模型/组件 | 许可 | 商用商品图？ |
|---|---|---|
| Qwen-Image / Qwen-Image-Edit（阿里官方 HF） | Apache-2.0 | ✅ 可 |
| FLUX.1 [dev] 全系（含 Fill/Depth/Canny/Kontext dev） | FLUX Non-Commercial v1.1.1（官方 LICENSE.md："non-commercial and non-production use"） | ❌ **不可**（需 BFL 付费授权） |
| FLUX.1 [schnell] | Apache-2.0（官方 flux 仓库许可表） | ✅ 可 |
| SDXL base/refiner 1.0 | CreativeML Open RAIL++-M | ✅ 可（附 Attachment A 使用限制） |
| SAM 2（Meta） | Apache-2.0（官方 README：checkpoints/demo/training 均是） | ✅ 可 |
| BiRefNet | MIT | ✅ 可 |
| Depth Anything V2 | **分裂许可**：仅 Small=Apache-2.0；Base/Large/Giant=CC-BY-NC-4.0（官方 README 原文） | ⚠️ 只可用 Small |
| RMBG-1.4（Bria） | bria-rmbg-1.4 专有许可，官方 README 明示非商用、商用需与 BRIA 签约 | ❌ 不可（选 BiRefNet 替代） |
| IP-Adapter | 仓库 Apache-2.0；h94 转换模型 apache-2.0；**FaceID 变体官方 issue 确认非商用** | ⚠️ 用主模型可，FaceID 不可 |
| Real-ESRGAN | BSD-3-Clause | ✅ 可 |
| InstantX Qwen-Image-ControlNet-Union | 模型卡仅 "All copyright reserved"，**无许可授予** | ❌ 合规未确立，实验可以、商用不行 |
| ComfyUI / ComfyUI-Manager | GPL-3.0-only | ✅ 独立服务调用可（注意分发边界） |

另注：商品图训练/生成数据合规（供应商图二次加工权、Ozon 平台对 AI 图的规则）不在 ComfyUI 引入范围内新增——qwen API 路径已有同样的前置问题，状态不因引擎更换而恶化。

---

## 6. 架构图与最小 adapter 接口

```text
agent_runner（既有设计，不改语义）
  task: generate_images_comfy   [permission: image-gen]
  policy gate：权限/预算(budgets: tool:comfy-gpu 时长记账)/human_tasks 不变
        │ 领取（idempotency_key=kind:product_id:hash(payload)，lease 30s）
        v
  comfy_image_adapter（新文件，唯一新增代码）
    submit(workflow_id, inputs{source_image, spec, seed}, attempt) -> prompt_id
      ├ 上传输入图: POST /upload/image (固定名+overwrite=true)      [server.py 一手]
      ├ 渲染 workflow JSON vN：占位符替换 seed/prompt/尺寸/图引用
      └ POST /prompt {prompt, client_id, prompt_id(客户端生成=attempt 派生)}
    poll(prompt_id) -> {queued|running|done|error|cancelled}
      ├ 主路径: GET /history/{prompt_id} 轮询（退避）
      └ 优化:  /ws?clientId 事件驱动（executing:null=完成；execution_error/interrupted）
    result(prompt_id) -> {slotX.png bytes, meta}
      └ GET /view?filename&subfolder&type ← history.outputs[].images
      ├ 后处理不变: PIL 贴字(text_overlay) → VL 文字闸 → slot7 数字闸
      └ artifacts 落库: path+sha256+meta_json{workflow_version, comfy_version,
        node_packs, seed, model_files{hash}, engine="comfy", gpu_seconds}
    cancel(prompt_id) -> POST /interrupt (running) | POST /queue{delete:[id]} (pending→cancelled)
    retry(attempt) -> 先 GET /history/{旧 prompt_id} 去重，再 submit（幂等）
        │ 失败/超时 → 降级
        v
  qwen_image_worker（既有，默认与 fallback，一行不改）
        │
        v
  review_server（既有，零改动：/media 出图、三动作、打回重生、slot7 人工通道）
```

接口契约要点（全部上表已有一手依据）：prompt_id 由 adapter 生成并持久化到 tool_call 行 → 任何重试先查 history 防重复执行；产物固定写 `data/gen/<assets>/slotX.png` 使 review_server 无感知；workflow 模板文件与 prompts/*.txt 同目录同审查通道（draft→golden_pass→canary→active）。

---

## 7. 最小 PoC：选一个 slot 验证（不一次替换全部）

**选择：slot4「使用演示」**（备选 slot5 场景延伸）。理由（源码依据）：slot4 模板自述失败模式"手部畸形（AI 通病——翻车则改成无手悬浮重试）"，且要求"product must stay pixel-identical"——这正是 inpainting（mask 保护商品区域、只重绘手/环境）相对全图 I2I 的结构性优势；slot2 已被供应商原图方案零成本解决，slot7 受数字闸限制与生成质量无关，slot1/3/6/8 的文字版式依赖 PIL 双轨与生成质量关联弱。**未验证假设：slot4 当前实际打回率需先从 reviews 表/slots-report.json 统计确认（PoC 前置动作）**。

PoC 验收清单（全部可机检/可从现有表推导）：
1. **出图质量**：固定 20 个 slot4 任务，ComfyUI 组 VL 文字闸通过率与人工审核 approve 率 ≥ qwen 基线（reviews 表 golden set 对比；不设绝对阈值，先测基线）。
2. **商品一致性**：抽 10 张人工评审"商品形状/色/结构与供应商原图一致"通过率 ≥ 现有 I2I 基线；目标是无"商品被重画"类死因。
3. **失败恢复**：kill -9 ComfyUI 进程于执行中 → lease 过期回收重跑 → 产物正确、artifacts 无重复行、无重复文件（`slotX (1).png` 不出现）。
4. **重复提交**：同 idempotency_key 提交两次 → 第二次命中 harness 去重不产生第二次 GPU 执行；同 workflow+seed 重复提交 → 触发官方图缓存跳过（history 可查 execution_cached）。
5. **成本/耗时**：单张 GPU 秒数 vs qwen $0.033/张·约 30-60s；本地 GPU 边际成本≈0 但计入 GPU 时长预算（budgets: tool:comfy-gpu）。
6. **审核通过率与回滚**：全部产物照常进 review_pending 人工闸；spec 加 `worker: comfy` 开关——开关切回缺省即回滚 qwen，ComfyUI 停服不影响任何在跑 qwen 任务（回滚=改配置，不删代码）。

---

## 8. 现在是否值得接入：结论、触发条件与分阶段

**结论：现在值得做"准备工作 + PoC"，不值得现在替换任何生产 slot。** 判断依据：①缺口真实（slot4 手部、一致性、构图控制是 qwen API 结构性短板，风格参考机制已因文字迁移失败停用，证明现有引擎在"受控生成"上到顶了）；②成本低（本地 PoC 零 API 成本、review_server/harness/闸门零改动、adapter 是唯一新代码）；③风险可控（qwen 全量保留，回滚=配置开关）；④但生产化前置条件未满足——GPU 硬件未确认、模型许可矩阵需逐一签核（§5.10）、供应链红线（§5.3）需要部署规程，这些是"接入"之外的独立工作。

**分阶段建议**：
- **P0（不装 ComfyUI 也能做）**：统计 reviews/slots-report 中 slot4/slot5 打回率与死因分布 → 用数据确认 PoC 目标 slot；确认本机 GPU 型号/显存/驱动；签核 §5.10 许可矩阵（初选组合：Qwen-Image-Edit + BiRefNet + Real-ESRGAN，全部可商用）。
- **P1 PoC（≤1 周）**：单机装 ComfyUI（venv，Manager≥4.0.5，节点白名单 0 个起步——inpainting/抠图官方内置节点即可起步），写 comfy_image_adapter（§6 五接口，约 200 行），跑 §7 验收 6 条。
- **P2 定位**：验收达标 → slot4（或数据支持的其他 slot）灰度：spec 显式 `worker: comfy`，人工触发局部重生优先用 comfy，默认批量仍走 qwen；不达标 → 留代码留结论，等待更强模型（Qwen-Image 系迭代）再评估。
- **P3 生产化触发条件（全部满足才做）**：单卡吞吐实测 ≥ 日百卡×8 slot 峰值叠加缓冲；权重/节点版本锁定与 golden 回归流程建立；远程部署需求出现时再评估"远程 GPU + 带认证反代"形态。
- **明确不做**：接入 Comfy 官方闭源 API 节点（partner nodes）；用扩散模型重画大标题（破坏文字双轨制）；一次替换多 slot；主仓库 Docker 化依赖官方镜像（不存在）。

---

## Contradictions（矛盾与处理）

1. **"Qwen-Image 可商用"与"Qwen-Image-2.1 非商用"说法并存**：阿里官方 HF 对 Qwen-Image/Qwen-Image-Edit 标 Apache-2.0（一手 LICENSE 确认）；第三方博客称 2026-09 的 Qwen-Image-2.1 为非商用许可。**处理**：本报告只对官方一手确认的 Qwen-Image / Qwen-Image-Edit 下商用可用结论；引入任何新版本前逐版本核对官方 LICENSE 文件。
2. **"管理器 security_level=strong 就安全"与 CVE-2026-22777**：Manager 曾有 config CRLF 注入链（GHSA-562r-8445-54r2）可绕过 security_level 语义，官方已修补（4.0.5/3.39.2）。**处理**：报告的部署红线以"≥0.3.76 + Manager≥4.0.5 + 默认 loopback + 安装开关默认 false"为准，不依赖单一配置项。
3. **/queue 的 delete 语义**：旧文档称只删 pending，当前 openapi 官方注释明确"pending 与 running 都会取消、实为转 cancelled 状态"。**处理**：以 openapi 注释为准（4.6），adapter 取消逻辑按此实现。
4. **Docker 支持**：官方长期拒绝（PR #9305 关闭声明）与 2026-10 出现 Docker 配置 PR（#17259）并存。**处理**：按"无官方支持镜像"规划，不自建生产镜像主张官方背书。

## Missing evidence（未验证假设）

- 本机 GPU 硬件（型号/显存/CUDA 可用性）：未核实，P0 前置确认。
- slot4/slot5 当前打回率：未连库统计（未执行任何 DB 写/读操作），PoC 目标的定量前提待补。
- Qwen-Image-Edit 在 ComfyUI 官方工作流下于本机显存的实测速度/质量：零实测（20.4GB diffusion 文件清单来自官方 diffusers 仓库文件，非实测运行）。
- 经典 /prompt 在"未来大版本"中是否被 jobs API 取代：openapi 已将 /history 标 deprecated 但无移除时间表；adapter 以经典接口实现属保守押注，需在 P3 复核。
- 超分（Real-ESRGAN）对 slot6 的实际收益：未实验，判为低优先。

## Sources

**保留（本轮一手，直接抓取/直读）**
- ComfyUI LICENSE（GPL-3.0 原文）— https://raw.githubusercontent.com/comfyanonymous/ComfyUI/HEAD/LICENSE
- ComfyUI README（内置 inpainting/超分/分割/深度、原生模型清单、执行缓存语义、seed 元数据、发布周期、--listen/TLS/--offline、extra_model_paths、Manager --enable-manager、comfy-cli）— https://github.com/Comfy-Org/ComfyUI
- ComfyUI server.py 源码（/upload/image 幂等改名+hash 去重+穿越防护、/upload/mask、/view 防护、origin 中间件、jobs 模块、max_upload_size）— https://github.com/Comfy-Org/ComfyUI/blob/master/server.py
- ComfyUI openapi.yaml（GPLv3、双路径路由、/api/jobs 幂等取消、/history deprecated、上传限制、ExecutionError schema）— https://github.com/Comfy-Org/ComfyUI/blob/master/openapi.yaml
- docs.comfy.org《Server Routes》— https://docs.comfy.org/development/comfyui-server/comms_routes
- docs.comfy.org《Server Messages (WebSocket API)》— https://docs.comfy.org/development/comfyui-server/comms_messages
- docs.comfy.org《API Examples》（官方 websockets_api_example 模式）— https://docs.comfy.org/development/comfyui-server/api-examples
- docs.comfy.org《Workflow API Format》— https://docs.comfy.org/development/api-development/workflow-api-format
- docs.comfy.org Comfy API v2（Idempotency-Key single-use reject-on-duplicate、poll-first、resumable）— https://docs.comfy.org/api-reference/v2/overview 、https://docs.comfy.org/api-reference/v2/jobs/submit-a-workflow-for-execution 、https://docs.comfy.org/development/api-development/sdks-design
- ComfyUI-Manager README（security_level、allow_git_url_install/allow_pip_install 默认 false 且仅 loopback、network_mode、V3.38 迁移、snapshot、cm-cli）与 LICENSE.txt（GPL-3 原文，本轮克隆直读）— https://github.com/Comfy-Org/ComfyUI-Manager
- GHSA-95pq-hr8p-f5g7（CVE-2025-67303 官方通告全文：0.0.0.0=HIGH、默认本地不受影响、补丁版本、workaround 表）— https://github.com/Comfy-Org/ComfyUI-Manager/security/advisories/GHSA-95pq-hr8p-f5g7
- GHSA-562r-8445-54r2（Manager CRLF 注入官方通告）— https://github.com/Comfy-Org/ComfyUI-Manager/security/advisories/GHSA-562r-8445-54r2
- Comfy-Org/ComfyUI 官方 Security Advisories 页 — https://github.com/Comfy-Org/ComfyUI/security/advisories
- ComfyUI 官方博客《2025 Jan Security Update》（恶意节点事件与 Registry 封禁机制）— https://blog.comfy.org/p/comfyui-2025-jan-security-update
- Comfy Registry 官方文档与标准 — https://docs.comfy.org/registry/overview 、https://docs.comfy.org/registry/standards
- Qwen/Qwen-Image LICENSE（Apache-2.0）— https://huggingface.co/Qwen/Qwen-Image/blob/main/LICENSE ；Qwen/Qwen-Image-Edit（Apache 2.0 声明）— https://huggingface.co/Qwen/Qwen-Image-Edit
- black-forest-labs/FLUX.1-dev LICENSE.md（Non-Commercial v1.1.1 原文）— https://huggingface.co/black-forest-labs/FLUX.1-dev/blob/main/LICENSE.md ；BFL 官方许可页 — https://bfl.ai/legal/non-commercial-license-terms ；flux 仓库模型许可表（schnell=Apache-2.0）— https://github.com/black-forest-labs/flux
- SDXL base 1.0 LICENSE.md（Open RAIL++-M）— https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/blob/main/LICENSE.md
- facebookresearch/sam2 LICENSE + README（Apache-2.0 全覆盖声明）— https://github.com/facebookresearch/sam2
- DepthAnything/Depth-Anything-V2 README（Small=Apache-2.0，Base/Large/Giant=CC-BY-NC-4.0 原文）— https://github.com/DepthAnything/Depth-Anything-V2
- ZhengPeng7/BiRefNet LICENSE（MIT）— https://github.com/ZhengPeng7/BiRefNet
- briaai/RMBG-1.4 README/LICENSE（bria-rmbg-1.4 非商用、商用需签约原文）— https://huggingface.co/briaai/RMBG-1.4
- tencent-ailab/IP-Adapter（Apache-2.0）+ h94/IP-Adapter（模型 apache-2.0）+ issue #188（FaceID 非商用）— https://github.com/tencent-ailab/IP-Adapter
- xinntao/Real-ESRGAN（BSD-3-Clause）— https://github.com/xinntao/Real-ESRGAN
- InstantX/Qwen-Image-ControlNet-Union 模型卡（能力清单 + "All copyright reserved" 无许可授予原文）— https://huggingface.co/InstantX/Qwen-Image-ControlNet-Union
- Docker 相关：PR #9305（官方拒绝声明）— https://github.com/Comfy-Org/ComfyUI/pull/9305 ；PR #17259（2026-10 Docker 配置）— https://github.com/Comfy-Org/ComfyUI/pull/17259 ；Comfy-Org/comfyui-ci-container — https://github.com/Comfy-Org/comfyui-ci-container
- 本仓库源码（本轮直读）：image_worker.py（全文）、text_overlay.py、dimensions.py、review_server.py（全文）、prompts/slot4_usage_v3.txt、docs/research/ozon-agent-harness-deep-research.md、docs/research/ozon-postgres-langmem-harness.md

**拒绝/降权**
- 各类 ComfyUI 接入教程/博客（kingsclaw、iimagined、localaimaster、deepwiki 等）——内容与官方文档重叠，按任务要求不作为事实来源；其中 deepwiki/kingsclaw 关于 Idempotency-Key 的说法已用 docs.comfy.org 官方页替代核验
- HelixGuard CVE-2025-67303 分析（"ComfyUI 无内建认证"表述来自第三方）——仅作风险佐证，部署红线以官方通告 workaround 表为准
- 维基百科 ComfyUI 词条——二手，未采用
- 第三方模型许可对比博客（kishan.design 等）——与官方 LICENSE 对照后弃用，未采信其结论

## Next steps

1. P0 数据动作：只读统计 reviews/slots-report 中 slot4 打回率与死因分布，确认 PoC slot 选择。
2. P0 硬件动作：确认本机 GPU 型号/显存/驱动（`nvidia-smi`、`torch.cuda.is_available()`），决定 PoC 是本机还是租用 GPU。
3. 许可签核：把 §5.10 矩阵过一遍人工确认（尤其商品图商用边界），锁定初选组合 Qwen-Image-Edit + BiRefNet + Real-ESRGAN。

## Acceptance

- criterion-1（简明结论+残余风险）：§0 给出直接答案（薄接入、slot4 PoC、qwen 保留 fallback）；Contradictions 4 条 + Missing evidence 5 条为残余风险。
- 外部事实全部一手（官方仓库源码/LICENSE/文档/通告/HF 官方模型卡），二手教程全部拒绝并记录；研究者推断逐处标注（§3.3、§7 的"未验证假设"等）。
