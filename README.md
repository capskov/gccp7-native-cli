# 广联达 GCCP7 原生桥 CLI

**面向 AI Agent 的广联达 GCCP7 原生操作底座。** 面向工程造价与概算计价任务，Agent 可以用结构化命令定位定额、套项、修改工程量、删除和读回验证。CLI 在 Windows 本机通过版本锁定的 Qt 桥调用 GCCP 自己的业务动作，保存命令意图、回执和审计证据。

**GCCP7 Native CLI** is an agent-oriented native command-line foundation for Glodon GCCP7 construction cost estimation. This independent developer preview supports scoped quota lookup, insertion, quantity updates, deletion and audited readback.

- **Agent 入口**：[命令地图](docs/agent-commands.md) · [主技能](.agents/skills/gccp7-native-cli/SKILL.md) · [llms.txt](llms.txt)
- **首版**：`v0.1.0-alpha.1` 开发者预览 · [变更记录](CHANGELOG.md) · [诊断与恢复](docs/diagnostics.md)

| 入口 | 当前范围 |
| --- | --- |
| `gccp doctor` / `gccp bind` | 检查工具链和六个组件指纹，绑定已经打开的测试工程，构建同进程只读观察器 |
| `gccp catalog search` | 只读检索自备 SQLite 定额索引，按编号、规格或来源核对候选 |
| `gccp quota-run` | 北京2025管网球墨铸铁管件 3-193～3-208 家族。定位、套入、改量、原生保存及同进程回读 |
| `gccp quota-delete` | 精确定位单条定额，临时删除或永久删除，识别原生单行确认框 |
| `gccp native-inspect` / `command-status` | 同进程只读快照、审计状态及失败定位 |

原生业务已在 GCCP7 x64、工程格式版本 7.5000.2.4、Qt 5.9.9 和北京2025概算环境中验证过代表场景。公开版另行验证了安装、离线契约和原生编译。新工作站上请用自己的已获授权的测试工程做首次实机验证。批量、全定额覆盖、通用调价、撤销临时删除和保存后自动关闭重开仍属后续范围。项目与广联达官方无隶属关系。

## 安装和绑定

需要 Windows x64、Python 3.12+、已合法安装且与配置指纹一致的 GCCP7、MSVC x64 工具链、Windows SDK、Qt 5.9.9 x64 MSVC SDK。软件、SDK、定额数据和工程文件由使用者自行准备。用 PowerShell 在源码目录运行：

```powershell
py -3.12 -m venv .venv
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
$binding.data.parents
```

绑定只对选定的已打开工程建立只读观察器。业务调用复用同一 `state` 目录。进程重建后重新绑定，未决命令先核对旧回执。若应用另存或后台保存改变了文件身份，先保留原账本和当前工程，按[诊断说明](docs/diagnostics.md)处理。

## 单条业务

参照 [SQLite 字段](examples/catalog-schema.sql)准备本地定额索引。候选条目仍以软件中的实际定额为准。

```powershell
& $gccp catalog search --db .\catalog.sqlite --code 3-201 --specialty 管网 --spec 700
# 将示例父项、条目 ID 和命令号改成你的绑定及检索结果。
& $gccp quota-run --db .\catalog.sqlite --state .\state --runtime-root .\runtime `
  --inspector $binding.data.inspector --project-id $binding.data.project_id `
  --parent-id 'TEST-PARENT' --entry-id 1 --quantity 2 --command-id 'demo-add-001'
```

`quota-run` 会触发原生保存，目标父项需是唯一、非空的顶层分部。插入后的零工程量、零材料价是正常值。传给改量步骤的目标工程量须为正数且最多四位小数。定额的名称和单位以回读为准。

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

Codex 在此仓库中可发现 `.agents/skills/gccp7-native-cli`。在 Codex 对话中用 `$gccp7-native-cli` 明确调用。其他支持 Agent Skills 的工具可加载同一目录，按其技能安装规则注册。

可以把仓库链接和这条任务交给 Agent：

> 使用 $gccp7-native-cli，在 Windows 本机先核对当前工程和匹配版本，只读检查北京2025管网定额 3-201（DN700）。返回候选身份、目标父项及计划使用的命令。等我确认工程和工程量后再执行套项。

`AGENTS.md` 给仓库中的 Agent 提供导航，`llms.txt` 提供适合抓取的短索引。命令地图说明副作用、回执和异常恢复。技能使用本机 `gccp` CLI，运行前需要完成安装和绑定。

## 参与开发

从 [架构和验证记录](docs/architecture.md)了解执行路径。Agent 可使用[主技能](.agents/skills/gccp7-native-cli/SKILL.md)。运行 `python -m unittest discover -s tests/application` 可做离线回归，测试不打开或写入 GCCP。欢迎提交可复现问题、修正和 PR。请提交合成样例与脱敏回执。

本仓库按 **GPL-2.0-or-later** 发布。参见 [LICENSE](LICENSE) 和 [来源与依赖](THIRD-PARTY-NOTICES.md)。
