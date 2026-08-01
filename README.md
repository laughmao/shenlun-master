# 申论复盘一体版

本仓库将两种发布形态明确分开：

- `redskill-version/`：申论 Skill 的唯一源码，可直接用于 RedSkill 发布。
- `github-version/`：GitHub 完整版，包含 WorkBuddy、Codex、Claude Code、TRAE 的 Skill、MCP、Hook、安装脚本和测试。
- `docs/`：发布测试提示词与项目交接文档，不参与安装。

请只在 `redskill-version/` 修改申论规则、评分引擎和 MCP 源码。运行下面的命令后，系统会自动生成 GitHub 版及各客户端插件中的 Skill 副本：

```bash
python3 github-version/scripts/build_integrations.py
```

## WorkBuddy 一键安装

```bash
d="$(mktemp -d)" && git clone --depth 1 https://github.com/liuyuexi1987/shenlun-review-pro.git "$d/repo" && python3 "$d/repo/github-version/scripts/install_workbuddy.py"
```

更多平台安装和开发说明见 `github-version/README.md`。
