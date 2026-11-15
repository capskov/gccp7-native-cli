# 复用已有定额

已有预算先核查。同一份概算中，多种构件若经核定适用同一专业、同一定额、同一施工方式和计量单位，可以先套一条，再复用整行。DN500 的三通、弯头等应按实际定额适用范围分组。闸门或其他安装对象需单独核定定额。

## 原生复制并填量

在“整个项目”预算表选中源定额父行，打开“工料机显示”。使用已有工程绑定。新名称应是经核定的实际材料名称，且每个名称在本批及目标分部内唯一。

```json
[
  {"material_name": "DN500 球墨铸铁三通", "quantity": "1"},
  {"material_name": "DN500 球墨铸铁弯头", "quantity": "2"}
]
```

将上面的材料清单按实际情况修改并存为 UTF-8 的 `variants.json`。

```powershell
& $gccp quota-clone --state $state --runtime-root $runtime --inspector $inspector `
  --project-id $projectId --parent-section '实际一级分部名称' `
  --code '3-199' --quota-name '现场回读的定额完整名称' --unit '个' `
  --material-code '现场拟更名主材的实际编码' --variants .\variants.json `
  --command-id 'pipe-fitting-clone-001'
```

嵌套分部从顶层开始逐级传 `--parent-section`。单条可用 `--material-name` 和 `--quantity` 代替 `--variants`。命令调用预算表原生「复制」和普通「粘贴」，保留子目下的主材，依次追加在同一分部的上一行下面。每批一次复制，最多 5 条。已定价或锁定数量的待更名主材需要单独处理。规格、单位、含量及其他材料沿用源定额，原厂材料库选材替换属于独立操作。广联达可能为改名主材重新编码，读回以新名称、编码、所属定额及数量为准。

写入回执中的 `clones` 记录逐条成功读回的结果，`save_dispatches=0` 表示 CLI 未派发保存。部分完成时使用同一命令号查询，人工判断待定步骤。

## 仅核查

`quota-reuse-plan --capture 预算.json --request 计划.json` 只读取 `native-inspect` 输出和准备好的材料计划。适合整理已有预算中可能需要保留、改量或复制的行。输出中的 `review_existing_rows` 提示应先确认同码行的主材和数量。

```json
{
  "quota": {"code": "3-199", "name": "现场回读的定额完整名称", "unit": "个"},
  "source_material_code": "现场主材编码",
  "variants": [{
    "parent_sections": ["实际一级分部名称"],
    "material_code": "已核定的材料编码",
    "material_name": "已核定的材料名称",
    "material_spec": "",
    "material_unit": "个",
    "quantity": "2"
  }]
}
```
