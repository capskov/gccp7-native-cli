# Agent 命令地图

**输入**为操作者确认的测试工程、定额身份、工程量或删除模式。**输出**为 UTF-8 JSON 和本地回执。Agent 可以选用命令并解释结果，CLI 负责真实操作、版本检查和防重复。

| 目标 | 命令 | 先决条件 | 工程影响 |
| --- | --- | --- | --- |
| 检查本机 | `gccp doctor` | 配置 MSVC、Windows SDK、Qt，已装匹配的 GCCP7 | 无 |
| 绑定项目 | `gccp bind` | 已打开测试副本、明确路径和 SHA256 | 加载只读观察器，写本地绑定和回执 |
| 查预算和工料机 | `gccp native-inspect` | 绑定返回的 inspector | 只读 |
| 查定额候选 | `gccp catalog search` | 自备 SQLite 索引 | 只读 |
| 套入并改量 | `gccp quota-run` | 精确候选、父项 ID、数量和 command_id | 插入、改量、保存 |
| 删除一个定额 | `gccp quota-delete` | 精确父项、完整名称、编号、单位和模式 | 临时或永久删除，原位读回 |
| 查执行结果 | `gccp command-status` | 原账本和原 command_id | 读本地回执 |
| 续接永久删除确认 | `gccp quota-delete-confirm` | 原待确认命令和匹配的原生确认框 | 只继续原确认步骤 |
| 核对待定结果 | `gccp command-reconcile` 或 `quota-delete-reconcile` | 原命令、原证据及当前匹配的工程 | 只读核对，记录账本结果 |

安装及可复制示例见 [README](../README.md)。具体参数以对应命令的 `--help` 为准。首次调用先读版本范围，传入操作者授权的工程，避免用演示 ID 直接下单。

## 如何处理回执

正常返回的 `schema` 为 `gccp.native-cli.v0.1`。标准业务成功退出码为 0，`status` 为 `ok`，结果在 `data`。阻断退出码为 2，`status` 为 `blocked`，检查 `error.code`、`error.message` 和 `error.retry_allowed`。

- 有 `receipt_only=true` 时，结果是历史回执。需要当前状态就重新只读观察。
- 退出码异常、缺少回执或操作中断时，先查原命令和当前预算。保留同一账本和命令号。
- 单进程读回、保存证据和独立重开是不同的验证范围。按回执实际记录报告结果。

本版套项支持北京2025管网球墨铸铁管件 3-193～3-208 家族，指定唯一且非空的顶层分部。详见 [验证范围](architecture.md) 和 [异常恢复](diagnostics.md)。
