# 可选程序台账契约

使用 tools/ledger_check.py 或兼容 CLI 时读取本文件；机器约束由 [JSON schema](../contracts/land_audit.schema.json) 定义。它保留既有检查ID、金额与状态契约。正常业务交付按 [AI入口](../AI_START.md) 和 [格式](../rules/format.md) 选择两表及意见，不强制建立额外工作簿或目录流水线。

## 3. 初始化对象 CSV

模板见 [objects.csv](../examples/objects.csv)。至少需要 `object_id,object_name,modules`；模块以英文分号连接。可用 batch、town、village、group、parcel_id、building_id、floor、parent_id、comment 保留层级。空表不能初始化；每行须来自原始资料识别，不能以示例行替代真实对象。

## 4. JSON 台账

机器格式以 [land_audit.schema.json](../contracts/land_audit.schema.json)为准。它与已有数量审核 schema 分开，不能把金额塞进 original_qty。

| 集合 | 唯一键 | 内容 |
|---|---|---|
| meta | project_id | 模式、阶段、scope_basis（范围及覆盖分母依据）、rounding_basis（金额舍入规则） |
| sources | source_id | path、sha256、role、version；本项目引用视图，不代替来源登记和冻结证据契约 |
| policies | policy_id | title、status、region_basis、time_basis、subject_basis、adoption_basis、source_refs |
| objects | object_id | 名称、层级标识、模块、source_refs |
| checks | object_id + check_id | status、observation、source_refs、evidence_refs、calculation_ref、policy_ids、reason、next_action、reviewed_by、review_round、issue_ids |
| fees | fee_id | object_id、category、unit、五类金额、decision、source_refs、calculation_ref、policy_ids、decision_basis、review_ref、issue_ids |
| issues | issue_id | object_id、check_ids、kind、fact_level、observation、source_refs、recommendation、money_effect、fee_ids、status、review_ref、close_reason |

`source_refs` 每个元素为 `{ "source_id": "登记来源ID", "loc": "物理P3/印刷P2，第5行；或Sheet1!D12；或原页坐标" }`。`evidence_refs` 只能引用实际存在的工具证据，禁止 AI 自造证据 ID。记录校验不验证这些证据真实性，执行者仍须回读实际证据并遵守本次宿主的来源与冻结证据约定。`calculation_ref`、`review_ref` 必须指向可查的计算/复核记录；工具仅要求填写，审核执行者还要实际打开验证。

检查状态：NOT_STARTED、PASS、FINDING、BLOCKED、NA。NOT_STARTED 不计已审；BLOCKED 不计已完成；NA 计已处理但不计实质复核。PASS/FINDING 要有观察、来源、审核人、轮次；目录要求计算/政策的项还需相应引用。FINDING 需关联同对象问题；NA 需理由、来源、审核人、轮次；BLOCKED 需原因和下一步。

费用的五类金额是 submitted_amount、calculated_amount、adopted_amount、adjustment_amount、pending_amount，均为普通十进制字符串或 null，例如 `"123.45"`；禁止浮点 JSON 数字、科学计数法、NaN。工具接受最多两位小数；更高精度过程值保留在计算底稿。

- PENDING：adopted_amount 和 adjustment_amount 必须 null；已知涉及额填 pending_amount，无法确定则 null，并解释范围。没有送审额也能登记待核项。
- CONFIRMED：只允许 FULL；送审、计算、采用、调整金额齐全，pending_amount 为零；采用=送审+调整；对象无 NOT_STARTED/BLOCKED，政策适用、复算和复核记录齐全。与计算额不同须在 decision_basis 解释采用理由。未关闭的金额问题不能进入确认。
- OUT_OF_SCOPE：有证据支持本轮范围外，采用/调整留空，待核额为零，不能借此排除证据不足的范围内项目；送审范围外金额另列，不混入范围内总计。

问题的 money_effect 为 NONE/PENDING/ADJUSTMENT；状态 OPEN/RESPONDED/CLOSED/WITHDRAWN。有金额影响须连到费用；CLOSED/WITHDRAWN 须保留关闭/撤回理由及复核记录。kind 是具体业务分类，fact_level 为 FACT/CALC/INFERENCE/UNCERTAINTY。

## 5. 校验报告能证明什么

`ok` 只表示台账结构和有限跨表约束通过。`review_readiness` 为 IN_PROGRESS 或 COMPLETE_RECORDS；后者只表示所选模式的检查记录齐备、范围依据和对象来源已填、问题闭环。FULL 另要求费用无待核、每个对象直接或通过子对象关联费用、精度依据已填；父汇总对象不新增费用。ARITHMETIC 也需精度依据。DOCUMENT/POLICY 可无费用，专项检查记录完成仍不形成采用金额。`review_readiness_scope` 明示模式。`business_verified` 永远为 false，工具不自动认定事实、政策或最终审批效力。

`--verify-sources` 检查台账所列来源路径的当前 SHA256，不核查签名、文件内容真实性或未登记遗漏。未启用时报告 NOT_CHECKED。总览统计仅基于台账，必须与 S01/S02 外部覆盖分母交叉核对；没有费用的 FULL 空壳不能获得完整记录状态。覆盖报告分别列对象、模块状态和未关联费用对象；全范围采用额仅在 FULL 记录完整时输出，仍需独立确认覆盖分母。
