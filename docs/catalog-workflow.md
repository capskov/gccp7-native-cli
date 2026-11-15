# 广联达原生桥与北京 2025 定额库

## 从图纸到现场定额

安装时自动带入北京 2025 概算子目索引，共 12,244 条，默认离线查项。如需直接使用已安装的库，传入 --library-root。

```powershell
& $gccp catalog inspect
& $gccp catalog search --code '4-102' --specialty '轨道'
& $gccp catalog search --text '钢筋格栅 竖井' --specialty '轨道'
```

搜索返回原厂子目 ID、编码、完整名称、专业和分类 ID。索引不含计量单位和消耗量。单位以软件现场回读为准，工程量规则、工作内容和系数查正式定额文件。输入资料保留设计量、暂估量和待核项，工程量按现场定额单位换算。

## 套项与读回

1. `gccp doctor` 检查本机版本。对当前已打开、经授权的工程运行 `gccp bind`，登记当前磁盘文件的 SHA-256，记录状态目录、工程 ID 和观察器路径。
2. `gccp native-inspect --runtime <观察器>` 读取预算树。确认目标父项、已有定额及数量，避免重复套项。
3. 用 `gccp catalog search` 按编号、名称及专业找候选。多个专业出现相同编号时，比较专业和完整名称。需要确认现场单位时，用 `quota-candidates --vendor-id` 读取匹配候选。
4. 从原厂索引取得 `vendor_id` 后，用 `quota-run --vendor-id` 一次完成现场查项、校验和套入。命令要求现场库名、编码、完整名称及专业匹配唯一，读取现场单位。图纸计量单位明确时加 `--expect-unit`，单位不同会在派发前停止。

```powershell
& $gccp quota-candidates --state $state --runtime-root $runtime `
  --project-id $projectId --vendor-id 10411
```

原厂 ID 套项示例

```powershell
& $gccp quota-run --state $state --runtime-root $runtime --inspector $inspector `
  --project-id $projectId --parent-section '顶管工程（D2000，50 m）' `
  --parent-section '02 接收井（H=7.5 m）' --selected-anchor --navigate-anchor `
  --vendor-id 10411 --expect-unit t --quantity 11.325 `
  --command-id 'rev14-receiving-rebar-lattice-4-100-20261108'
```

原厂 ID 随来源库管理，回执保留子目身份和来源文件指纹。相同参数重跑同一命令号只读原回执。历史操作可用 `command-status` 查询原账本。显式 `--db` 为自备数据库的旧脚本保留兼容，默认流程只使用原厂导出。

`quota-run` 使用完整候选、正数工程量和唯一命令号。顶层分部用业务 ID 并加 `--no-save`，二级分部用完整名称路径和 `--selected-anchor`，需要切换子分部时加 `--navigate-anchor`。回执 `native_insert_quantity_readback_verified_not_saved` 且 `save_dispatches=0` 表示 CLI 完成读回，保存由操作者或广联达安排。见[命令地图](agent-commands.md)。
