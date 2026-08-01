# 申论复盘一体版

这是申论专用的 GitHub 多智能体版本，包含完整 Skill、固定 MCP Server 和安全执行 Hook。RedSkill 商店版位于独立项目 `shenlun-review-skill`，本仓库不会导出、覆盖或更新该目录。

## 核心结构

```text
shenlun-review-pro/
├── .agents/plugins/marketplace.json       # Codex Git marketplace
├── .claude-plugin/marketplace.json        # Claude Code marketplace
├── plugins/shenlun-review/                 # 唯一完整插件源码
│   ├── .codex-plugin/plugin.json
│   ├── .claude-plugin/plugin.json
│   ├── .codebuddy-plugin/plugin.json
│   ├── config/                             # 各宿主 MCP 配置
│   ├── hooks/                              # Claude / WorkBuddy Hook
│   └── skills/shenlun-review-pro/          # 唯一 Skill、引擎和测试
├── integrations/workbuddy/                 # WorkBuddy marketplace 模板
├── integrations/trae/                      # TRAE 项目安装器
├── scripts/install_workbuddy.py
└── tests/
```

申论规则、评分引擎和 MCP 只维护在 `plugins/shenlun-review/skills/shenlun-review-pro/`。`dist/` 只是可选离线构建产物，不是安装前提。

## 安装

要求：Git、Python 3.10 或更高版本。

### Codex

```bash
codex plugin marketplace add liuyuexi1987/shenlun-review-pro
codex plugin add shenlun-review@shenlun-review
```

完成后新建任务，显示名为“申论复盘一体版”。

### Claude Code

```bash
claude plugin marketplace add https://github.com/liuyuexi1987/shenlun-review-pro
claude plugin install shenlun-review@shenlun-review
```

完成后重启 Claude Code。

### WorkBuddy

```bash
d="$(mktemp -d)" && git clone --depth 1 https://github.com/liuyuexi1987/shenlun-review-pro.git "$d/repo" && python3 "$d/repo/scripts/install_workbuddy.py"
```

安装器会保留其他设置，禁用旧申论插件，并把重复的独立申论 Skill 移入可恢复备份。完成后必须完全退出并重启 WorkBuddy，再新建任务。

### TRAE

克隆本仓库后，在目标项目中执行：

```bash
python3 /path/to/shenlun-review-pro/integrations/trae/install.py --project /path/to/target-project
```

安装器只写入目标项目的 `.trae/skills/shenlun-review-pro` 和 `.trae/mcp.json`，会保留其他 MCP Server。

## 交给智能体安装

只需告诉智能体：

> 请安装 https://github.com/liuyuexi1987/shenlun-review-pro ，按 README 中与你当前平台对应的方式执行；安装后验证申论 Skill 和 `shenlun_grade_once`、`shenlun_validate_user_output` 两个 MCP 工具均已加载。

## 开发与验证

```bash
python3 scripts/build_integrations.py
python3 -m unittest discover -s plugins/shenlun-review/skills/shenlun-review-pro/tests -v
python3 -m unittest discover -s tests -v
```

可选离线包生成到 `dist/workbuddy/`、`dist/codex/shenlun-review/` 和 `dist/claude-code/shenlun-review/`。

Windows 用户可先手动克隆仓库，再用系统中的 Python 3 可执行文件运行安装器；README 中的 `mktemp` 一行适用于 macOS 和 Linux。
