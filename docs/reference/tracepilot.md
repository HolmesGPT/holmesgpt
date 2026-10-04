# TracePilot 运行指南

## 运行结构

```mermaid
flowchart TD
    L[LLM 分析与候选动作] --> R[Runtime 校验]
    R --> J[Jev 动作选择]
    J -->|查询| M[MCP 工具]
    M --> E[证据与审计事务]
    E --> J
    J -->|需要新参数或解释| L
    J -->|结束请求| V[报告与引用校验]
    V --> D[保存终态和报告]
```

LLM 和 Jev 是两个外部推理接口，由同一个运行时协调。规则负责硬约束，Jev 负责封闭动作集合中的选择；没有自研模型训练。旧的 `holmes decision` 命令仍可在完整依赖环境中使用，新入口为 `python -m tracepilot`。

## 真实模型与模拟观测

先按首页安装依赖，再配置密钥；示例模型 ID 可替换为账号当前可用的型号：

```bash
export ANTHROPIC_API_KEY='YOUR_KEY'
export TYPESAFE_API_KEY='YOUR_KEY'
python -m tracepilot investigate '排查 checkout 接口超时，提供证据和验证步骤' \
  --config examples/tracepilot/fixture.yaml \
  --service checkout --namespace demo \
  --start 2026-09-28T08:00:00Z --end 2026-09-28T08:15:00Z --events
```

此命令实际调用模型 API，但观测来自带 `simulated: true` 标记的本地 MCP 服务。纯 `demo` 命令则连模型都不调用。未配置密钥不会降级到模拟模式。

也支持 `llm.provider: openai-compatible`、`api_key_env` 和可选 `base_url`；接口使用 `/chat/completions`、JSON 响应格式、`max_completion_tokens`，需确认所用服务支持该合同。Anthropic 使用 `/messages`。没有自动验证付费模型服务的账号可用性。

## 实际观测数据

使用 `examples/tracepilot/observability.yaml`，配置数据源 URL，传入真实故障的服务、命名空间及带时区时间窗口。内部服务可使用本地 HTTP 转发或 HTTPS 地址。

| 数据源 | 环境变量前缀 | 工具 | 约定 |
| --- | --- | --- | --- |
| Loki | `LOKI` | `service_logs` | `service`、`namespace`、可选 `pod` 标签；最多 100 条；过滤文本只作为字面量 |
| Prometheus | `PROMETHEUS` | `service_metrics` | 传入真实 metric 名；强制附带范围标签，最多 100 序列、约 1440 时间点；不接受任意 PromQL |
| Tempo | `TEMPO` | `service_traces` | TraceQL 搜索，要求 `resource.service.name` 和 `resource.k8s.namespace.name`，最多 50 条 |
| Kubernetes | `KUBERNETES` | `service_instances` | `app.kubernetes.io/name` 标签；最多 100 Pod；仅当前状态，不能代表历史状态 |

每个前缀支持 `_URL`、可选 `_TOKEN`（Bearer）、`_TENANT` 和 `_CA_BUNDLE`。需要的变量必须加入 `env_names` 才传入 stdio 子进程；列出的变量必须存在且非空，不会把模型密钥自动传给工具进程。未配置 URL 的数据源不会注册工具。

可用 `OBS_SERVICE_LABEL`、`OBS_NAMESPACE_LABEL`、`OBS_POD_LABEL` 调整 Loki/Prometheus 标签名，也需加入 `env_names`。不同标签名不能相同。指标名和标签必须符合实际埋点；空查询结果不表示系统健康。Tempo 当前实现为调用链搜索，不是任意跨服务图谱分析。

HTTP 封装只执行 GET，拒绝重定向并限制响应为 2 MB；MCP 客户端另外限制返回大小。超限明确失败，需缩小查询范围，不会把丢弃的数据伪装成完整证据。外部 MCP `isError` 若没有可信类型信息，按未知错误处理，不根据错误文字猜测是否可重试。

## 自定义 MCP 服务

```yaml
mcp_servers:
  telemetry:
    transport: streamable_http
    url: https://your-mcp.example/mcp
    headers_env:
      Authorization: TELEMETRY_AUTHORIZATION
    tools:
      query_logs:
        read_only: true
        retry_safe: true
        scope:
          service: service_name
          namespace: namespace
          start: start_time
          end: end_time
```

本地工具策略使用远端原始名称；模型看到带服务器前缀的 `telemetry__query_logs`，避免同名冲突。`read_only: true` 是操作者对工具的声明，远端 `readOnlyHint` 不授予权限。`approval_required: true` 会停为 `needs_input`，不会自动批准。

范围绑定只支持顶层精确服务/命名空间和 ISO 时间字段；工具参数必须是任务范围的子集。无历史时间参数的快照工具可明确将 `start`、`end` 同时设为 `null`。这些约束不替代后端 RBAC，也不是多租户鉴权。

每次发现和执行各自创建并关闭 MCP 会话；执行会在同一会话重新发现 Schema 再调用。stdio 因此存在启动开销，适合无会话状态的查询工具；长期服务可用 Streamable HTTP。支持远端工具动态增删与 Schema 变化，本地配置文件变更需重启任务。

## 存储、检查与预算

```bash
export TRACEPILOT_DATABASE_URL='postgresql+pg8000://USER:PASSWORD@localhost:5432/tracepilot'
python -m tracepilot inspect TASK_ID
python -m tracepilot verify TASK_ID
python -m tracepilot evidence TASK_ID EVIDENCE_ID --offset 0 --limit 4000
```

默认真实任务使用 `tracepilot.db`，演示使用 `tracepilot-demo.db`；检查真实任务时应传入对应 URL 或设置环境变量。数据库继续使用 `decision_agent_tasks`、`decision_agent_evidence`、`decision_agent_events`，兼容此前记录。状态以 revision 乐观并发更新；证据、审计与状态一起提交；执行外部调用前先保存意图。

`verify` 对照状态检查点中的 SHA-256 检查返回值和完整证据记录。旧记录只有返回值摘要时仍可检查，`full_record_checks` 会显示较少的覆盖数。这是完整性检测，不是数字签名，不能抵御同时修改数据库记录和摘要的管理员。

默认 20 轮、10 次工具尝试、5 次中间推理、300 秒循环预算；最后报告单独调用模型。只对本地声明可重试且类型明确的临时失败重试，重试也占用工具预算。相同工具版本与参数的已完成查询不会因候选 ID 改变而重放。

模型上下文保留最近证据预览和动作，完整证据可分页读取；默认模型请求上限 160,000 字符，超限明确拒绝。字符限制不是 Token 精确计费。MCP 有单次超时；总循环预算在操作间检查，并不是涵盖最终报告的硬端到端截止时间。

报告中事实和候选原因必须引用本任务的有效观测，不能引用错误、审批拒绝或证据读取动作作为独立根因依据。Runtime 生成摘要和 outcome；候选原因始终需要验证。数据库重开检查不等于中断任务自动恢复，目前不重放状态不明的外部调用。

## 验证边界与接口参考

测试验证模拟模型 HTTP 合同、真实本地 MCP 传输、范围拒绝、Schema 变更、证据损坏、数据库重开和多轮分支。没有运行真实付费 LLM/Jev、生产监控系统或生产准确率评测。CI 定义了 PostgreSQL 服务，但是否通过须以实际 CI 运行结果为准。

接口参考：[Prometheus API](https://prometheus.io/docs/prometheus/latest/querying/api/)、[Loki API](https://grafana.com/docs/loki/latest/reference/loki-http-api/)、[Tempo API](https://grafana.com/docs/tempo/latest/api_docs/)、[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)。
