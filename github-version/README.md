# 申论复盘一体版｜GitHub 完整版

这是申论专用项目，不包含行测，也不再使用 `gongkao` 作为项目或市场名称。

项目只维护一份申论源码：仓库根目录的 `redskill-version/`。本目录负责生成 GitHub 完整版，供 WorkBuddy、Codex、Claude Code 和 TRAE 使用；各平台均复用同一套题型协议、评分引擎和结构化 MCP Server，避免评分口径漂移。

## 推荐安装形态

| 平台 | 推荐形态 | 原因 |
| --- | --- | --- |
| WorkBuddy | 完整插件 | 同时提供 Skill、MCP 和拦截 Hook，自动加载最完整 |
| Codex | 原生插件 | 同时提供 Skill 和 MCP，适合版本化安装与更新 |
| Claude Code | 原生插件 | 同时提供 Skill、MCP 和拦截 Hook |
| TRAE | Skill + MCP | TRAE 支持项目级 Skill 和标准 MCP，使用安装脚本写入可靠的绝对路径 |

WorkBuddy 不建议改成“只装 MCP”。MCP 只负责安全执行结构化评分；真正决定如何读材料、建点池和写批改正文的是 Skill，Hook 则用于阻止模型绕回终端拼接 JSON。完整插件能把三者一起交付。

## 目录

```text
shenlun-review-pro/
├── redskill-version/                    # 唯一源码、可直接发布到 RedSkill
├── github-version/
│   ├── skills/shenlun-review-pro/       # 构建生成的 GitHub Skill
│   ├── integrations/workbuddy/          # WorkBuddy 完整插件市场
│   ├── integrations/codex/              # Codex 原生插件
│   ├── integrations/claude-code/        # Claude Code 原生插件
│   ├── integrations/trae/               # TRAE 安装器
│   ├── scripts/build_integrations.py     # 从 RedSkill 源码生成全部副本
│   └── tests/                            # 跨平台一致性测试
└── docs/                                 # 测试提示词与交接文档
```

## 开发与校验

```bash
python3 github-version/scripts/build_integrations.py
python3 -m unittest discover -s redskill-version/tests -v
python3 -m unittest discover -s github-version/tests -v
```

`github-version/skills/` 和各插件内的 Skill 都是构建产物，不应手工修改；所有申论规则和引擎修改都应先落在 `redskill-version/`。

## 本地接入

- WorkBuddy：运行 `python3 github-version/scripts/install_workbuddy.py`，安装器会复制完整插件、注册本地市场并启用插件，然后重启或新建任务。
- Codex：把 `github-version/integrations/codex/shenlun-review/` 放入本地插件市场后安装；新建任务后加载 Skill 与 MCP。
- Claude Code：复制 `github-version/integrations/claude-code/shenlun-review/` 到 `~/.claude/skills/shenlun-review/`，或用 `claude --plugin-dir` 临时测试。
- TRAE：在目标项目执行 `python3 github-version/integrations/trae/install.py --project <项目目录>`，安装器会保留已有 MCP，并写入可靠的绝对路径。

RedSkill 直接发布仓库根目录的 `redskill-version/`；其申论规则与完整版相同，只是不包含客户端插件负责的自动加载与 Hook 能力。

### WorkBuddy 从 GitHub 一键安装

```bash
d="$(mktemp -d)" && git clone --depth 1 https://github.com/liuyuexi1987/shenlun-review-pro.git "$d/repo" && python3 "$d/repo/github-version/scripts/install_workbuddy.py"
```

该命令会安装完整的 Skill、MCP 与 Hook，并在修改 WorkBuddy 配置前自动保留备份。安装后重启 WorkBuddy 或新建任务即可加载。
