# TracePilot | 智能运维故障诊断 Agent

面向微服务线上排障：LLM 分析现象、形成假设和候选检查动作，Jev Decision Model 选择下一步，Runtime 校验范围、Schema、权限与预算，MCP 获取观测数据，Evidence Memory 保存诊断过程及证据。报告提供带来源的候选原因、验证步骤与信息缺口。

## 快速运行

Python 3.11 或 3.12，在仓库根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-tracepilot.txt
python -m tracepilot demo
python -m tracepilot demo --scenario slow-dependency
python -m tracepilot demo --scenario no-data
python -m tracepilot demo --scenario denied
```

模拟模式无需密钥，使用固定观测数据和规则型测试替身，不代表真实模型效果。每次输出任务 ID；重开数据库可检查记忆、决策和原始观测：

```bash
python -m tracepilot inspect TASK_ID
python -m tracepilot evidence TASK_ID EVIDENCE_ID --offset 0 --limit 4000
python -m tracepilot verify TASK_ID
```

真实模型、PostgreSQL、MCP 和数据源配置见 [运行指南](docs/reference/tracepilot.md)。完整 Poetry 环境还提供 `tracepilot` 命令；独立入口无需加载原有集成框架。

## 核心能力

| 能力 | 实现 | 验证方式 |
| --- | --- | --- |
| 推理与动作选择分离 | `tracepilot/providers.py`、`engine.py`、`policy.py` | Jev 只能选择 Runtime 提供的动作，不能直接执行工具 |
| 多轮证据驱动排查 | `DecisionState`、结构化记忆、候选方向 | 相同超时问题根据证据进入连接池或下游分支 |
| Evidence Memory | `tracepilot/store.py`、`models.py` | 事务保存任务/观测/事件，分页读取，校验内容与来源摘要 |
| 独立 MCP 工具层 | `tracepilot/mcp_gateway.py` | stdio、Streamable HTTP、分页发现、命名空间、Schema 变化检测 |
| 真实观测接口 | `tracepilot/observability_server.py` | Loki 日志、Prometheus 指标、Tempo 调用链搜索、Kubernetes 当前实例状态 |
| 执行约束 | 本地白名单、服务/命名空间/时间范围、输入输出 Schema | 越界不调用、审批时停止、有界重试和上下文容量限制 |

连接池模拟先检查实例和调用链，再从结果定位 `checkout-a`，查询该实例日志、指标，最后检查配置变化；下游延迟模拟不会进入连接池配置分支。

## 测试

```bash
pip install pytest responses
python -m pytest -c tests/tracepilot/pytest.ini --confcutdir=tests/tracepilot \
  tests/tracepilot tests/decision/test_engine.py tests/decision/test_runtime_regressions.py \
  tests/decision/test_store.py tests/decision/test_providers.py
```

[CI](.github/workflows/tracepilot-tests.yml) 配置了独立 PostgreSQL 测试数据库；本地未设置 `HOLMES_TEST_POSTGRES_URL` 时会明确跳过对应测试。
[简历能力与实现对照](docs/reference/tracepilot-implementation.md) 记录新增内容和验证边界。

## 来源与边界

本仓库由 HolmesGPT 派生；TracePilot 是本分支的产品名称。原有代码与贡献记录保留，新增工作与既有实现的关系见 [来源说明](THIRD_PARTY_NOTICES.md)。[原始 README](UPSTREAM_README.md) 和 [Apache-2.0 许可证](LICENSE) 保留。

`completed` 表示调查流程结束，不表示根因已确认。引用校验能检查来源，不能证明推断在语义上正确。生产准确率、成本收益与 Jev 概率校准需要接入目标环境后测量。当前为单用户 CLI，未实现多租户调度、自动修复或中断任务自动重放。
