# 来源与贡献说明

TracePilot 基于 `mochengqian/holmesgpt` 的 `0251a6e91d62bb1244f48a447de0f2fcd20f0f21` 继续开发。该仓库派生自 [HolmesGPT/holmesgpt](https://github.com/HolmesGPT/holmesgpt)，适用 Apache License 2.0；原有作者、版权声明、许可证和提交历史继续保留。

本轮将已有 `holmes/decision` 中的模型、循环、证据存储、报告校验、Jev 接口和错误分类迁移至 `tracepilot`，原导入路径保留兼容导出。这些文件是既有代码的演进，不宣称为本轮从零原创。

本轮新增和扩展：独立 CLI、轻量运行依赖、HTTP 推理适配、直接 MCP SDK 接入、范围与 Schema 约束、记录完整性检查、只读观测 HTTP 封装、证据分支模拟、协议集成测试和 CI。

MCP SDK 负责协议与传输，SQLAlchemy 负责数据库抽象，Pydantic/jsonschema 负责结构校验，Tenacity 负责重试。各依赖适用各自许可证。生产模式调用外部 LLM 和 TypeSafe Jev API，没有训练或微调自有基础模型。

产品改名不改变代码来源。保留原有开源归属，不将整个衍生仓库描述为从零实现。
