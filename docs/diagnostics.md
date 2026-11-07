# 只读诊断和命令恢复

运行 `gccp doctor` 核对工具链、系统与目标软件指纹。它不加载原生桥。绑定后可运行 `gccp native-inspect --runtime <inspector/runtime.json>` 读取模型。一个观察器只对应一个进程和工程，达到读取次数上限时用同一账本重新绑定。操作前检查真实工程和对话框。

超时或进程退出后，先执行 `gccp command-status --state <原目录> --command-id <原命令号>`。若已有完整保存证据，套项可用 `command-reconcile` 核对原运行清单与原请求。永久删除确认之后可用 `quota-delete-reconcile` 做只读验证。结果未决时不得换命令号重发。

若永久删除停在原生的单行确认框，`quota-delete-confirm` 只续接同一个已记录动作。若工程文件身份发生变化，只有在操作者核对当前文件及原确认框后才传入 `--reviewed-file-sha256`。程序还会核对文件 CRC、原命令、当前选区和对话框。未识别的提示保持原样。应用重启后需要重新核实进程和工程身份。
