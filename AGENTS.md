# GCCP7 Native CLI

面向 AI Agent 的广联达 GCCP7 原生操作底座。目标软件为 Windows x64 上已验证版本的 GCCP7。

- Agent 使用者先读 [命令地图](docs/agent-commands.md)，再按需加载 [GCCP7 操作技能](.agents/skills/gccp7-native-cli/SKILL.md)。
- 安装和绑定见 [README](README.md)。需要核实失败或待定状态时，读 [诊断与恢复](docs/diagnostics.md)。
- 业务写入使用 `gccp` 的已验证范围、明确工程身份和唯一命令号。超时后用原命令和回执核对。版本、工程、候选或对话框不明确时停止写入。
- 维护代码时先运行 `python -m unittest discover -s tests/application`。实机写入只在用户授权的测试工程中进行。提交问题和示例时使用合成数据或脱敏证据。
