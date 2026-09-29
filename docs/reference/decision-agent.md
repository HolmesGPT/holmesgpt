# 通用服务智能排障 Agent

这个 fork 在 HolmesGPT 上新增 `holmes decision`，将复杂推理与执行决策分开：
LLM 理解故障、更新假设、生成候选工具参数、撰写报告；Jev 从候选动作中选择下一步，
返回动作和置信度；运行时校验参数、权限、工具版本和预算，再执行工具并保存证据。

| 能力 | 实现位置 | 可观察行为 |
| --- | --- | --- |
| LLM + Jev 双模型循环 | `holmes/decision/providers.py`, `engine.py`, `policy.py` | LLM 一次生成多个候选调用；运行时计算可选动作，Jev 从中选择；候选为空仍可结束调查 |
| Decision State | `holmes/decision/models.py` | 任务上下文、历史动作、事实、假设、反证、验证结果、可用工具、候选动作及剩余预算 |
| Evidence Memory | `holmes/decision/store.py` | PostgreSQL 持久化工具返回值和结构化记忆；上下文只保留有界预览，原始返回值可按证据 ID 分页读取 |
| MCP 工具生命周期 | `holmes/decision/tools.py`, `holmes/config.py` | 复用 Holmes 的 MCP 发现、鉴权、错误处理和刷新；名称、描述或参数 Schema 改变都会使缓存失效 |
| 报告校验 | `holmes/decision/reporting.py` | LLM 输出结构化草稿，程序验证引用后生成摘要，并同时保存最终记忆和终态 |

这些模块在同一个进程中运行。运行时负责状态转换和执行授权，LLM 提供分析与候选参数，
Jev 提供动作选择，MCP 提供数据源接入，PostgreSQL 保存任务、证据及事件。

## 运行

在仓库根目录安装依赖。锁文件由 Poetry 1.8.5 维护：

```bash
poetry install --with dev

# 无 API 密钥的确定性模拟，保存到 /tmp/holmes-decision-demo.db。
# 此变量阻止 LiteLLM 在导入时下载模型价格表。
LITELLM_LOCAL_MODEL_COST_MAP=True poetry run holmes decision demo
```

演示模拟 `checkout` 服务的连接池等待问题，使用本地四类观测数据。
它验证循环、持久化、证据引用和命令入口；模拟模式中的决策器与分析器均为测试夹具。

真实运行使用 PostgreSQL。先创建数据库，再设置连接字符串及模型密钥：

```bash
# PostgreSQL 示例；替换连接信息，凭据通过环境变量提供。
export HOLMES_DECISION_DATABASE_URL='postgresql+pg8000://USER:PASSWORD@localhost:5432/holmes_agent'
export TYPESAFE_API_KEY='YOUR_TYPESAFE_KEY'
export ANTHROPIC_API_KEY='YOUR_ANTHROPIC_KEY'

# 使用真实 Jev + LLM，以及示例 MCP 服务的模拟观测数据。
# 从仓库根目录运行，stdio MCP 子进程使用 Poetry 环境中的 python。
poetry run holmes decision investigate \
  '排查 checkout 在 2026-09-28 08:00–08:15 UTC 的接口超时，给出依据和验证步骤' \
  --config examples/decision_agent/config.example.yaml \
  --allow-tool service_logs --allow-tool service_metrics \
  --allow-tool service_config --allow-tool service_instances \
  --retry-safe-tool service_logs --retry-safe-tool service_metrics \
  --events
```

首次连接会创建 `decision_agent_tasks`、`decision_agent_evidence`、`decision_agent_events`
三张表。已有表不会被清空；这是一版初始表结构，后续结构升级需要显式迁移。
SQLite URL 可用于开发，但不能据此声称验证了 PostgreSQL 的生产部署。

示例 MCP 服务只提供模拟数据。接入真实服务时，替换配置中的 `mcp_servers`，或显式启用
Holmes 已有的 Prometheus、Loki、Kubernetes 等工具集。该模式不会自动启用全部可用工具。
`--allow-tool` 可多次传入，限制当前会话的工具集合；实际的后端权限继续由数据源凭据决定。
工具集需配好只读权限及 Holmes 的 `approval_required_tools`，需要审批的调用会结束为
`needs_input`，运行时不会自动将 `user_approved` 设为真。

也可以使用独立入口：`poetry run python -m holmes.decision --help`。

## 一次排障的执行过程

1. 创建任务，发现当前可用工具，调用 LLM 形成结构化记忆和候选检查动作。
2. 运行时按预算、调用历史和工具版本生成可选动作，Jev 请求与响应校验共用这份集合。
3. 推理次数用尽只禁用重新推理，工具调用次数用尽只禁用工具调用；候选为空不会强制调用 LLM。
4. 校验返回动作及置信度。低置信度在预算允许时交回 LLM，无法继续推理时以低置信度停止。
5. 每次执行或重试前重新发现工具并核对 Schema、重试策略及审批要求。
6. 先记录调用意图，再执行工具；每次尝试的结果、证据、任务状态和审计事件在同一事务中提交。
7. 新事实可推动 Jev 选择其他方向；最终报告调用会从最新证据更新结构化记忆，不额外强制规划一次。
8. Jev 的 `finish` 只发出结束请求。报告和记忆通过引用检查后，才在同一事务中提交报告及终态。

Jev 使用 [TypeSafe 官方 HTTP 接口](https://docs.typesafe.ai/api)的 Choice 协议：
`POST https://api.typesafe.ai/v1/systemone`，请求为 `model`、`state`、`questions`，
响应读取 `answers.next_action.choice`、`confidence`、`probabilities`。
工具参数由 LLM 生成并通过 JSON Schema 校验，Jev 选择封闭集合中的动作。
Jev 的认证错误或格式错误会明确失败；429、5xx 和连接超时使用有限次数退避重试。

默认限制为 20 轮、10 次工具调用、5 次中间 LLM 推理、300 秒循环预算和 0.65 置信度阈值。
最后一次报告生成独立于中间推理次数。时间预算在操作之间检查，不会强制杀死正在执行的
同步工具；来源工具本身仍需配置请求超时。实际端到端耗时还包含初始化、在途请求和最终报告。
0.65 是可配置的初始策略值，需要通过故障样本校准。

调用指纹包含工具名称、工具版本和规范化参数；工具版本也包含来源及本地重试策略。
在同一任务内，成功或正常查无数据的同一查询不会重复执行，更换候选动作 ID 也不能重置历史。
需要重新采样时，应使用新的显式采样时间参数或新任务，避免把旧观测误当成当前状态。

**失败与重试**

`--retry-safe-tool NAME` 表示操作者已确认这个工具只读且允许重试，不会授予额外的调用权限。
默认不自动重试未标记的外部工具。已标记工具只有遇到可分类的超时、连接失败、限流或临时
服务错误才重试；审批、权限、参数错误及无法可靠分类的错误都不自动重试。
默认最多 3 次总尝试（含首次），可通过 `--max-tool-attempts` 调整，每次尝试都占用工具调用预算。
使用 Tenacity 进行指数退避和随机抖动，并遵守可解析的 `Retry-After` 秒数或 HTTP 日期。
等待或下一次尝试无法容纳在剩余预算内时，停止该调用的重试，其他可用动作仍可继续。

每次尝试拥有独立 `attempt_id`、`attempt_no` 和证据 ID，同一逻辑查询共享调用指纹。
重试耗尽后不能靠重新规划、更换候选 ID 继续尝试。尚无结果的 `started` 调用不会自动重放。
Holmes MCP 适配层保留类型明确的传输异常；仅有错误字符串的旧工具结果按未知错误处理。
这里计数的是 Holmes 工具调用尝试，工具内部的 SDK 重试仍需配置自己的超时与上限。

**检查范围与规划进展**

不同数据源或不同查询范围返回相同内容，仍是不同检查；`no_data` 也是正常完成的范围查询，
不能因此断言服务健康。结果 SHA-256 用于完整性检查，不再用于跨工具判断是否有进展。
连续失败不会自动跳过其余数据源。反复规划但没有产生新可执行调用时，才增加 `no_progress`；
达到默认 3 次阈值后禁用继续规划，仍保留已有可执行调用、结束及补充信息选项。
新增查询是否有诊断价值仍需模型判断，整体探索受轮数、调用次数和时间预算限制。

## 证据与任务检查

命令输出任务 ID 和报告所引用的证据 ID。可以在进程重启后读取：

```bash
poetry run holmes decision inspect TASK_ID
poetry run holmes decision evidence TASK_ID EVIDENCE_ID --offset 0 --limit 4000

# 读取默认离线演示数据库
poetry run holmes decision inspect TASK_ID \
  --database-url sqlite:////tmp/holmes-decision-demo.db
```

每条证据记录来源工具集、工具名称、查询参数、采集时间、耗时、执行状态、完整返回值、
错误信息。提供给模型的预览包含长度、截断标记和 SHA-256；分页读取只允许本任务的证据。
这里的完整返回值是 **Holmes 工具接口返回的内容**，数据源查询范围或已有 transformer
可能已经过滤/处理过内容，不等同于数据源的全部原始日志。

结构化记忆中的事实、反证、验证结果必须引用本任务的成功或正常无数据观测。假设可没有引用，
始终作为假设；如果引用了证据，同样需要通过检查。报告的候选原因也使用这个范围，错误、
审批拒绝和证据读取动作自身不能证明根因，读取后应引用原始观测 ID。

LLM 的 `DiagnosisDraft` 不接受自由文本 `summary` 或自定义 `outcome`。运行时验证草稿后，
根据候选原因数量生成“候选原因仍需验证”或“证据不足”的摘要。任务的 `completed` 仅代表
调查流程结束；报告的 `outcome` 为 `candidate_causes`、`insufficient_evidence` 或
`invalid_report`，没有由模型自行宣布的“根因已确认”级别。验证步骤和信息缺口仍是模型建议。
引用检查验证 ID 和观测状态，不能代替对证据是否支持推断的语义评测。

新报告保存 `schema_version=2`；旧报告仍可读取，但标为 `legacy_unverified`，不表示已经通过
新规则验证。新增任务/调用字段兼容旧 JSON 记录，不需要更改已有三张表的结构。

任务更新使用 revision 条件更新，过期写入不会覆盖新状态；对应证据和审计事件也会回滚。
工具调用与数据库属于不同系统，不能保证跨系统 exactly-once。调用前记录的 `started`
用于排查进程中断；已有任务支持查看，不会自动重放不确定是否完成的调用。
这是单用户 CLI 入口，尚未提供 Web 服务的多租户鉴权或任务调度。

## 验证与实现边界

```bash
# 针对新增模块的独立测试，不加载上游 LLM 评测设施。
LITELLM_LOCAL_MODEL_COST_MAP=True poetry run pytest tests/decision \
  --confcutdir=tests/decision -o addopts='' -q

# 对一次性 PostgreSQL 测试数据库运行存储集成测试。
export HOLMES_TEST_POSTGRES_URL='postgresql+pg8000://USER:PASSWORD@localhost:5432/holmes_agent_test'
LITELLM_LOCAL_MODEL_COST_MAP=True poetry run pytest tests/decision/test_store.py \
  --confcutdir=tests/decision -o addopts='' -q
```

测试覆盖真实本地 MCP stdio 发现和调用、Jev 动态动作集合、报告摘要绕过、终态与报告提交、
逐次重试审计、预算和退避限制、跨来源空结果、规划停滞、低置信度、工具 Schema/策略变化、
审批、幻觉证据引用、数据库重开和过期写入回滚。PostgreSQL 用例
在未提供测试数据库 URL 时会明确跳过；测试数据使用独立任务 ID，不删除已有任务。

真实 Jev/LLM 效果、生产故障诊断准确率以及成本/延迟收益需要配置密钥后单独评测。
本改动不包含已测得的性能提升承诺。可以用固定故障集比较原有 ReAct 与新循环，记录
任务完成率、正确证据引用率、错误停止率、LLM/Jev 调用次数、token 费用和总耗时。

HolmesGPT 提供原有工具接入和运行基础；本 fork 的新增贡献集中在决策控制、证据记忆、
执行约束和测试。上游许可证及来源信息保留在仓库中。
