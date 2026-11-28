# 建立一级、二级预算分部并套定额

适用于 CLI v0.3.1。预算分部是预算书里的“部”行。广联达原厂有插入分部和插入子分部，现版 CLI 支持在已有分部套项、改分部名及删除空分部。

## 在广联达内建分部

1. 进入目标单位工程的 **预算书 → 整个项目**，确认左侧单位工程正确。
2. **一级分部**：从预算书右键菜单或上方分部菜单选 **插入分部**。原厂动作名是 InsertTitle，也可用 Ctrl+B。创建后填写名称，确认新行直接属于“整个项目”。例如“道路破除”“道路恢复”“管道保护”“河道恢复”可以各设为一级。
3. **二级分部**：选中要作为父项的一级分部标题，在同一菜单选 **插入子分部**（InsertSubTitle）。填写名称后，确认新行缩进在该父项下。例如“道路破除 → 拆除子项”。在二级下套定额时，须把两级实际名称都传给 CLI。
4. 如菜单未启用，先核对当前视图、项目选区和是否有弹窗。以当前广联达的整棵预算树确认落点。只有落点符合预期才继续套项。

操作后，用原绑定的观察器检查：

~~~powershell
& $gccp native-inspect --runtime $inspector
~~~

在“工程量表达式”表中核对新行为“部”、名称准确、同一父项下名称唯一。一级的 depth 应为 1，二级为 2。根据 row_path_session_only 检查二级父路径及同级位置。已有绑定且文件身份未变时可继续使用，若遇到 WORKCOPY_FILE_REPLACED，按 [恢复说明](diagnostics.md)核对原账本和文件，不要另建账本跳过待核命令。

## 用 CLI 套进分部

先核对广联达当前库中的真实定额候选。下面以 6-12 为例，编号和专业最终以设计内容及现场候选为准。

~~~powershell
$base = @('--state', $state, '--runtime-root', $runtimeRoot, '--project-id', $projectId)
& $gccp quota-candidates @base --code '6-12'
~~~

在目标分部选中最后一个直接定额行，或实际存在的空白定额尾行。先准备运行，核对父路径、定额编号、完整名称、专业、单位和用量。已有独立计价用途时用备注区分。下列变量用现场值。

~~~powershell
$target = @('--parent-section', '道路破除', '--parent-section', '拆除子项')
$quota = @('--candidate-code', '6-12', '--candidate-name', $candidateName,
           '--candidate-unit', $candidateUnit, '--candidate-specialty', $candidateSpecialty)
$run = @('--inspector', $inspector, '--quantity', '88', '--command-id', $newCommandId,
         '--selected-anchor', '--no-save')
& $gccp runtime-prepare @base @target @quota @run
& $gccp quota-run @base @target @quota @run
~~~

一级直接套项时只传一级名称。只有预检成功、现场尚无该用途的定额，才执行第二条。88 平方米对应 100m2 定额，表达式仍传 88，计价量应为 0.88。

完全空白的分部若没有可识别的直接定额行，v0.3.1 会报 PARENT_SHAPE_NOT_REVIEWED。此时可让操作者在广联达中录入第一条经过核对的定额作为锚点，再由 CLI 从其余条目继续。若任务明确要求所有业务写入走 CLI，则报告这个缺口，等分部和首条锚点已就绪后继续。若已有原命令待核，先按 [西王佐恢复说明](wangzuo-recovery.md)处理。

每条套项后回读分部、编号、表达式、计价量及合价。确认原账本无待核命令后，再用新的保存命令号执行 project-save --check-only 和正式保存。更多参数见 [命令参考](command-reference.md)。
