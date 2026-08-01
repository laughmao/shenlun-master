# 申论复盘一体版

这是申论专用主项目，也是唯一开发源。项目不包含行测，也不再按“GitHub 版”“RedSkill 版”维护两套源码。

申论规则、评分引擎和 MCP 源码统一放在 `skills/shenlun-review-pro/`。WorkBuddy、Codex、Claude Code、TRAE 的完整接入包，以及渠道无关的独立 Skill，都从这份源码生成。

## 目录

```text
shenlun-review-pro/
├── skills/shenlun-review-pro/          # 唯一 Skill 源码与单元测试
├── integrations/workbuddy/             # WorkBuddy 轻量插件模板
├── integrations/codex/                 # Codex 轻量插件模板
├── integrations/claude-code/           # Claude Code 轻量插件模板
├── integrations/trae/                  # TRAE 安装器
├── scripts/build_integrations.py        # 生成各客户端插件中的 Skill 副本
├── scripts/export_standalone_skill.py   # 导出渠道无关的独立 Skill
├── tests/                               # 跨平台与安装测试
├── docs/                                # 测试提示词与交接文档
└── dist/                                # 构建生成的完整安装包，不提交 Git
```

`integrations/` 只保存清单、MCP 和 Hook 等轻量模板，不再重复保存完整 Skill。今后只修改 `skills/shenlun-review-pro/`；完整插件由构建器输出到 `dist/`。

## 开发与校验

```bash
python3 scripts/build_integrations.py
python3 -m unittest discover -s skills/shenlun-review-pro/tests -v
python3 -m unittest discover -s tests -v
```

构建完成后得到：

- `dist/workbuddy/`
- `dist/codex/shenlun-review/`
- `dist/claude-code/shenlun-review/`

## 导出独立 Skill

需要发布到 RedSkill 或其他支持 Skill 的平台时执行：

```bash
python3 scripts/export_standalone_skill.py
```

默认导出到与本项目并列的 `shenlun-review-skill/`。该目录是发布产物，不作为开发源；再次导出前会自动备份旧目录。

## 平台接入

| 平台 | 推荐形态 | 说明 |
| --- | --- | --- |
| WorkBuddy | 完整插件 | 同时提供 Skill、MCP 和拦截 Hook |
| Codex | 原生插件 | 同时提供 Skill 和 MCP |
| Claude Code | 原生插件 | 同时提供 Skill、MCP 和拦截 Hook |
| TRAE | Skill + MCP | 安装器写入项目级 Skill 和 MCP 配置 |
| RedSkill 等 | 独立 Skill | 使用 `shenlun-review-skill/` 导出目录 |

- WorkBuddy：运行 `python3 scripts/install_workbuddy.py`。
- Codex：先构建，再使用 `dist/codex/shenlun-review/`。
- Claude Code：先构建，再使用 `dist/claude-code/shenlun-review/`。
- TRAE：运行 `python3 integrations/trae/install.py --project <项目目录>`。

### WorkBuddy 从 GitHub 一键安装

```bash
d="$(mktemp -d)" && git clone --depth 1 https://github.com/liuyuexi1987/shenlun-review-pro.git "$d/repo" && python3 "$d/repo/scripts/install_workbuddy.py"
```

安装后重启 WorkBuddy 或新建任务即可加载。
