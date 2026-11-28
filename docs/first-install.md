# 新电脑安装与验收

从 [GitHub Releases](https://github.com/capskov/gccp7-native-cli/releases/latest) 下载 Windows Build ZIP 和 SHA256SUMS.txt，核对哈希后解压。ZIP 含 CLI、独立查价助手、两个 Codex 技能和参考文档。安装需要 Windows x64、Python 3.12 x64、已激活的广联达 GCCP7 和 C++ 构建工具。

## 配置

1. 启动广联达，确认自己的概算文件能正常打开。已实机验证的版本为 GCCP7 7.5000.2.4、Qt 5.9.9、北京 2025 概算。不同安装路径可传 -GccpRoot 为包含 Bin 和 Construction 的目录。CLI 仍核验六个组件指纹。
2. 缺 Python 或工具链时，在管理员 PowerShell 执行下列命令。也可先在 Visual Studio Installer 选择 C++ 桌面生成工具、MSVC x64 和 Windows 11 SDK。

~~~powershell
winget install --id Python.Python.3.12 --exact
winget install --id Microsoft.VisualStudio.2022.BuildTools --exact --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 --add Microsoft.VisualStudio.Component.Windows11SDK.26100"
~~~

3. 在 ZIP 解压目录打开 PowerShell 并运行安装脚本。已有 Qt 5.9.9 MSVC 2015 x64 SDK 时可传 -QtRoot，省略 -DownloadQt。便携工具链可传 -MsvcRoot、-WindowsSdkRoot 和 -WindowsSdkVersion。

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup-windows.ps1 -DownloadQt
~~~

另开 PowerShell 和 Codex 新对话，让它们读取已安装的技能和用户工具链配置。

~~~powershell
$gccp = Join-Path $env:LOCALAPPDATA 'GCCP7-Native-CLI/venv/Scripts/gccp.exe'
& $gccp --version
& $gccp doctor
~~~

## 首次实机验收

1. 打开测试工程并进入预算书。按 [README](../README.md#安装和绑定) 绑定文件，保留固定 state 和 runtime，保存返回的 project_id 和 inspector。
2. 先用 native-inspect 完整回读，再用 quota-candidates 核实实际候选。用 runtime-prepare 验证目标分部和定额。成功后对单条套项核对名称、表达式、计价量、合价和工料机。
3. 预检失败见 [诊断与恢复](diagnostics.md)。按原命令号处理未决回执。另见 [逐条命令参数](command-reference.md) 和 [西王佐恢复](wangzuo-recovery.md)。

独立查价可打开 ZIP 中的 price-assistant/GCCP7-Price-Assistant.exe，选择自己的市场价 SQLite。工程、报价库和现场账本由使用者在本机保存。
