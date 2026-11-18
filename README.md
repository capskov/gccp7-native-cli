# 广联达 GCCP7 原生桥 CLI

**面向 AI Agent 的广联达 GCCP7 原生操作底座。** Agent 可以用结构化命令查定额、套入并填写新行的工程量、补项、删除和读回验证。CLI 在 Windows 本机通过版本锁定的 Qt 桥调用 GCCP 自己的业务动作，并记录回执。

**GCCP7 Native CLI** provides native quota lookup, insertion with initial quantity, supplemental items, deletion and audited readback for Glodon GCCP7.

- **Agent 入口**：[命令地图](docs/agent-commands.md) · [主技能](.agents/skills/gccp7-native-cli/SKILL.md) · [llms.txt](llms.txt)
- **当前版本** `0.2.0b6`（限定环境试用版） · [实机验证与发布范围](docs/architecture.md) · [变更记录](CHANGELOG.md) · [诊断与恢复](docs/diagnostics.md)

| 入口 | 当前范围 |
| --- | --- |
| `gccp doctor` / `gccp bind` | 检查工具链和六个组件指纹，绑定已经打开的测试工程，构建同进程只读观察器 |
| `gccp catalog search` | 只读检索随包的北京 2025 原厂索引，支持编号、名称、专业和原厂 ID |
| `gccp quota-candidates` | 从当前北京 2025 库按编号或原厂 ID 读取软件候选 |
| `gccp quota-run` | 精确套入、填写新增子目工程量并读回。二级分部按路径执行，自动在读回后结束 |
| `gccp quota-clone` | 复用已套的普通定额，复制一次并逐条向下追加，改零价主材名称、填量并读回 |
| `gccp quota-supplement` | 打开原生补充子目表单，提交零价工作量并回读，或用 `--check-only` 校验后取消 |
| `gccp quota-short-drive-factor` | 对已选北京 2025 管网 `11-34` 应用短距离标准换算并回读 |
| `gccp quota-delete` | 精确定位单条定额，临时删除或永久删除，识别原生单行确认框 |
| `gccp native-inspect` / `command-status` | 同进程只读快照、审计状态及失败定位 |

原生业务已在一套 GCCP7 x64、工程格式 7.5000.2.4、Qt 5.9.9 和北京 2025 概算环境中实机验证。使用前以 `doctor` 检查匹配的安装路径和组件指纹。当前主流程覆盖既有分部的查项、套入并填量、补项、指定换算、单行删除和回读。已有行独立改量、分部增删改、批量调价、通用工料机换算和保存后重开属于待扩展能力。项目独立开发。

## 安装和绑定

实机操作需要 Windows x64、Python 3.12、已安装且与配置指纹一致的 GCCP7、MSVC x64 工具链、Windows SDK、Qt 5.9.9 x64 MSVC SDK。安装包声明支持 Python 3.12 及以上，本次独立安装验证使用 3.12。北京 2025 原厂子目索引随 CLI 安装，可独立离线检索。用 PowerShell 建立独立环境，从源码安装时运行 `pip install .`。拿到 wheel 时，将该行的 `.` 换成 wheel 的实际路径。

```powershell
python -m venv .venv
$py = Join-Path $PWD '.venv\Scripts\python.exe'
& $py -m pip install .
$gccp = Join-Path $PWD '.venv\Scripts\gccp.exe'
$env:GCCP7_MSVC_ROOT = 'C:\path\to\MSVC\14.44.35207'
$env:GCCP7_WINDOWS_SDK_ROOT = 'C:\Program Files (x86)\Windows Kits\10'
$env:GCCP7_WINDOWS_SDK_VERSION = '10.0.26100.0'
$env:GCCP7_QT_ROOT = 'C:\path\to\Qt\5.9.9\msvc2015_64'
& $gccp doctor
```

在广联达中手工打开你准备的测试工程，进入预算表页面。确认当前文件路径和摘要，建立本地绑定：

```powershell
$workcopy = 'C:\work\test-estimate.GPB7'
$sha = (Get-FileHash -LiteralPath $workcopy -Algorithm SHA256).Hash.ToLowerInvariant()
$binding = (& $gccp bind --state .\state --runtime-root .\runtime --workcopy $workcopy --expected-sha256 $sha) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw $binding.error.message }
$binding.data | Select-Object project_id, inspector, parents
& $gccp native-inspect --runtime $binding.data.inspector
```

绑定只对选定的已打开工程建立只读观察器。业务调用复用同一 `state` 目录。进程重建后重新绑定，未决命令先核对旧回执。若应用另存或后台保存改变了文件身份，先保留原账本和当前工程，按[诊断说明](docs/diagnostics.md)处理。

`parents` 只列有业务 ID 的顶层分部，可能为空。二级分部的完整名称从预算树读回，套项时从顶层开始逐级传入 `--parent-section`。

## 单条业务

遇到多种构件适用同一定额时，可先核对既有预算，再用 [定额复用](docs/quota-reuse.md) 批量追加主材变体。是否复用以专业、定额工作内容和计量单位为准。

### 用广联达现场候选套项

按编号读取软件当前库的结果。根据业务选定候选，把四项原样传给执行器。示例值需替换为实际回读值。

```powershell
& $gccp quota-candidates --state ./state --runtime-root ./runtime --project-id $binding.data.project_id --code '目标编号'
& $gccp quota-run --state ./state --runtime-root ./runtime --inspector $binding.data.inspector --project-id $binding.data.project_id --parent-id '实际父项ID' --no-save --quantity 2 --command-id 'live-add-001' --candidate-code '实际编号' --candidate-name '候选完整名称' --candidate-unit '候选单位' --candidate-specialty '候选专业'
```

候选来自广联达现场。套入前会再次核对当前库和唯一精确候选。遇到身份歧义、未知对话框或读回失败，程序阻断或保留原命令待核。北京 2025 管网、市政通用和轨道土建已有代表子目实机回读，具体范围见[实机验证记录](docs/architecture.md)。

### 用随包的原厂索引套项

先查原厂子目 ID，再用它套项。CLI 会核对现场唯一匹配的定额和实际计量单位。索引更新见[定额库工作流程](docs/catalog-workflow.md)。

```powershell
& $gccp catalog search --code 4-102 --specialty 轨道
# 在“整个项目”视图选中目标分部末尾的空白行或上一条定额。
# 将分部名、原厂 ID、数量、单位和命令号换成实际值。
& $gccp quota-run --state .\state --runtime-root .\runtime `
  --inspector $binding.data.inspector --project-id $binding.data.project_id `
  --parent-section '一级分部名称' --parent-section '二级分部名称' `
  --selected-anchor --vendor-id 10413 --expect-unit t --quantity 2 --command-id 'demo-add-001'
```

常规套项核对回执后，由操作者或广联达自身安排保存。二级分部路径自动采用 `--no-save`，旧命令仍可显式传入。使用 `--parent-id` 时显式加 `--no-save` 采用同一流程，省略时保留原有保存行为。目标工程量须为正数，最多四位小数。定额名称和单位以现场回读为准。

### 补充子目

在“整个项目”视图选中目标分部内的一条直接子目。第一次先用校验模式试通表单。确认工程、名称和数量后，正式调用去掉 `--check-only`。

```powershell
& $gccp quota-supplement --state .\state --runtime-root .\runtime `
  --inspector $binding.data.inspector --project-id $binding.data.project_id `
  --parent-section '一级分部名称' --parent-section '二级分部名称' `
  --code '补DEMO-01' --name '经核定的补充子目名称' --unit 套 --quantity 2 `
  --command-id 'demo-supplement-001' --check-only
```

正式调用用广联达原生动作打开表单、填入零价工作量并回读预算，由操作者安排保存。其他业务和回执见[命令地图](docs/agent-commands.md)。

### 单条删除

删除时输入精确父项、编号、名称和单位。示例用 `temporary`，永久删除用 `permanent`。删除命令返回同进程回读，工程保存由操作者安排。

```powershell
& $gccp quota-delete --state .\state --runtime-root .\runtime `
  --project-id $binding.data.project_id --parent-id 'TEST-PARENT' `
  --code 3-201 --name '实际回读中的完整名称' --unit 个 --mode temporary --command-id 'demo-del-001'
```

同一命令号再次提交相同参数只取回执。超时或返回未知时先查原命令号，不要用新命令号重做：

```powershell
& $gccp command-status --state .\state --command-id 'demo-add-001'
```

## 给智能体使用

Codex 在此仓库中可发现 `.agents/skills` 内的主技能和定额复用技能。跨项目使用时，将发布包中的两个技能目录解压到 Codex 用户技能目录，通常是 `%USERPROFILE%\.codex\skills`。技能和 Python wheel 分别安装。新对话可直接说“用 CLI 操作当前广联达概算”，也可输入 `$gccp7-quota-reuse` 指定复用流程。

可以把仓库链接和这条任务交给 Agent：

> 使用 $gccp7-native-cli，在 Windows 本机先核对当前工程和匹配版本，只读检查北京2025管网定额 3-201（DN700）。返回候选身份、目标父项及计划使用的命令。等我确认工程和工程量后再执行套项。

`AGENTS.md` 给仓库中的 Agent 提供导航，`llms.txt` 提供适合抓取的短索引。命令地图说明副作用、回执和异常恢复。技能使用本机 `gccp` CLI，运行前需要完成安装和绑定。

## 参与开发

从 [架构和验证记录](docs/architecture.md)了解执行路径。Agent 可使用[主技能](.agents/skills/gccp7-native-cli/SKILL.md)。运行 `python -m unittest discover -s tests/application` 可做离线回归，测试不打开或写入 GCCP。欢迎提交可复现问题、修正和 PR。请提交合成样例与脱敏回执。

本仓库按 **GPL-2.0-or-later** 发布。参见 [LICENSE](LICENSE) 和 [来源与依赖](THIRD-PARTY-NOTICES.md)。
