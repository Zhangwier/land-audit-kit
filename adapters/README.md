# 宿主适配

审核方法和格式独立于宿主。支持单个 AI、可委派的多 Agent 宿主及可选本机 Gemini/OCR；所有角色按 workflow.md 使用同一事实、输出和完成边界。

本机配置放 `.local/config.json`，使用者明确提供 source_root、output_root、evidence_root、scratch_root、gemini_bridge 和项目口径。凭据放用户 secrets 目录，配置与代码以 secrets 优先、原路径回落（MGSecrets），公开库不存令牌或浏览器登录态。

已有 Gemini bridge 的 task CLI 通过 `--prompt-file/--out/--work-dir` 调用，共享浏览器锁；final_sent=true 的有效断点用 --resume-url 恢复。附件限制遵循实际 CLI --help 与版本，不通过改变 TEMP/TMP 绕过锁。OCR 按宿主现有能力定位后回原图。

cost_skill 的征地入口和已有安装技能可用薄入口引用 AI_START.md；程序兼容入口转调 tools/ledger_check.py，维护同一检查目录和契约。路径由本机部署配置，不把业务目录写入公开文档。
