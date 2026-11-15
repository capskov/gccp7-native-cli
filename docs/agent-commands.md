# Agent 命令地图

**输入**为操作者确认的测试工程、定额身份、工程量或删除模式。**输出**为 UTF-8 JSON 和本地回执。Agent 可以选用命令并解释结果，CLI 负责真实操作、版本检查和防重复。

| 目标 | 命令 | 先决条件 | 工程影响 |
| --- | --- | --- | --- |
| 检查本机 | `gccp doctor` | 配置 MSVC、Windows SDK、Qt，已装匹配的 GCCP7 | 无 |
| 绑定项目 | `gccp bind` | 已打开测试副本、明确路径和 SHA256 | 加载只读观察器，写本地绑定和回执 |
| 查预算和工料机 | `gccp native-inspect` | 绑定返回的 inspector | 只读 |
| 查软件实际候选 | `gccp quota-candidates` | 已绑定工程、当前北京 2025 库、准确编号或 `--vendor-id` | 打开并关闭查询面板，无业务写入 |
| 查定额索引 | `gccp catalog search` | 本机安装的北京 2025 原厂索引 | 只读，可用 `catalog inspect` 核对来源与条数 |
| 重新导出原厂子目索引 | `gccp catalog vendor-index --library-root ... --output ...` | 本机安装的北京 2025 概算库目录 | 只读安装库，另建 JSON |
| 预检套项 | `gccp runtime-prepare` | 复用 `quota-run` 的工程、父项、候选、数量和命令号参数 | 生成原生运行包和请求，可查看 `anchor_check`，零定额业务派发 |
| 套入并填量 | `gccp quota-run` | 原厂 `--vendor-id` 或精确候选、父项 ID 或分部路径、数量和 command_id | 核对现场、插入、填量、读回。分部路径自动免主动保存，顶层常规套项请加 `--no-save` |
| 复用已套定额 | `gccp quota-clone` | 整个项目中选中普通定额父行，工料机显示开启，指定未定价且未锁量的主材 | 原生复制一次，在同一分部向下追加 1 至 5 条并逐条改名、填量、读回。见[定额复用](quota-reuse.md) |
| 核查复用候选 | `gccp quota-reuse-plan` | 成功的只读快照和计划 JSON | 比对已有定额和源主材，返回需核对的重复候选，无业务写入 |
| 补充零价子目 | `gccp quota-supplement` | 整个项目视图、选中目标分部内的直接子目 | 打开原生表单、填写并提交一条补充子目，回读整份预算，无保存派发 |
| 短距离顶进换算 | `gccp quota-short-drive-factor` | 整个项目视图、准确选中 `11-34`、标准换算页、工程量至多 60 m | 勾选内置人工及机械系数 1.15，检查主材与整份预算，无保存派发 |
| 调价后刷新绑定 | `gccp bind --refresh-from-inspector ... --accept-price-updates` | 原进程和账本、操作者确认调价、整个项目视图、新保存文件的 SHA-256 | 核对同一预算，接受价格字段变化，保留新观察器 |
| 删除一个定额 | `gccp quota-delete` | 精确父项、完整名称、编号、单位和模式 | 临时或永久删除，原位读回。二级分部须用完整路径及 `--expected-quantity`，且目标行已经选中 |
| 查执行结果 | `gccp command-status` | 原账本和原 command_id | 读本地回执 |
| 查已套定额的工料机 | `gccp command-status --include-resources` | 同一命令的完整工料机回执 | 读历史明细，无现场操作 |
| 续接永久删除确认 | `gccp quota-delete-confirm` | 原待确认命令和匹配的原生确认框 | 只继续原确认步骤 |
| 核对待定结果 | `gccp command-reconcile` 或 `quota-delete-reconcile` | 原命令、原证据及当前匹配的工程 | 只读核对，记录账本结果 |

安装及可复制示例见 [README](../README.md)。具体参数以对应命令的 `--help` 为准。首次调用先读版本范围，传入操作者授权的工程，避免用演示 ID 直接下单。

## 如何处理回执

正常返回的 `schema` 为 `gccp.native-cli.v0.1`。标准业务成功退出码为 0，`status` 为 `ok`，结果在 `data`。阻断退出码为 2，`status` 为 `blocked`，检查 `error.code`、`error.message` 和 `error.retry_allowed`。

- 有 `receipt_only=true` 时，结果是历史回执。需要当前状态就重新只读观察。
- 退出码异常、缺少回执或操作中断时，先查原命令和当前预算。保留同一账本和命令号，交由操作者判断如何继续。
- 单进程读回、保存证据和独立重开是不同的验证范围。按回执实际记录报告结果。

通用入口接受北京 2025 库中的精确现场候选，包括其他专业和单位。`--parent-id` 定位有业务 ID 的非空顶层分部。二级分部可用逐级重复的 `--parent-section` 配合 `--selected-anchor`，自动采用 `--no-save`。同一父项下每一级名称须唯一，目标分部须已有直接定额行。当前行应是末条定额，或紧邻自动生成空白尾行的上一条定额。旧的分部路径运行包不能用于写入。

同一分部内，新增构件与原行的计量边界独立但共用定额时，可传入 `--item-label` 将用途写进新增行名称。先核对现有行和工程量，再填写独立用途。无用途的新增和同用途的重复新增会被阻止。

原厂库无适用子目时，先切到预算书的“整个项目”，选中目标分部的直接子目。用 `quota-supplement` 传入 `--state`、`--runtime-root`、绑定返回的 `--inspector` 和 `--project-id`、逐级 `--parent-section`、唯一 `--command-id` 及 `--code`、`--name`、`--unit`、`--quantity`。编号用 `补` 加字母、数字或连字符。命令通过广联达原生动作打开表单，也兼容已经打开的空白表单。提交后核对目标分部、数量、价格以及其余预算行。加 `--check-only` 可打开、填写、校验并取消，回读确认预算一致。意外退出时先查 `command-status` 和原工程，待定补项需人工核对原回执。

短距离顶进时，先选中 `11-34` 并打开“标准换算”，用 `quota-short-drive-factor` 传入 `--state`、`--runtime-root`、`--inspector`、`--project-id`、逐级 `--parent-section`、`--quantity` 和唯一 `--command-id`。命令只执行已验证的内置规则。若已接受但需重新核验，用同一组参数加 `--verify-only`，从原回执继续验证。

需要跨子分部时，可加 `--navigate-anchor`。CLI 限制在同一顶层分部内，展开已确认的目标子分部后最多发出三十二次原生网格方向动作，每一步核对预算行，匹配后才开放插入。实机已从顶进分部切回折叠的接收井并套入网片。范围外、树形结构不符或光标偏差时停止。出现 `anchor_navigation_step_mismatch` 时，先用 `command-status`，再用原运行包和请求执行 `command-reconcile`。只有零业务派发且同进程全预算行一致时，才关闭原命令。已关闭的命令号保留，不能重发。

`runtime-prepare` 用于单独检查范围。`anchor_check` 记录目标、光标是否匹配和允许的导航路径。复用运行包时返回原预检结果。常规套项可直接用 `quota-run` 完成预检和写入。嵌套分部当前验证范围为套入、首次填量、工料机读回和指定单行删除。独立修改已有行工程量及整组删除属于待扩展能力。见[诊断与恢复](diagnostics.md)。

### 修改已有主材

先用绑定的 inspector 执行 native-inspect。在预算表中确认主材行路径、父定额编码、工程量及备注。
material-edit 接收 --state、--runtime-root、--project-id、--command-id、--path、--quota-code、
--quota-quantity、--quota-note、--material-code、--field name|content、--expected 和 --value。
可加 --check-only 检查目标和可编辑性。一次修改一格，随后读回。改名后先读回主材编码再继续，
因为广联达可能为独立主材分配新编码。命令不会保存工程。
