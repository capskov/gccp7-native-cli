---
name: gccp7-native-cli
description: 使用广联达 GCCP7 原生桥 CLI 在已授权的测试概算工程中查定额、套项、改量、临时或永久删除，并核对工料机联动和命令回执。Use for Glodon GCCP7 estimate automation through gccp.
---

# GCCP7 原生桥 CLI

- 在本机 Windows 的 shell 中调用已安装的 `gccp`。先读[命令地图](https://github.com/capskov/gccp7-native-cli/blob/main/docs/agent-commands.md)，缺安装或绑定时再读[快速开始](https://github.com/capskov/gccp7-native-cli#安装和绑定)。
- 先核对操作者指定的测试工程、版本和原账本。首次连接用 `doctor`、`bind`，保留回传的 `project_id`、`inspector` 及真实父项。用 `native-inspect` 或 `catalog search` 获取证据，按库、专业、编号、规格和单位确认定额身份。
- 写入前核对用户授权和作用范围。`quota-run` 会套入、改量并保存。删除用 `quota-delete`，带完整父项、定额身份和明确的 `temporary` 或 `permanent` 模式。新套定额的默认零量和零材料价是正常值。
- 同一业务始终使用相同的 `command_id`。遇到超时、未知状态或意外对话框，保留原账本并用 `command-status` 和只读快照核对；按[诊断说明](https://github.com/capskov/gccp7-native-cli/blob/main/docs/diagnostics.md)续接。对原命令之外的写入需要单独确认。
