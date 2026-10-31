# AGENTS.md — 给 AI 助手的项目指南

## 这个仓库是什么

**申论复盘一体版**：面向申论（公务员考试写作）学习者的题目解析与作答批改工具。
包含两个可独立使用的形态：

1. **AI 宿主插件**（`plugins/`）：安装到 Claude Code / Codex / WorkBuddy / TRAE 的
   Skill + MCP 插件，方法论在 `plugins/shenlun-review/skills/shenlun-review-pro/`。
2. **Web 版**（`webapp/`）：独立网页应用，不依赖任何 AI 宿主，手机可用。
   **部署相关问题以本文件和 `webapp/DEPLOY.md` 为准。**

## Web 版架构（改动前必读）

```
浏览器 (webapp/static/)  →  webapp/server.py（纯标准库 HTTP + SSE）
                               ├─ 解析/批改/学习模式 → claude-agent-sdk → 上游模型
                               │    system prompt 由 server.py 启动时从
                               │    plugins/ 的 SKILL.md + references/ 动态组装
                               ├─ 抓题模式 → 固定脚本解析 gkzhenti.cn 试卷页
                               │    （parse_paper_text，不用 LLM）
                               └─ .env 配置经 compose 只读挂载，仅启动时读取一次
```

关键文件：

| 文件 | 作用 |
|---|---|
| `webapp/server.py` | 后端全部逻辑：路由、SSE 流式、试卷解析器、各模式 prompt |
| `webapp/static/app.js / index.html / style.css` | 前端（无框架、无构建步骤） |
| `webapp/.env` | **密钥唯一存放处**（BASE_URL / API_KEY / MODEL），已 gitignore + dockerignore |
| `webapp/sample_questions.json` | 内置示例题 |
| `webapp/requirements-webapp.txt` | 锁定 claude-agent-sdk 版本 |

## 本地开发

```bash
cd webapp
uv venv .venv --python 3.13
UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple \
  uv pip install --python .venv/bin/python claude-agent-sdk
cp .env.example .env   # 填入真实 Key（绝不提交）
.venv/bin/python server.py   # http://localhost:8787，同 Wi-Fi 手机可访问
```

运行时要求：Python ≥3.10 **且** Node.js ≥18（claude-agent-sdk 底层跑 Node CLI）。

## 部署（Docker）

见 `webapp/DEPLOY.md`。速查：

```bash
docker compose up -d --build   # 首次部署 / 代码更新
docker compose logs -f         # 日志
docker compose restart         # 改 webapp/.env 后重启即可
curl http://127.0.0.1:8787/api/health   # 健康检查，default_ready=true 表示 Key 已加载
```

## 硬性约束（违反会出事故）

- **`webapp/.env` 永远不进 git 和 Docker 镜像**。已由 `.gitignore`、`.dockerignore`
  双重排除；提交前应扫描暂存内容确认无真实 Key/中转地址。
- 抓题模式**必须用固定脚本**（`parse_paper_text`），不许走 LLM——这是明确的用户决策。
  gkzhenti 页面有 4 种题号格式（`一、` / `第一题：` / `（一）` / 无题号），改解析器时
  四种都要兼容并全量回归 77 份试卷。
- 解析/批改的 system prompt 从 `plugins/` 的 Skill 文档动态组装，**不要把方法论
  硬编码进 server.py**；SKILL.md 的 MCP 工具执行锁靠 `WEB_ADAPTATION` 前言解除。
- 前端无框架无构建：改静态文件刷新即生效，不要引入 npm/打包链。
- 上游模型经 OpenAI 兼容或 Anthropic 兼容中转访问，模型名可能带前缀/后缀
  （如 `ark/glm-5.3-flash[1m]`），以 `.env` 配置为准。
