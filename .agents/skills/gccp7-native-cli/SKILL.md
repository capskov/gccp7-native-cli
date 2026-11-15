---
name: gccp7-native-cli
description: 当用户说“用 CLI 操作”“原生桥”“中间桥”，或要求在当前广联达 GCCP7 概算中查定额、套定额、填量、补项、删项时，使用 Windows 本机 gccp 命令执行和核对。Use for Glodon GCCP7 estimate operations through its native CLI.
---

# GCCP7 原生桥 CLI

在 Windows PowerShell 调用 `gccp`。若同级 `references/local-installation.md` 存在，优先用其中的本机可执行文件及工具链配置。运行 `gccp --version`，用各子命令的 `--help` 核实当前版本支持的参数。具体示例读命令地图和 README。独立安装时它们在本技能的 `references/docs/agent-commands.md` 和 `references/README.md`，仓库内见[命令地图](../../../docs/agent-commands.md)和[快速开始](../../../README.md#安装和绑定)。

1. **确认工程。** 用 `doctor`、`bind` 连接用户指定且已打开的工程，复用原 `--state`，保留 `project_id` 和 `inspector`。用 `native-inspect` 确认预算树、父项和已有数量。用户授权当前工程时就在该工程操作。
2. **查定额。** 用 `catalog search` 查本机安装的北京 2025 原厂索引，按编号、完整名称和专业选 `vendor_id`。用 `quota-candidates --vendor-id` 核对现场单位。索引缺项时可用 `quota-candidates --code` 查现场精确候选。
3. **套项并填量。** 用一条 `quota-run --vendor-id … --expect-unit … --quantity … --command-id …`，带同一工程的 `--state`、`--runtime-root`、`--inspector`、`--project-id`。顶层传 `--parent-id`，按默认工作流加 `--no-save`。二级分部按真实名称逐级传 `--parent-section`，加 `--selected-anchor`，该模式自动在读回后结束。同一顶层内需切换子分部时可用 `--navigate-anchor`。先检查同一用途是否已套；确有独立计量用途时才用 `--item-label`。
4. **按业务选择已有命令。** 零价补项用 `quota-supplement`，在“整个项目”中选中分部内的直接子目，首次可用 `--check-only` 验证表单。单条删除用 `quota-delete`，核对完整身份和删除模式。特殊换算或调价后刷新绑定查命令地图。套项只填写新插入行的工程量，存量行独立改量、分部增删和批量刷价仍需开发。
5. **读回和收尾。** 核对回执里的分部、编号、单位、数量和工料机。默认由操作者或广联达自身负责保存，CLI 回执只证明其实际观察到的结果。遇到超时、退出或意外对话框，停止继续写入，保留原账本和命令号，用 `command-status` 和只读快照核对，把结果和疑点交给操作者判断。

已有预算核查或同一定额有多种主材时，可加载同目录的 `gccp7-quota-reuse` 技能，用 `quota-clone` 整行复制、向下粘贴、改主材名称并填量。只读核查用 `quota-reuse-plan`。

业务动作优先使用上述 CLI。界面仅用来确认预算视图、定位选区、观察弹窗、取证或验证新增的原生动作。缺命令或遇到故障时，先核对 `--help` 和诊断文档，再补 CLI 入口或如实标明缺口。

修改已有主材的名称或含量时，参照命令地图使用 `material-edit`。先读回预算行和父定额身份，可用 `--check-only` 确认字段可编辑。
