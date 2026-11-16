# 可选小工具

Python 3.11+。`python -m pip install -r requirements.txt` 安装工具依赖。只阅读规则和使用母版无需 Python。以下命令在仓库或下载包根执行，输入、输出路径按本批资料填写。可以先运行 `python tools/public_check.py --release` 检查下载内容。

## 读取、复算与定位

- `python tools/excel_snapshot.py 输入.xlsx --output 新结果.json`：读取原值、公式、缓存与隐藏结构，不执行公式。
- `python tools/decimal_recalc.py 输入.json --output 新结果.json`：Decimal 独立复算，数值用十进制字符串，未知用 null。示例见 `examples/recalc.json`；基数、比例和取位来源于当前依据。
- `python tools/unzip_gbk.py 原件.zip 新目录`：中文文件名解压；来源副本另存。
- PDF 定位、图面差异、Office 整包差异、证据索引用 `pdf_locator.py`、`pdf_inspect.py`、`redline_compare.py`、`office_package_diff.py`、`evidence_index.py` 的 `--help`。这些工具辅助定位和结构检查，图面判断须回看原图。
- `python tools/output_mapping_check.py --help`：审核表和来源映射核对。
- 土地表在明细 BO 填行政村、BP 填地类，扩行后执行 `python tools/land_summary.py 输入副本.xlsx --output 新表.xlsx --detail-end-row 实际明细末行 --fill-summary`。明细起行为6；合计与打印区外说明不纳入范围。工具按村和地类重建汇总并保留未确认金额的空白；不会覆盖输入或已有输出。审核数量、标准、取位仍须按本批依据确认。

## 逐项检查台账（可选）

`python tools/ledger_check.py catalog --module land --mode FULL --stage INITIAL --full` 查看具体检查动作。`init --objects 对象清单.csv --project 本次项目 --output 新台账.json` 初始化；`check 台账.json --verify-sources --output 新报告.json` 校验来源哈希与记录约束。

保留 schema_version=1.0、检查 ID 与既有契约，字段和状态见 [台账契约](ledger-contract.md)，机器约束见 `contracts/land_audit.schema.json`。PASS/FINDING/NA 是检查记录状态；COMPLETE_RECORDS 只表示记录完整，business_verified 保持 false。专项范围不确认采用金额。原始文件覆盖分母需另行核对。

## 交接与安全发布

运行清单字段：`package`、`batches`；每批 `id/name/info/inputs/outputs`，outputs 含 audit/materials/opinion 的绝对路径。materials 指向本批目录的 `征地拆迁资料符合性核查表（改）.xlsx`。运行 JSON 路径全部使用实际绝对路径，模板和示例中的相对路径只用于说明。

```
python tools/workflow_state.py init --run-dir 运行目录 --input 运行清单.json
python tools/workflow_state.py status --run-dir 运行目录
python tools/workflow_state.py record --run-dir 运行目录 --input 交接事件.json
python tools/workflow_state.py guard --run-dir 运行目录 --batch 批次ID --file 登记成果
python tools/workflow_state.py guard --run-dir 运行目录 --batch 批次ID --role writer --file 登记成果 --candidate 候选稿
```

角色 prepare/gemini/table/writer/verify/review/final；状态 pending/running/waiting_input/waiting_user/done/failed/canceled。先登记 running 和真实 task_id，再发布或登记 done；等待时保留 task_id。同角色全轮只用一个活跃 ID。

done 需要 artifacts 的实际文件，pending 为空；table 覆盖两张表，writer 覆盖意见。`record` 不能认领历史稿。guard 查询不写入，给 candidate 才发布：检查占用与哈希，备份未被人工改变的自动旧稿，写入后读回确认。失败保留候选与delivery_pending，状态显示候选稿待交付；全部登记成果读回哈希一致后才完成角色。历史、人工删改、历史路径移动均保护；受保护返回2，参数错误1，成功0。review/final=done还要求有效check_report。

## 成果核验

`python tools/check_outputs.py check --input 核验配置.json --output 新报告.json`；显式给 batch/stage/files、money、formulas、materials、coverage、opinion_sources、print_report。位置由制表和核实交接，缺定位、缓存、回源或打印记录记未验证。money key submitted/audited/delta，金额用字符串；loc 给 file/sheet/cell 或 Word paragraph 与唯一捕获 pattern；大写金额加 kind=uppercase。初审不强加金额页。names 和 equations 可核标题及金额关系。特殊批次编码可给 batch_pattern；项目特定禁用名称或错名模式由 forbidden_name_terms / forbidden_text_patterns 配置提供，不按通用规则外推。

materials 的 carrier=electronic/scan/both/na/missing/unknown，content_status 单独记录 confirmed/missing/na 或待核；整批缺件需 scope=whole 与 opinion_text。coverage 含 expected_objects/objects/expected_problems/problems，各项有 refs 和 confirmed/unfounded/na 等结果，存疑保持未验证。opinion_sources 为全部编号意见给 paragraph/source，自动条目需要 decision=confirmed；historical=true 时格式命中只给建议。

打印报告版本2由下节入口产生，含当前成果、PDF、逐页图片哈希、软件版本、页对应关系、实际尺寸与字号，以及每页看图记录。旧版布尔标记不满足新版验收。工具返回0只证明配置内检查通过，不替代业务判断；2为失败或未验证，1为输入/执行错误。

## 统一制表、重算与打印验收

`python tools/print_layout.py 工作副本.xlsx --config 打印配置.json --output 新候选.xlsx` 设置打印并生成 `新候选.xlsx.layout.json`。土地在现有汇总表、明细表设置 A4 分页；其他类别按模板的打印配置生成。扩行、删行或类别改变后重新生成。输入与输出使用不同路径。

打印配置示例：

```json
{
  "template_category": "land_tree",
  "categories": ["land", "crop", "tree"],
  "stage": "INITIAL",
  "project_name": "合成示例项目",
  "batch": "示例段第一批",
  "title": "示例项目示例段第一批",
  "compensation_name": "土地、青苗及竹果木补偿",
  "end_rows": {"补偿审核明细表": 8, "补偿审核汇总表": 8}
}
```

类别以模板清单为准，`categories` 填本批实际类别。阶段为 INITIAL / PAYMENT / SETTLEMENT，`end_rows` 填合计前最后一行。土地的 `title` 是经认可的项目简称及批次组成的标题前缀，省略时取项目与批次；工具追加 `compensation_name` 与“审核汇总表”或“审核明细表”。`margins_mm` 可覆盖边距。行政村以本批原件为准。

`python tools/office_export.py --config 导出配置.json --output 新导出目录 --engine wps` 使用独立 WPS 实例；交付给 Excel 用户时选择 `--engine excel`。需相应桌面软件及 Windows pywin32。程序按布局重算、保存、重新打开并导出 PDF。土地汇总设置横纵各一页，明细按指定列分区并允许纵向续页；在选定软件中出现意外横向断页时收紧明细缩放。导出目录的工作簿是带重算缓存的待发布候选。

```json
{
  "batch": "【批次全称】",
  "files": [
    {"kind": "audit", "file": "【新审核候选绝对路径】", "layout": "【其layout.json绝对路径】"},
    {"kind": "materials", "file": "【新符合性候选绝对路径】", "layout": "【其layout.json绝对路径】"},
    {"kind": "opinion", "file": "【新意见候选绝对路径】", "font_pt": 14}
  ]
}
```

完整批次登记三件套。只制表或专项导出相应子集。核查表导出为 `征地拆迁资料符合性核查表（改）.xlsx`，初审与正式审核 Word 分别沿用所选母版字号及版式。

1. `python tools/print_check.py inspect --input 新导出目录/export.json --output 新看页目录` 解析并渲染全部物理页，检查 A4、标题、重复表头、分区、字段和合计。土地表从保存文件读回 A1 及页眉22磅加粗，并记录 PDF 实测字号供看页判断。按 100% 输出的其他布局继续检查实际字号下限。
2. 实际查看每页PNG后写看页决定JSON，pages 的键取报告中的每页id；每页记录 reviewer 与 checks，checks 必须逐项包含 text、borders、title、total、pagination、readable 的真实布尔结果。生成图片本身不是看页。
3. `python tools/print_check.py review --input 新看页目录/print_report.json --decisions 看页决定.json --output 新打印验收.json`：重读PDF和图片验证哈希及对应关系，自动失败与未完成项不能靠布尔标记消除。
4. 使用guard发布导出候选并确认磁盘读回。`python tools/print_check.py rebind --input 新打印验收.json --files 交付路径映射.json --output 新交付打印验收.json`：映射为 kind→实际绝对路径，仅允许字节与重算候选一致，保留原PDF与每页图证据；新报告及相邻导出记录均保留。
5. 成果核验配置的 files 与 print_report 指向当前交付文件及新交付打印验收报告，再运行 check_outputs。任何保存或改稿导致哈希变化后重新导出、看页及核验；旧版报告只保留为历史证据。

Gemini 分组结果：`python tools/check_outputs.py split --input 批次清单.json --response 组审核.md --output 新报告.json`。清单每项 batch/output，以完整 `## 批次：` 标题对应，报告 completed/missing/duplicate/unexpected/protected_conflicts；缺节单独续做，不按返回顺序猜批次。

## 回归与公开包

将 LAND_AUDIT_TEST_DIR 设置为本次工作目录，运行 `python -m unittest discover -s tools/tests`；测试只清自己的临时样本。选配 LAND_AUDIT_GEMINI_BRIDGE 指向已有本机 Gemini CLI 可运行模拟恢复和真实多进程锁测试，不上传业务。

`python tools/build_public.py` 更新阅读版和清单；`python tools/public_check.py --release --manifest public_manifest.json --denylist 私有敏感词.json` 检查入口与模板齐全、清单完整、白名单文本和 Office 全包，报告不输出敏感命中原值。维护者仍需人工抽查。`python tools/build_public.py --zip 输出包.zip --denylist 私有敏感词.json` 严格按清单打包，包含各文件校验值；.local、凭据、真实原文和旧 Git 历史不入包。
