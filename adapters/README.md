# 宿主适配

审核方法和格式独立于宿主。支持单个 AI、可委派的多 Agent 宿主及可选本机 Gemini/OCR；所有角色按 workflow.md 使用同一事实、输出和完成边界。

公开使用时直接读规则和空白母版，按本批资料与路径执行。下列配置仅供有宿主集成的使用者选配。各路径由使用者按自己的环境填写，凭据由自己的宿主管理。

已有 Gemini bridge 的 task CLI 通过 `--prompt-file/--out/--work-dir` 调用，共享浏览器锁；final_sent=true 的有效断点用 --resume-url 恢复。附件限制遵循实际 CLI --help 与版本，不通过改变 TEMP/TMP 绕过锁。OCR 按宿主现有能力定位后回原图。

cost_skill 的征地入口和已有安装技能可用薄入口引用 AI_START.md；程序兼容入口转调 tools/ledger_check.py，维护同一检查目录和契约。路径由本机部署配置，不把业务目录写入公开文档。
