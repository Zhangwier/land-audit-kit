# 项目入口

处理业务任务先读 AI_START.md，按任务类别与阶段读取其指向的规则。维护本库先读 README.md；审核方法、格式、句式 JSON 和检查目录各维护一处，生成文件由 tools/build_public.py 更新。

实际业务原件与人工稿保持原位置；候选成果另存，通过 guard 发布。真实来源、原文对照、项目配置与验收底稿仅存 .local/，凭据存用户 secrets 目录。公开前按 public_manifest.json 白名单打包并检查 Office 全包隐藏内容。
