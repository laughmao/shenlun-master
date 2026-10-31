# 申论复盘 · Web 版

把本仓库的申论方法论（SKILL.md + 方法卡 + 题型协议）通过网页直接用起来，
不依赖 Claude Code / WorkBuddy 等宿主，**朋友打开即用，无需任何配置**。
手机浏览器可用。

## 运行

```bash
cd webapp
uv venv .venv --python 3.13
uv pip install --python .venv/bin/python claude-agent-sdk
# 国内网络慢可加镜像：
# UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple \
#   uv pip install --python .venv/bin/python claude-agent-sdk

cp .env.example .env   # 填入服务端默认模型（base_url / api_key / model）
.venv/bin/python server.py            # 默认端口 8787
```

启动后会打印本机和局域网地址，手机与电脑连同一 Wi-Fi 即可直接访问。

## 架构

```
浏览器 (static/)  →  server.py（stdlib HTTP + SSE）
                         ├─ 默认路径：Claude Agent SDK → 服务端 .env 内置的默认模型
                         └─ 备用路径：前端自带 OpenAI 兼容配置（可选，个人 BYO）
                                ↑
                启动时从 plugins/ 的 Skill 目录组装 system prompt
                （SKILL.md + output-style + 方法论核心 + 5 份题型协议）
```

- 默认 AK 只存在于服务端 `.env`（已加入 `.gitignore`，权限 600），浏览器端零配置。
- 朋友使用时不消耗自己的 Key；想用自己的接口的人可在页面 ⚙︎ 里填 OpenAI 兼容配置。
- 云服务器部署时：克隆仓库 → 建 venv → 配 `.env` → 启动即可，无需其他依赖。

## 功能

- **三种模式**（顶部大卡片切换）：
  - 📖 **解析模式**：填 题干 + 给定资料 → 拆题、找点、示范答案（不评分）。
    额外提供「🎓 学习模式」：私教苏格拉底式提问引导，学生自己走到答案面前，
    费曼式复述通过后才出示标准答案并对照总结
  - ✅ **批改模式**：填 题干 + 材料 + 我的作答 → 逐条评点、档位建议分
  - 🌐 **抓题模式**：内置 gkzhenti 公开真题库（77 套申论整卷，可搜索），
    点击即用固定脚本秒级提取全部题干和材料（不消耗 LLM），预览确认后
    可一键进入解析或学习模式
- 学习模式为多轮对话（聊天 UI），历史由前端维护、服务端无状态
- SSE 流式输出，Markdown 渲染，一键复制完整正文
- 等待动画（思考点 + 秒数计时 + 进度光带），手机模式适配

## 说明

- 当前版本由 LLM 直接按方法论输出正文，未接入 `review_engine.py` 的确定性
  评分引擎（点池一致性校验、字数门禁、SQLite 学情库）。接入引擎做"真评分"
  是下一步工作。
- Agent SDK 路径当前按"整块消息"流式（块间有间隔，非逐字），因为部分上游
  中转不支持逐字透传；OpenAI 兼容 BYO 路径为逐字流式。
