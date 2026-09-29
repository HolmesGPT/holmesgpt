# 通用服务智能排障 Agent

这个 fork 在 HolmesGPT 上新增 `holmes decision`，将复杂推理与执行决策分开：
LLM 理解故障、更新假设、生成候选工具参数、撰写报告；Jev 从候选动作中选择下一步，
返回动作和置信度；运行时校验参数、权限、工具版本和预算，再执行工具并保存证据。

| 能力 | 实现位置 | 可观察行为 |
| --- | --- | --- |
| LLM + Jev 双模型循环 | `holmes/decision/providers.py`, `engine.py` | LLM 一次生成多个候选调用，Jev 可以连续选择工具；需要新参数或复杂分析时才重新调用推理 LLM |
| Decision State | `holmes/decision/models.py` | 任务上下文、历史动作、事实、假设、反证、验证结果、可用工具、候选动作及剩余预算 |
| Evidence Memory | `holmes/decision/store.py` | PostgreSQL 持久化工具返回值和结构化记忆；上下文只保留有界预览，原始返回值可按证据 ID 分页读取 |
| MCP 工具生命周期 | `holmes/decision/tools.py`, `holmes/config.py` | 复用 Holmes 的 MCP 发现、鉴权、错误处理和刷新；名称、描述或参数 Schema 改变都会使缓存失效 |

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
2. 将 Decision State 发给 Jev，选项包含具体候选调用、重新推理、结束、请求补充信息。
3. 校验返回动作及置信度；低置信度交回 LLM 重新规划，并受推理次数预算约束。
4. 执行前重新发现工具并核对 Schema 指纹；工具删除、停用或参数变更会使旧动作失效。
5. 先记录调用意图，再执行工具。结果、证据摘要、任务状态和审计事件在同一事务中提交。
6. 新事实可推动 Jev 选择其他检查方向；必要时 LLM 重新解释证据并生成新候选。
7. 结束时由 LLM 输出候选原因、证据 ID、验证步骤和信息缺口，运行时再次检查引用。

Jev 使用 [TypeSafe 官方 HTTP 接口](https://docs.typesafe.ai/api)的 Choice 协议：
`POST https://api.typesafe.ai/v1/systemone`，请求为 `model`、`state`、`questions`，
响应读取 `answers.next_action.choice`、`confidence`、`probabilities`。
工具参数由 LLM 生成并通过 JSON Schema 校验，Jev 选择封闭集合中的动作。
Jev 的认证错误或格式错误会明确失败；429、5xx 和连接超时使用有限次数退避重试。

默认限制为 20 轮、10 次工具调用、5 次中间 LLM 推理、300 秒循环预算和 0.65 置信度阈值。
最后一次报告生成独立于中间推理次数。时间预算在操作之间检查，不会强制杀死正在执行的
同步工具；来源工具本身仍需配置请求超时。实际端到端耗时还包含初始化、在途请求和最终报告。
0.65 是可配置的初始策略值，需要通过故障样本校准。

完全相同的工具名和参数只执行一次，连续重复结果或失败会触发无进展停止。
需要重新采样时，应使用新的显式采样时间参数或新任务，避免把旧观测误当成当前状态。

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

结构化记忆中的事实、反证、验证结果必须引用已收集证据。假设可没有引用，始终作为假设。
报告中的候选原因只能引用成功或无数据结果；错误和审批被拒绝的结果不能证明根因。
引用检查验证 ID 的存在和结果状态，不能代替对证据是否支持结论的语义评测。

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

测试覆盖真实本地 MCP stdio 发现和调用、Jev HTTP 契约与错误处理、低置信度、工具 Schema
变化、重复调用、预算、审批、幻觉证据引用、数据库重开和过期写入回滚。PostgreSQL 用例
在未提供测试数据库 URL 时会明确跳过；测试数据使用独立任务 ID，不删除已有任务。

真实 Jev/LLM 效果、生产故障诊断准确率以及成本/延迟收益需要配置密钥后单独评测。
本改动不包含已测得的性能提升承诺。可以用固定故障集比较原有 ReAct 与新循环，记录
任务完成率、正确证据引用率、错误停止率、LLM/Jev 调用次数、token 费用和总耗时。

HolmesGPT 提供原有工具接入和运行基础；本 fork 的新增贡献集中在决策控制、证据记忆、
执行约束和测试。上游许可证及来源信息保留在仓库中。
