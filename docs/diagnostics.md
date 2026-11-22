# 只读诊断和命令恢复

运行 `gccp doctor` 核对工具链、系统与目标软件指纹。它不加载原生桥。绑定后可运行 `gccp native-inspect --runtime <inspector/runtime.json>` 读取模型。一个观察器只对应一个进程和工程，达到读取次数上限时用同一账本重新绑定。操作前检查真实工程和对话框。

超时或进程退出后，先执行 `gccp command-status --state <原目录> --command-id <原命令号>`，用只读预算核对后交操作者判断。若已有完整保存证据，套项可用 `command-reconcile` 核对原运行清单与原请求。已记录派发回执的临时删除或永久删除确认之后可用 `quota-delete-reconcile` 做只读验证。结果未决时不得换命令号重发。

若跨分部导航返回 `anchor_navigation_step_mismatch`，准备阶段只派发了网格导航。用原命令的 `runtime.json` 和 `request.json` 执行 `command-reconcile`。CLI 核对原生准备回执、两条业务动作状态、进程身份及完整预算模型。证据相符时，返回 `native_navigation_blocked_no_dispatch` 并释放工程占用，原命令仍保留。运行目录 `build` 的 `prepare-*.stage` 文件可以区分展开、网格移动及回读前后的边界。

若在候选选择返回 `candidate_model_not_unique` 或 `candidate_row_bound_rejected`，先查原命令回执。`command-reconcile` 对已验证的二级分部流程检查候选选择以前的原生回执、两条业务动作状态和完整预算。原查询面板仍在时，验证其身份与内容后关闭并读回。只有零业务派发且预算一致才返回 `native_candidate_blocked_no_dispatch`。

若二级分部流程超时且只留下首个 `inspect` 请求，可将原运行包、原请求和已经绑定的只读观察器传给 `command-reconcile --inspector <观察器>`。CLI 核对原命令尚无业务任务或写入意图，观察器与原进程一致且完整预算未变时返回 `native_inspection_timeout_no_dispatch`。遇到操作者正在保存，先等保存结束，再核对磁盘身份及按下文刷新原绑定。

若永久删除停在原生的单行确认框，`quota-delete-confirm` 只续接同一个已记录动作。若工程文件身份发生变化，只有在操作者核对当前文件及原确认框后才传入 `--reviewed-file-sha256`。程序还会核对文件 CRC、原命令、当前选区和对话框。未识别的提示保持原样。应用重启后需要重新核实进程和工程身份。

如果已打开的工程仍在原进程，而 GPB7 在磁盘被替换，可用原账本和观察器调用 `gccp bind --refresh-from-inspector <原观察器> --expected-sha256 <当前文件指纹>`，同时传入常规绑定参数。命令校验文件 CRC、原进程和窗口、最新业务回执以及当前完整预算树。预算有变、存在未决动作或有待确认的保存时会停止。刷新只更新绑定并生成观察器。

操作者更新单价并保存后，可在预算书“整个项目”视图加 `--accept-price-updates`。命令核对同一预算结构、子目、数量和换算标记，只允许金额字段变化。保留 `changed_price_rows`、新 `inspector` 及原账本。

补充子目提交超时后，用 `command-status` 和当前只读预算核对原编号、分部、数量及对话框。当前这类待定结果需核对原回执，`command-reconcile` 的套项恢复流程与补充子目使用不同的证据。

对于已验证的 m³ 工程量显示精度，CLI 保留输入表达式，并按两位小数核对工程量显示。旧运行包如果在填量后因 `normal_up_context_guard` 停止，可用原命令调用 `command-reconcile`。该命令核对一次派发的原回执、原始表达式、显示值及完整预算差异，然后登记预算读回范围。此类恢复回执的 `resource_readback_verified` 为 `false`，原命令不会重发。

## 保存耗时较长时

常规套项在读回后结束，保存由操作者或广联达安排。若广联达正在保存，等操作者确认后再核对工程文件身份。

原生保存只排队一次。执行器默认最多等待五分钟，可用 `--save-timeout` 按工程调整，每四秒检查一次磁盘文件。文件内容有变化、连续两次摘要相同且 CRC 校验通过后，才向广联达发出预算及工料机读回，再复核一次摘要。等待磁盘期间没有新的广联达请求。任一步无法确认时，保留原保存记录和命令号供核对。传 `--no-save` 时直接在读回后结束。

`DELETE_PREFLIGHT_TIMEOUT_NO_DISPATCH` 表示尚未登记删除意图，亦未派发。先等软件恢复响应，再核对当前工程文件身份及预算。
`native_delete_no_effect_verified` 表示已核对删除后的目标及整表，均与原始快照一致。按新操作处理时先重新定位，已登记的原命令号只返回原回执。
`native_delete_blocked_no_dispatch` 表示 `quota-delete-reconcile` 已核对原生选区保护回执与本次完整预算树，确认删除动作未排队。
