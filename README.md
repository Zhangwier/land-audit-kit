# 征地审核规范、句式与模板库

供 AI 或审核人员处理土地、青苗竹果木、房屋构建筑物、坟墓及补偿支付资料。提供可编辑空白母版、逐项审核方法、条件化意见句式和可选小工具。

把下面这句话连同本次资料交给 AI：

> 请先完整读取本库 AI_START.md，按其中入口选择本次范围和阶段。审核我提供的资料，使用本库母版制表；意见参考对应句式的适用条件，逐项回源，交付实际已核范围和未核事项。

GitHub 阅读入口：[AI_START.md](AI_START.md)。不能逐文件读取的 AI 使用 [AI_README.md](AI_README.md)；不能访问 GitHub 时，把 [Release 下载包](https://github.com/Zhangwier/land-audit-kit/releases/latest)解压后连同本次资料上传。二进制母版在 [templates/](templates/)，需要下载后使用。

| 内容 | 入口 |
|---|---|
| 本次任务如何开始 | [AI_START.md](AI_START.md) |
| 实质审核、证据及未核边界 | [审核方法](rules/audit.md) |
| 格式、公式、汇总与打印 | [制表与文笔](rules/format.md) |
| 条件化意见句式 | [分类阅读版](catalog/opinions.md) / [JSON](catalog/opinions_catalog.json) |
| 可逐项执行的检查目录 | [检查目录 CSV](catalog/check_catalog.csv) |
| 单 AI / 多 Agent 交接 | [workflow.md](workflow.md) |
| 母版用途与扩行 | [模板清单](templates/template_manifest.json) |
| 小工具及依赖 | [工具说明](docs/tools.md) |
| 防止重复失误 | [复核经验](docs/lessons.md) |
| 已验证范围与局限 | [验证说明](docs/validation.md) |

本库中的占位符和合成示例用于演示；当前事实、政策适用和补偿标准取自本次原件。初稿交付与实审完成分别记录。项目特定口径由使用者另行提供。

维护时修改权威源文件，再运行 `python tools/build_public.py` 更新句式阅读版与合订文本。公开文件清单见 `public_manifest.json`；源码仓库与下载包都只包含清单内文件。

文档、目录内容、模板和示例采用 [CC BY 4.0](LICENSE-CONTENT)，代码采用 [MIT](LICENSE-CODE)。来源记录和真实案例由维护者在本机私有证据区保存，不随公开库发布。
