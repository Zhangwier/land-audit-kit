# 可选小工具

Python 3.11+。`python -m pip install -r requirements.txt` 安装工具依赖；只阅读规则和使用母版无需 Python。以下命令在仓库根执行，业务输出都使用新路径。

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

运行清单字段：`package`、`batches`；每批 `id/name/info/inputs/outputs`，outputs 含 audit/materials/opinion 的绝对路径。运行 JSON 路径全部使用实际绝对路径，模板和示例中的相对路径只用于说明。

```
python tools/workflow_state.py init --run-dir 运行目录 --input 运行清单.json
python tools/workflow_state.py status --run-dir 运行目录
python tools/workflow_state.py record --run-dir 运行目录 --input 交接事件.json
python tools/workflow_state.py guard --run-dir 运行目录 --batch 批次ID --file 登记成果
python tools/workflow_state.py guard --run-dir 运行目录 --batch 批次ID --role writer --file 登记成果 --candidate 候选稿
```

角色 prepare/gemini/table/writer/verify/review/final；状态 pending/running/waiting_input/waiting_user/done/failed/canceled。先登记 running 和真实 task_id，再发布或登记 done；等待时保留 task_id。同角色全轮只用一个活跃 ID。

done 需要 artifacts 的实际文件，pending 为空；table 覆盖两张表，writer 覆盖意见。`record` 不能认领历史稿。guard 查询不写入，给 candidate 才发布：检查占用与哈希，备份未被人工改变的自动旧稿。历史、人工删改、历史路径移动均保护；受保护返回 2，参数错误 1，成功 0。review/final=done 还要求有效 check_report。

## 成果核验

`python tools/check_outputs.py check --input 核验配置.json --output 新报告.json`；显式给 batch/stage/files、money、formulas、materials、coverage、opinion_sources、print_report。位置由制表和核实交接，缺定位、缓存、回源或打印记录记未验证。money key submitted/audited/delta，金额用字符串；loc 给 file/sheet/cell 或 Word paragraph 与唯一捕获 pattern；大写金额加 kind=uppercase。初审不强加金额页。names 和 equations 可核标题及金额关系。特殊批次编码可给 batch_pattern；项目特定禁用名称或错名模式由 forbidden_name_terms / forbidden_text_patterns 配置提供，不按通用规则外推。

materials 的 carrier=electronic/scan/both/na/missing/unknown，content_status 单独记录 confirmed/missing/na 或待核；整批缺件需 scope=whole 与 opinion_text。coverage 含 expected_objects/objects/expected_problems/problems，各项有 refs 和 confirmed/unfounded/na 等结果，存疑保持未验证。opinion_sources 为全部编号意见给 paragraph/source，自动条目需要 decision=confirmed；historical=true 时格式命中只给建议。

打印记录包含当前三成果 source_hashes、tool、recalculated、pages_checked、no_blank_pages、no_orphan_total_or_date、实际 pdfs；实际操作并看页后才写 true。工具返回 0 只证明配置内检查通过，不替代业务判断；2 为失败或未验证，1 为输入/执行错误。

Gemini 分组结果：`python tools/check_outputs.py split --input 批次清单.json --response 组审核.md --output 新报告.json`。清单每项 batch/output，以完整 `## 批次：` 标题对应，报告 completed/missing/duplicate/unexpected/protected_conflicts；缺节单独续做，不按返回顺序猜批次。

## 回归与公开包

将 LAND_AUDIT_TEST_DIR 设置为本次工作目录，运行 `python -m unittest discover -s tools/tests`；测试只清自己的临时样本。选配 LAND_AUDIT_GEMINI_BRIDGE 指向已有本机 Gemini CLI 可运行模拟恢复和真实多进程锁测试，不上传业务。

`python tools/build_public.py` 更新阅读版和清单；`python tools/public_check.py --release --manifest public_manifest.json --denylist 私有敏感词.json` 检查入口与模板齐全、清单完整、白名单文本和 Office 全包，报告不输出敏感命中原值。维护者仍需人工抽查。`python tools/build_public.py --zip 输出包.zip --denylist 私有敏感词.json` 严格按清单打包，包含各文件校验值；.local、凭据、真实原文和旧 Git 历史不入包。
