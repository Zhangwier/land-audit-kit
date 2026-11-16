# 项目入口

处理业务任务先读 AI_START.md，按任务类别与阶段读取其指向的规则。维护本库先读 README.md；审核方法、格式、句式 JSON 和检查目录各维护一处，生成文件由 tools/build_public.py 更新。

使用者已有项目约定时按该约定执行。维护者本机的可选配置在 `.local/local_format.md`。

实际业务原件与人工稿保持原位置；候选成果另存，通过guard发布并确认磁盘读回。制表、重算与打印验收按docs/tools.md统一入口，版本2打印证据绑定当前成果和逐页图片。真实来源、原文对照、项目配置与验收底稿仅存.local/，凭据存用户secrets目录。公开前按public_manifest.json白名单打包并检查Office全包隐藏内容。
