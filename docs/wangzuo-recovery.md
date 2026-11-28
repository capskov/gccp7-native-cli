# 西王佐分部、倍数单位及待核命令

## 分部

现版支持 section-rename、section-delete 和在已有一级、二级分部内套项。新建分部尚无实机验收通过的命令。现场已枚举 InsertTitle（插入分部）和 InsertSubTitle（插入子分部），创建锚点和写入结果仍需在测试工程验证。

## 数量与恢复

quota-run --quantity 和 quota-quantity --value 填白表自然量。quota-quantity --expected 是旧工程量表达式。旧底座实机验证了 10m2 和 100m2 折算，新版修正对应的离线校验。6-12 的 100m2 表达式填 88，计价量 0.88。输入 0.88 会再次除以 100。超过四位计价小数的面积、10m3 和 100m 写入会在预检阻断。

保留原 state、runtime.json、request.json、IPC 和同进程整树快照。确认同一条定额已正确修正后，用原请求核对：

~~~powershell
gccp command-status --state $state --command-id wzv9-rd-demolition-ac10-100m2
gccp native-inspect --runtime $inspector
gccp command-reconcile --state $state --runtime $originalRuntime --request $originalRequest --review-expression 88
~~~

新增分支要求同一会话、已确认的原插入、唯一原改量派发及完整预算证据。它只读核对工程，成功后记录核销。此分支尚未用西王佐原账本实机验证。若仍返回 EVIDENCE_INCOMPLETE 或 NATIVE_READBACK_CHANGED，保留原回执和当前工程。勿重发套项或直接改账本。读到 readback_verified、唯一 6-12、表达式 88、计价量 0.88、合价 359.41 元后，才用 project-save --check-only 预检，再用新命令号正式保存。

## 价格

单查 SQLite 用 gccp price-catalog --market $sqlite --text '闸阀'。原厂房建、京标价协需在广材助手选择月份下载，再用 price-catalog --period YYYY-MM 读取。载价预检见 [命令地图](agent-commands.md#人材机载价)。
