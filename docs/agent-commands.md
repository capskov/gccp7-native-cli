# Agent 命令地图

**输入**为操作者确认的测试工程、定额身份、工程量或删除模式。**输出**为 UTF-8 JSON 和本地回执。Agent 可以选用命令并解释结果，CLI 负责真实操作、版本检查和防重复。

| 目标 | 命令 | 先决条件 | 工程影响 |
| --- | --- | --- | --- |
| 检查本机 | `gccp doctor` | 配置 MSVC、Windows SDK、Qt，已装匹配的 GCCP7 | 无 |
| 绑定项目 | `gccp bind` | 已打开测试副本、明确路径和 SHA256 | 加载只读观察器，写本地绑定和回执 |
| 查预算和工料机 | `gccp native-inspect` | 绑定返回的 inspector | 只读 |
| 查软件实际候选 | `gccp quota-candidates` | 已绑定工程、当前北京 2025 库、准确编号或 `--vendor-id` | 打开并关闭查询面板，无业务写入 |
| 查定额索引 | `gccp catalog search` | 随 CLI 安装的北京 2025 原厂索引 | 只读，可用 `catalog inspect` 核对来源与条数 |
| 重新导出原厂子目索引 | `gccp catalog vendor-index --library-root ... --output ...` | 本机安装的北京 2025 概算库目录 | 只读安装库，另建 JSON |
| 预检套项 | `gccp runtime-prepare` | 复用 `quota-run` 的工程、父项、候选、数量和命令号参数 | 生成原生运行包和请求，可查看 `anchor_check`，零定额业务派发 |
| 套入并填量 | `gccp quota-run` | 原厂 `--vendor-id` 或精确候选、父项 ID 或分部路径、数量和 command_id | 核对现场、插入、填量、读回。分部路径自动免主动保存，顶层常规套项请加 `--no-save` |
| 复用已套定额 | `gccp quota-clone` | 整个项目中选中普通定额父行，工料机显示开启，指定未定价且未锁量的主材 | 原生复制一次，在同一分部向下追加 1 至 5 条并逐条改名、填量、读回。见[定额复用](quota-reuse.md) |
| 核查复用候选 | `gccp quota-reuse-plan` | 成功的只读快照和计划 JSON | 比对已有定额和源主材，返回需核对的重复候选，无业务写入 |
| 补充零价子目 | `gccp quota-supplement` | 整个项目视图、选中目标分部内的直接子目，或用 `--navigate-anchor` | 打开原生表单、填写并提交一条补充子目，回读整份预算，无保存派发 |
| 短距离顶进换算 | `gccp quota-short-drive-factor` | 整个项目视图、准确选中 `11-34`、标准换算页、工程量至多 60 m | 勾选内置人工及机械系数 1.15，检查主材与整份预算，无保存派发 |
| 调价后刷新绑定 | `gccp bind --refresh-from-inspector ... --accept-price-updates` | 原进程和账本、操作者确认调价、整个项目视图、新保存文件的 SHA-256 | 核对同一预算，接受价格字段变化，保留新观察器 |
| 删除一条预算行 | `gccp quota-delete` | 精确父项、完整名称、编号、单位和模式 | 分部路径模式还需 `--expected-quantity`，可用 `--expected-note`、`--path` 区分同量行，`--kind` 指定类别，`--navigate-target` 请求原生定位 |
| 删除空分部 | `gccp section-delete` | 完整分部路径、唯一命令号、目标无子行 | 原生定位并移除空分部，读回层级和其余预算行 |
| 修改预算分部名称 | `gccp section-rename` | `native-inspect` 回读的分部行路径、原名称、新名称、唯一命令号 | 支持 `--check-only`。正式执行后核对目标名称和整棵预算树，保留待保存状态 |
| 修改工程名称 | `gccp project-rename` | 当前工程树中的完整原名称、新名称、唯一命令号 | 支持 `--check-only`。正式执行后核对工程树名称和当前预算，使用 `project-save` 保存到原路径 |
| 修改单位工程名称 | `gccp unit-rename` | 工程树的完整项目名称、当前单位工程名称、新名称、唯一命令号 | 支持 `--check-only`，核对单位工程名称和当前预算 |
| 切换单位工程预算 | `gccp unit-select` | 完整项目名称、单位工程名称和该预算内已知的一个顶层分部名称 | 切换后核对目标分部，业务写入为零 |
| 取消改量清空明细提示 | `gccp quota-quantity-cancel` | 原待核对的改量命令号和对应的广联达提示 | 只按提示中的“否”，回读原预算后关闭待核对状态 |
| 修改已有定额工程量 | `gccp quota-quantity` | 已绑定工程、行路径、编码、名称、单位、备注、原表达式、新工程量和命令号 | 修改表达式，读回工程量及其余预算行，可加 `--check-only`，无需保存 |
| 核对或关闭原生查询窗 | `gccp quota-candidates --query-status`、`--close-query` | 原工程绑定 | 核对唯一查询窗，关闭后校验预算不变 |
| 保存当前工程 | `gccp project-save` | 已绑定、已完成预算复核、无未决命令，原生保存动作唯一可用 | 派发一次保存，核对原路径文件 SHA-256、CRC 和同进程预算，可先加 `--check-only` |
| 查执行结果 | `gccp command-status` | 原账本和原 command_id | 读本地回执 |
| 查已套定额的工料机 | `gccp command-status --include-resources` | 同一命令的完整工料机回执 | 读历史明细，无现场操作 |
| 续接永久删除确认 | `gccp quota-delete-confirm` | 原待确认命令和匹配的原生确认框 | 只继续原确认步骤 |
| 核对待定结果 | `gccp command-reconcile` 或 `quota-delete-reconcile` | 原命令、原证据及当前匹配的工程 | 只读核对，记录账本结果 |

连续同口径、同工艺的管件默认先核一条，再优先用 `quota-clone --variants` 复制并填写各自主材和数量。按 [定额复用](quota-reuse.md) 核对源行、安装工艺和工料机。

安装及可复制示例见 [README](../README.md)。具体参数以对应命令的 `--help` 为准。首次调用先读版本范围，传入操作者授权的工程，避免用演示 ID 直接下单。

需要先建预算分部时，按 [分部创建与套项](budget-sections.md) 操作。

## 人材机载价

在人材机汇总页选“全部”，用 `bind --price-summary` 绑定，再用 `price-view` 取完整清单。只为正用量、未锁定、市场价为零的行准备报价。

- `price-catalog --package <房建缓存目录> --association <京标价协缓存目录> --market <外挂库>` 读取三个本地价源。加 `--assistant-library <新的SQLite路径>` 可生成原助手能打开的合并库。
- `price-plan --capture <price-view的JSON> --package <房建目录> --association <京标价协目录> --market <外挂库> --tax-basis ex_tax` 按房建、京标价协、外挂顺序匹配。核对工程税价口径。用 `--aliases` 提供已审核的同单位近似候选，每条含 `target`、`quotes` 和 `reason`，可带 `preferred_brand`。选取可比报价原值，缺价项留空。
- `price-apply --request <审核后的JSON> --command-id <批次号>` 使用随 CLI 安装的助手。每批最多 8 项，每项含 `target`（编码、类别、名称、规格、单位）、`price`、`source`、`tax_basis` 和包含原价 `ex_tax`、`incl_tax` 的 `quote`。重新载价还须提供 `expected_current`，核对旧 `price`、`source` 和 `quantity`。可先加 `--check-only`，调试独立助手时可指定 `--assistant-root`。
- 中断后用 `command-status` 和 `price-reconcile` 核对原批次。报价已完整载入且尚未确认时可用 `price-reconcile --confirm-staged` 续接。自动保存替换文件后，用原工程、原账本、原 inspector 和新 SHA-256 执行 `bind --price-summary --refresh-from-inspector`。最终用 `project-save --price-summary` 保存并核对磁盘文件。

### 找库、核对来源和复制

1. 在广材助手的“信息价”选择所需月份，分别选择房建和京标价协。下载后查看 `%APPDATA%\GrandSoft\CMJGB\jgb-main`。例如十月房建目录是 `北京_XXJ_2026_10月`，同月京标价协目录是 `北京_XXJ_2026_10月京标价协`。CLI 用 `Chapter.dat` 核对发布机构，按 `TableStructDefine.txt` 解析 `Material.dat`，保留来源编号及含税、除税原值。
2. 外挂市场价库是外挂助手的 `data\材料市场价-*.sqlite`，也可显式指定助手中已选择的库。CLI 以只读方式读取 `quote` 和 `capture`，保留报价月份、品牌、型号及价格。先核对库的用途和条数，避免把专题库误当成通用库。
3. 在同一个 PowerShell 会话执行下列命令。合并时复制原外挂库，再将两个信息价期表写入新 SQLite。新库可在外挂助手用“打开数据库”选择。输出文件名必须是新路径。

```powershell
$cache = Join-Path $env:APPDATA 'GrandSoft\CMJGB\jgb-main'
$housing = Join-Path $cache '北京_XXJ_2026_10月'
$association = Join-Path $cache '北京_XXJ_2026_10月京标价协'
$market = (Resolve-Path 'GccpMaterialPriceAssistant\data\材料市场价-2026-11.sqlite').Path
$merged = Join-Path (Split-Path $market) '材料市场价-合并2026-11.sqlite'

gccp price-catalog --package $housing --association $association --market $market --text '圆钉'
gccp price-catalog --package $housing --association $association --market $market --assistant-library $merged
```

以后更新月份，用 `gccp price-catalog --period 2026-10 --copy-packages <新的原包存档目录> --market $market --assistant-library <新的SQLite路径>`。命令从广材助手下载目录同步两套完整原厂包并解读报价。重读存档时传 `--period 2026-10 --cache-root <原包存档目录>`，也可用于 `price-plan`。

市场价沿用 `InputAllResRate` 双击载价消息。报价名称、规格、品牌、供应商和原始含税、除税价送入对应字段，价格来源显示为 `市场价(年月)`，有日的保留到日。房建显示 `北京信息价(年月)`，京标价协使用 `北京信息价(年月京标价协)`。报价日期随原报价保存在回执，供应时间字段保留广联达原有用途。读回中 `metadata_unconfirmed` 标明应用未回显的字段，包括同价汇总行上丢失的来源。工程原材料的名称、规格和数量用于核对目标身份，借用型号记录在报价凭据。

## 如何处理回执

正常返回的 `schema` 为 `gccp.native-cli.v0.1`。标准业务成功退出码为 0，`status` 为 `ok`，结果在 `data`。阻断退出码为 2，`status` 为 `blocked`，检查 `error.code`、`error.message` 和 `error.retry_allowed`。

- 有 `receipt_only=true` 时，结果是历史回执。需要当前状态就重新只读观察。
- 退出码异常、缺少回执或操作中断时，先查原命令和当前预算。保留同一账本和命令号，交由操作者判断如何继续。
- 单进程读回、保存证据和独立重开是不同的验证范围。按回执实际记录报告结果。

通用入口接受北京 2025 库中的精确现场候选，包括其他专业和单位。`--parent-id` 定位有业务 ID 的非空顶层分部。二级分部可用逐级重复的 `--parent-section` 配合 `--selected-anchor`，自动采用 `--no-save`。同一父项下每一级名称须唯一，目标分部须已有直接定额行。当前行应是末条定额，或紧邻自动生成空白尾行的上一条定额。旧的分部路径运行包不能用于写入。

新增定额保留原厂名称。`quota-run` 把与命令号对应的 `[CLI:xxxxxxxx]` 追踪码写入备注，用于读回定位和防重复。确有独立计量用途时，用 `--item-label` 将用途一并写入备注。同一分部的同用途重复新增会被阻止，已有旧格式的用途也会参与检查。

图纸给出管件、阀门等主材名称或型号时，首套后用 `material-edit --field name` 完善主材，复用时用 `quota-clone --material-name` 或 `--variants` 填实际名称及规格。现有流程通过名称更名时可写成“球墨铸铁全盘三通 DN250×100”。验收时读回每条主材的名称、型号及数量，供询价使用。

原厂库无适用子目时，先切到预算书的“整个项目”，选中目标分部的直接子目。用 `quota-supplement` 传入 `--state`、`--runtime-root`、绑定返回的 `--inspector` 和 `--project-id`、逐级 `--parent-section`、唯一 `--command-id` 及 `--code`、`--name`、`--unit`、`--quantity`。编号用 `补` 加字母、数字或连字符。命令通过广联达原生动作打开表单，也兼容已经打开的空白表单。提交后核对目标分部、数量、价格以及其余预算行。加 `--check-only` 可打开、填写、校验并取消，回读确认预算一致。意外退出时先查 `command-status` 和原工程，待定补项需人工核对原回执。

短距离顶进时，先选中 `11-34` 并打开“标准换算”，用 `quota-short-drive-factor` 传入 `--state`、`--runtime-root`、`--inspector`、`--project-id`、逐级 `--parent-section`、`--quantity` 和唯一 `--command-id`。命令只执行已验证的内置规则。若已接受但需重新核验，用同一组参数加 `--verify-only`，从原回执继续验证。

需要切换分部时，可加 `--navigate-anchor`。同一顶层分部内优先使用有逐行证据的网格方向操作，无法建立该路径时使用原生定点选择。两种方式都要求读回同一预算模型和目标锚点后才开放插入。出现 `anchor_navigation_step_mismatch` 时，先用 `command-status`，再用原运行包和请求执行 `command-reconcile`。只有零业务派发且同进程全预算行一致时，才关闭原命令。已关闭的命令号保留，不能重发。

`runtime-prepare` 用于单独检查范围。`anchor_check` 记录目标、光标是否匹配和允许的导航方式。复用运行包时返回原预检结果。常规套项可直接用 `quota-run` 完成预检和写入。见[诊断与恢复](diagnostics.md)。

### 修改已有主材

先用绑定的 inspector 执行 native-inspect。在预算表中确认主材行路径、父定额编码、工程量及备注。
material-edit 接收 --state、--runtime-root、--project-id、--command-id、--path、--quota-code、
--quota-quantity、--quota-note、--material-code、--field name|content、--expected 和 --value。
可用 --quota-name、--quota-unit 同时核对父定额全名与单位。成功回执包含目标预算行路径。
选中父定额并显示工料机后，带 `--resource-content --field content` 可改工料机表的单单位含量。用该表的行路径、资源编码和原含量，CLI 核对数量由广联达重新计算。若单次写入已接受但回执待核，用 `resource-edit-reconcile` 根据原命令只读核对。
临时删除预算树中的主材可以传入 `quota-delete --kind 主 --parent-quota <完整父定额名称> --mode temporary`，用读回的删除线、工程量和合价确认结果。
可加 --check-only 检查目标和可编辑性。一次修改一格，随后读回。改名后先读回主材编码再继续，
因为广联达可能为独立主材分配新编码。命令不会保存工程。
