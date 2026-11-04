#!/usr/bin/env python3
"""申论复盘一体版 — Web 版后端。

职责：
1. 托管 webapp/static 下的静态页面。
2. 启动时从本仓库的 Skill 目录组装方法论 system prompt。
3. /api/review：优先走 Claude Agent SDK（服务端 .env 内置默认 AK，
   朋友打开即用，无需任何配置）；也兼容前端自带 OpenAI 兼容配置。

用法：
    webapp/.venv/bin/python server.py [--port 8787] [--skill-dir <path>]

首次部署：
    cd webapp && uv venv .venv --python 3.13
    uv pip install --python .venv/bin/python claude-agent-sdk
    cp .env.example .env   # 填入默认 AK（.env 不会提交到仓库）
"""

import argparse
import asyncio
import html.parser
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import store

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SKILL_DIR = REPO_ROOT / "plugins" / "shenlun-review" / "skills" / "shenlun-review-pro"
STATIC_DIR = Path(__file__).resolve().parent / "static"
ENV_FILE = Path(__file__).resolve().parent / ".env"
SAMPLES_FILE = Path(__file__).resolve().parent / "sample_questions.json"

MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_PAGE_BYTES = 2 * 1024 * 1024  # 抓取网页的体积上限
PAGE_TEXT_LIMIT = 20000           # 送入 LLM 提取的正文长度上限

# 内置真题来源：gkzhenti 公开真题库（申论整卷列表）
PAPER_LIST_URL = "https://gwy.gkzhenti.cn/paper?cls=%E7%94%B3%E8%AE%BA"
PAPER_BASE_URL = "https://gwy.gkzhenti.cn"
PAPER_LIST_TTL = 3600  # 列表缓存 1 小时
_paper_list_cache = {"ts": 0.0, "items": []}

# 从网页正文提取申论题目用的轻量 prompt（不走方法论大 prompt）。
EXTRACT_SYSTEM = """你是严谨的信息提取器。用户会给你一段网页正文，通常是申论试卷或题目页面。
请从中提取题目信息，只输出一个 JSON 对象，不要输出任何其他文字、解释或代码围栏：
{"materials": "全部给定资料的原文（不含注意事项、考试说明；含每则材料的编号行）",
 "questions": [{"stem": "每道题的完整题干原文，含题号、分值和作答要求"}],
 "reference": "参考答案原文，未找到则为空字符串"}
规则：
- 一页只有一道题时 questions 只有一个元素；整卷页面则列出全部题干。
- 题干、材料、参考答案都保持原文，不得改写、概括、补写。
- 跳过注意事项、监考说明、网站导航、广告等非题目内容。
- 页面里没有申论题目时，materials 和 questions 都返回空。"""

# 学习模式：苏格拉底式 + 费曼式私教。题目材料放在 system 的"教学底牌"区，
# 对话历史由前端逐轮传入，服务端无状态。
LEARN_SYSTEM = """# Web 运行环境说明
你运行在独立网页后端中，没有 MCP 工具、终端或文件系统，忽略一切工具调用要求。

# 你的角色：申论私教（苏格拉底式提问 + 费曼式检查）
学生想**学会**这道题，而不是听你讲完、背个答案。你的任务是通过提问和引导，
让学生自己一步一步走到答案面前。你的每句话都要为这个目标服务。

## 教学流程（不要跳步，不要一次性讲完）
1. **审题**：先问学生"这道题题干里的关键词是什么？出题人让你做什么动作？"
   让学生自己拆；他答完后追问遗漏（限定词、分值、字数、身份/文种），
   最后用一两句帮他确认完整的"题干责任清单"。
2. **找点**：让学生自己回材料找要点，一次只要 1–2 个；每个点先肯定对的部分，
   再追问"这条是做法还是成效？回到材料，原句是哪句？"；他卡住或漏方向时
   给定位提示（"材料第 X 段好像有个动作类的表述，再去读读？"），不直接报答案。
3. **组织**：让学生把要点合并成条，追问"这两条你为什么合并/拆开？字数够吗？"
4. **出答案**：只有当学生能独立说清"这题考什么、要点从哪来、为什么这样组织"
   （费曼式复述通过）之后，才展示示范答案（若有参考答案，以参考答案为准），
   并做对照总结：他哪里想对了、哪里偏了、这个方法下次怎么迁移。

## 教学原则
- 每轮只推进一小步；输出控制在 150 字左右，结尾必须落在一个引导性问题上。
- 多鼓励：答对的部分要具体表扬（对在哪、为什么对）；答错时先问"你是从材料
  哪里看出来的？"引导自查，不要直接否定，更不要流露失望。
- 费曼式检查：阶段转换前让学生用自己的话复述；复述不清就带他回材料重看。
- 学生说"不会/直接告诉我"时：可以讲一次，但讲完立刻用一个小问题检查理解，
  通过后回到流程；连续两次要求才可直接给答案并进入对照总结。
- 学生完全搞懂的标志：能自己复述全部要点来源和组织逻辑。此时停止提问，出答案。
- 材料没有给参考答案时，你自己依据材料建立示范答案，出示时机不变。

## 教学底牌（学生看不到之前的对话不会出现，你不得提前泄露）
【题干】
{question}

【给定资料】
{material}

【参考答案】（仅当学生通过费曼式复述后出示；没有则为空，届时你自己建立示范答案）
{reference}

## 学生画像（由历史学习自动沉淀，据此调整教学，不要向学生复述画像本身）
{profile}
- 画像里「易踩的坑」：学生再次踩到时，先温和点破（"这个坑咱们之前也遇到过"），再引导自查，不指责。
- 画像显示已掌握的环节（如审题、找点方法）可加快带过，把轮次留给薄弱环节。
- 画像为空或标注新学生时：按完整流程从审题开始。

## 近期练习（最近几次会话的动态，用于衔接；已讲过的不重复教）
{recent}

## 输出格式
聊天体，短段落；可用少量加粗标关键词；禁表格、禁编号刷屏、禁整段引用材料；
每轮结尾一个问题。开场（学生还没说话时）先热情地用一两句话引入这道题的场景，
然后提出第一个审题问题。"""

# 两级画像提炼：每轮一次轻量 LLM 调用，同时产出一级摘要与二级画像更新。
# 一级=近期记录滚动窗口（≤10条）；二级=综合窗口趋势的整体画像，不被单次表现带偏。
MEMO_SYSTEM = """你是学习档案管理员，维护学生的两级档案。
【一级·近期记录】每轮一条一句话摘要，系统只保留最近 10 条、自动滚动。
【二级·整体画像】稳定的画像与偏好，必须从近期记录的【整体趋势】提炼：
同类的坑重犯才算坑；连续多轮出现的偏好才算偏好；近期记录显示作答质量持续变好，
progress 就要体现进步趋势，反之体现退步——看趋势，不看单次。
现有二级画像（JSON 数组，可能为空）：
{memories}
近期记录（新→旧，最多 10 条）：
{recent}
本轮对话片段：
{transcript}
只输出一个 JSON 对象（不要其他文字）：
{"note":"本轮对话一句话摘要：练什么题型+学生表现，不超过50字",
 "profile":[{"kind":"background|pitfall|progress|strength|preference","content":"一句话，不超过60字"}]}
规则：
- note 每轮必写。
- profile 是【整体重写】的最新画像：综合现有画像与近期记录趋势精炼而成，
  最多 8 条、能合并就合并、拿不准的不写。单次波动不改变画像，趋势才改变。
  无需任何更新时 profile 返回 []（表示画像维持原样，服务端不会清空）。
- background=备考身份与目标（如"在职备考2027国考"）。
- pitfall=跨会话反复出现的具体错误（趋势显示已克服就从画像中去掉）。
- progress=整体学习阶段与趋势，只保留一条（如"归纳概括已从审题进到找点，正确率上升"）。
- strength/preference 有把握才写；与学习无关的一律忽略。
"""

MEMO_LABELS = {
    "background": "备考身份",
    "pitfall": "易踩的坑",
    "progress": "学习进度",
    "strength": "做得好",
    "preference": "教学偏好",
}

# 二级画像节流：每用户累计提炼次数（内存计数，重启归零只影响一次节流边界）。
# 每 L2_EVERY 次一级提炼才整体重写一次画像；退出学习时强制。
L2_EVERY = 6
_L2_COUNTER = {}
_L2_LOCK = threading.Lock()

# 组装 system prompt 时按顺序读取的文档（相对 skill 目录）。
PROMPT_DOCS = [
    ("", "SKILL.md"),
    ("", "references/output-style.md"),
    ("", "references/explanation-core.md"),
    ("", "references/scoring-evidence.md"),
    ("", "references/analysis-core.md"),
    ("题型协议：归纳概括", "references/protocols/归纳概括.md"),
    ("题型协议：综合分析", "references/protocols/综合分析.md"),
    ("题型协议：提出对策", "references/protocols/提出对策.md"),
    ("题型协议：贯彻执行", "references/protocols/贯彻执行.md"),
    ("题型协议：申发论述", "references/protocols/申发论述.md"),
]

# 网页环境适配说明：SKILL.md 里的 MCP 工具/回执/校验锁在 Web 版中不存在，
# 需要显式解除，否则模型会拒绝输出并要求调用不存在的工具。
WEB_ADAPTATION = """# Web 运行环境说明（优先级最高）

你当前运行在独立网页后端中，**没有任何 MCP 工具、终端或文件系统**。
SKILL.md 中所有涉及工具调用的执行锁（shenlun_grade_once、analyze-once、
finalize-analysis、validate_user_output、回执、approved_text、防重评缓存等）
一律不适用，直接忽略，不要提及它们，也不要要求用户提供工具。

替代执行方式：
- 收到批改请求（有考生作答）时，直接在回复正文中一次性输出完整批改。
- 收到解析请求（无考生作答）时，直接在回复正文中一次性输出完整解析。
- 严格遵守 SKILL.md 的"第一铁律"：完整正文直发，不用摘要或文件代替；
  严格遵守"输出要求"与 output-style 的文风：无表格、无内部 ID、无工程回执。
- 评分时按"点池与评分原则"在内部建立点池与映射，正文只呈现教学讲解与
  档位/中心值/区间/置信度结论，并说明这是经验校准建议分。
- 材料或题干缺失时列出缺项，不得虚构。

"""


def build_system_prompt(skill_dir: Path) -> str:
    """从 Skill 目录组装 Web 版 system prompt。"""
    parts = [WEB_ADAPTATION]
    for title, rel in PROMPT_DOCS:
        path = skill_dir / rel
        if not path.exists():
            print(f"[warn] 缺少文档，跳过: {path}", file=sys.stderr)
            continue
        text = path.read_text(encoding="utf-8")
        if title:
            parts.append(f"\n\n# ===== {title} =====\n\n{text}")
        else:
            parts.append(text)
    return "".join(parts)


def load_dotenv(path: Path) -> dict:
    """极简 .env 解析（KEY=VALUE，# 注释），不覆盖已存在的进程环境变量。"""
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.split(" #", 1)[0].strip()  # 去掉行内注释
        values[key.strip()] = val
    return values


def agent_cfg_from_env(env: dict) -> dict:
    """把 .env / 进程环境变量映射为内部配置名。"""
    return {
        "base_url": env.get("SHENLUN_DEFAULT_BASE_URL") or env.get("ANTHROPIC_BASE_URL") or "",
        "api_key": env.get("SHENLUN_DEFAULT_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN") or "",
        "model": env.get("SHENLUN_DEFAULT_MODEL") or "",
    }


_PAPER_FOOTER_MARKERS = ("欢迎使用公开真题库", "备案编号", "若有网络数据相关投诉")
# 分节标题两种形态：「二、给定材料」带序号前缀，或裸标题行「给定材料」。
# 注意事项里的"本题本由给定资料与作答要求两部分构成"因带有其他文字不会误配。
_MATERIAL_HEADER = re.compile(r"^(?:[一二三四五六七八九十]+、\s*)?给定(材料|资料)\s*[：:]?$")
_QUESTION_HEADER = re.compile(r"^(?:[一二三四五六七八九十]+、\s*)?作答要求\s*[：:]?$")
_QUESTION_SPLIT = re.compile(r"(?m)^[一二三四五六七八九十]+、")
_NOISE_LINE = re.compile(r"^\(\d+\)$")


def parse_paper_text(text: str) -> dict:
    """用固定规则从 gkzhenti 试卷页正文提取 {materials, questions, reference}。

    页面结构固定：注意事项 → 「给定材料」→「作答要求」→ 页脚。
    解析失败抛 ValueError，由调用方转为可读错误。
    """
    for marker in _PAPER_FOOTER_MARKERS:
        idx = text.find(marker)
        if idx > 0:
            text = text[:idx]

    # 按行定位：材料标题取作答要求之前最后一个，避免误配正文长句
    lines = text.splitlines()
    q_idx = m_idx = None
    for i, raw in enumerate(lines):
        line = raw.strip()
        if q_idx is None and _QUESTION_HEADER.match(line):
            q_idx = i
    if q_idx is not None:
        for i in range(q_idx):
            if _MATERIAL_HEADER.match(lines[i].strip()):
                m_idx = i  # 取最后一个匹配
    if q_idx is None or m_idx is None:
        raise ValueError("页面中未找到「给定材料/作答要求」分节，站点结构可能已变化")

    body = "\n".join(lines[m_idx + 1:q_idx])
    stem_zone = "\n".join(lines[q_idx + 1:])

    # 清理噪声：随卷编号 "(7569581)"（行内或独立行）与多余空行
    def clean(block: str) -> str:
        block = re.sub(r"(?m)^(材料\d+)\(\d+\)", r"\1", block)
        block = re.sub(r"(?m)^\(\d+\)\s*", "", block)
        lines = [ln for ln in block.splitlines() if ln.strip()]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()

    materials = clean(body)
    nums = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
    questions = []

    ti_split = re.compile(r"(?m)^第[一二三四五六七八九十]+题[：:]?\s*$")
    zt_split = re.compile(r"(?m)^（[一二三四五六七八九十]+）")

    def collect(parts, prefix_fmt):
        out = []
        for i, part in enumerate(parts):
            part = clean(part)
            if not part:
                continue
            stem = prefix_fmt(i) + part if prefix_fmt else part
            out.append({"stem": stem})
        return out

    if len(_QUESTION_SPLIT.findall(stem_zone)) >= 2:
        # 「一、」…「二、」…（序号可能单独成行，split 会吃掉序号，逐段补回）
        qi = 0
        for part in _QUESTION_SPLIT.split(stem_zone):
            part = clean(part)
            if not part:
                continue
            if qi < len(nums) and not part.startswith(nums[qi]):
                part = nums[qi] + "、" + part
            questions.append({"stem": part})
            qi += 1
    elif len(ti_split.findall(stem_zone)) >= 2:
        # 「第一题：」「第二题：」
        questions = collect(ti_split.split(stem_zone),
                            lambda i: "第" + nums[i] + "题：" if i < len(nums) else "")
    elif len(zt_split.findall(stem_zone)) >= 2:
        # 「（一）（二）」（早期国考卷；子项「（1）（2）」不受影响）
        questions = collect(zt_split.split(stem_zone),
                            lambda i: "（" + nums[i] + "）" if i < len(nums) else "")
    else:
        # 无题号格式：题干与要求交替排列，按「要求：」边界切分
        REQ_LINE = re.compile(r"^(要求|[（(]\d+[）)])")
        cur_stem, cur_req, seen_req = [], [], False
        for raw in stem_zone.splitlines():
            line = raw.strip()
            if not line:
                continue
            if REQ_LINE.match(line):
                if cur_stem:
                    cur_req.append(line)
                    seen_req = True
                continue
            if cur_stem and seen_req:
                questions.append({"stem": clean("\n".join(cur_stem))})
                cur_stem, cur_req, seen_req = [], [], False
            cur_stem.append(line)
        if cur_stem:
            questions.append({"stem": clean("\n".join(cur_stem))})
        questions = [
            {"stem": (nums[i] + "、" if i < len(nums) else "") + q["stem"]}
            for i, q in enumerate(questions)
        ]

    questions = [q for q in questions if len(q["stem"]) >= 10]
    if not materials or not questions:
        raise ValueError("未能从页面提取到材料和题目")

    return {"materials": materials, "questions": questions, "reference": ""}


def sse_event(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


class _TextExtractor(html.parser.HTMLParser):
    """把网页剥成正文：去 script/style，块级标签换行。"""

    _SKIP = {"script", "style", "noscript", "svg", "head"}
    _BLOCK = {"p", "div", "br", "section", "article", "li", "tr", "h1", "h2",
              "h3", "h4", "h5", "h6", "blockquote", "pre", "table", "ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        return re.sub(r"\n{3,}", "\n\n", raw).strip()


def fetch_page_text(url: str) -> str:
    """抓取网页并剥成正文文本。失败抛异常。"""
    if not url.startswith(("http://", "https://")):
        raise ValueError("URL 必须以 http(s):// 开头")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; ShenlunWeb/0.2)"},
    )
    with urllib.request.urlopen(request, timeout=20) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        raw = resp.read(MAX_PAGE_BYTES)
    extractor = _TextExtractor()
    extractor.feed(raw.decode(charset, errors="replace"))
    text = extractor.text()
    if len(text) > PAGE_TEXT_LIMIT:
        text = text[:PAGE_TEXT_LIMIT]
    if len(text) < 50:
        raise ValueError("抓取到的正文太短，可能页面需要登录或为纯图片内容")
    return text


def fetch_paper_list() -> list:
    """抓取 gkzhenti 申论整卷列表，带缓存。"""
    if _paper_list_cache["items"] and time.time() - _paper_list_cache["ts"] < PAPER_LIST_TTL:
        return _paper_list_cache["items"]
    request = urllib.request.Request(
        PAPER_LIST_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; ShenlunWeb/0.2)"},
    )
    with urllib.request.urlopen(request, timeout=20) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        page = resp.read(MAX_PAGE_BYTES).decode(charset, errors="replace")
    items = []
    seen = set()
    for href, title in re.findall(r'href="(/paper/[^"]+)"[^>]*>([^<]{4,120})', page):
        title = title.strip()
        if href in seen or "回忆版网站" in title:
            continue
        seen.add(href)
        items.append({"id": href.rsplit("/", 1)[-1], "title": title})
    if not items:
        raise RuntimeError("真题列表解析失败，站点结构可能已变化")
    _paper_list_cache["ts"] = time.time()
    _paper_list_cache["items"] = items
    return items


COOKIE_NAME = "shenlun_token"
COOKIE_TTL = 30 * 86400


class Handler(BaseHTTPRequestHandler):
    server_version = "ShenlunWeb/0.3"
    system_prompt = ""   # 启动时注入
    default_cfg = {}     # 启动时从 .env 注入：base_url / api_key / model
    invite_code = ""     # 启动时从 .env 注入；非空则注册需邀请码

    # -- 基础设施 -----------------------------------------------------------

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("[api] %s\n" % (fmt % args))

    def _send_json(self, status: int, obj: dict, set_cookie: str = None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if set_cookie:
            self.send_header("Set-Cookie",
                             "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d"
                             % (COOKIE_NAME, set_cookie, COOKIE_TTL))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- 路由 ---------------------------------------------------------------

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._serve_static("index.html", "text/html; charset=utf-8")
        elif self.path == "/app.js":
            self._serve_static("app.js", "text/javascript; charset=utf-8")
        elif self.path == "/style.css":
            self._serve_static("style.css", "text/css; charset=utf-8")
        elif self.path == "/api/health":
            self._send_json(200, {
                "ok": True,
                "prompt_chars": len(self.system_prompt),
                "default_ready": all(self.default_cfg.get(k) for k in
                                     ("base_url", "api_key", "model")),
            })
        elif self.path == "/api/samples":
            try:
                samples = json.loads(SAMPLES_FILE.read_text(encoding="utf-8"))
                self._send_json(200, {"samples": samples})
            except (OSError, ValueError):
                self._send_json(500, {"error": "内置题库读取失败"})
        elif self.path == "/api/papers":
            try:
                self._send_json(200, {"papers": fetch_paper_list()})
            except Exception as e:  # noqa: BLE001
                self._send_json(502, {"error": f"获取真题列表失败：{e}"})
        elif self.path == "/api/me":
            user = store.user_for_token(self._cookie_token() or "")
            if user:
                self._send_json(200, {"username": user["username"]})
            else:
                self._send_json(401, {"error": "未登录"})
        elif self.path.startswith("/api/conversations"):
            self._handle_conv_route("GET")
        elif self.path.startswith("/api/profile"):
            self._handle_profile_route("GET")
        else:
            self._handle_404()

    def _handle_404(self):
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/api/register":
            self._handle_register()
        elif self.path == "/api/login":
            self._handle_login()
        elif self.path == "/api/logout":
            self._handle_logout()
        elif self.path == "/api/fetch-url":
            self._handle_fetch_url()
        elif self.path == "/api/grab-paper":
            self._handle_grab_paper()
        elif self.path == "/api/review":
            self._handle_review()
        elif self.path == "/api/learn":
            self._handle_learn()
        elif self.path == "/api/profile/refresh":
            self._handle_profile_refresh()
        else:
            self._handle_404()

    def do_DELETE(self):
        if self.path == "/api/account":
            self._handle_account_delete()
        elif self.path.startswith("/api/conversations/"):
            self._handle_conv_route("DELETE")
        elif self.path.startswith("/api/profile/"):
            self._handle_profile_route("DELETE")
        else:
            self._handle_404()

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_BODY_BYTES:
                self._send_json(413, {"error": "请求体过大"})
                return None
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {"error": "请求体不是合法 JSON"})
            return None

    # -- 认证与账号 -----------------------------------------------------------

    def _cookie_token(self) -> str:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(COOKIE_NAME)
        return morsel.value if morsel else None

    def _require_user(self):
        user = store.user_for_token(self._cookie_token() or "")
        if not user:
            self._send_json(401, {"error": "请先登录"})
            return None
        return user

    def _handle_register(self):
        req = self._read_body()
        if req is None:
            return
        username = (req.get("username") or "").strip()
        password = str(req.get("password") or "")
        invite = (req.get("invite") or "").strip()
        if not re.fullmatch(r"[一-龥A-Za-z0-9_]{2,20}", username):
            self._send_json(400, {"error": "用户名需 2–20 位中文/字母/数字/下划线"})
            return
        if len(password) < 6:
            self._send_json(400, {"error": "密码至少 6 位"})
            return
        if self.invite_code and invite != self.invite_code:
            self._send_json(400, {"error": "邀请码不正确"})
            return
        if not store.disk_ok():
            self._send_json(503, {"error": "服务器存储空间不足"})
            return
        try:
            uid = store.create_user(username, password)
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return
        self._send_json(200, {"ok": True, "username": username},
                        set_cookie=store.create_token(uid))

    def _handle_login(self):
        req = self._read_body()
        if req is None:
            return
        username = (req.get("username") or "").strip()
        row = store.verify_user(username, str(req.get("password") or ""))
        if not row:
            self._send_json(401, {"error": "用户名或密码不正确"})
            return
        self._send_json(200, {"ok": True, "username": row["username"]},
                        set_cookie=store.create_token(row["id"]))

    def _handle_logout(self):
        raw = self._cookie_token()
        if raw:
            store.delete_token(raw)
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie", COOKIE_NAME + "=; Path=/; HttpOnly; Max-Age=0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_account_delete(self):
        """注销账号：级联删除全部数据并清除登录态。"""
        user = self._require_user()
        if not user:
            return
        store.delete_account(user["id"])
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie", COOKIE_NAME + "=; Path=/; HttpOnly; Max-Age=0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_profile_refresh(self):
        """退出学习等场景：对该会话立即做一次画像提炼（不看节奏）。"""
        user = self._require_user()
        if not user:
            return
        req = self._read_body()
        if req is None:
            return
        conv_id = (req.get("conversationId") or "").strip()
        if conv_id and store.get_conversation(user["id"], conv_id):
            self._spawn_extraction_from_conv(user["id"], conv_id, force=True)
        self._send_json(200, {"ok": True})

    def _handle_me(self):
        user = store.user_for_token(self._cookie_token() or "")
        if user:
            self._send_json(200, {"username": user["username"]})
        else:
            self._send_json(401, {"error": "未登录"})

    def _handle_conv_route(self, method: str):
        """GET 列表/详情，DELETE 删除。路径形如 /api/conversations[/<id>]。"""
        user = self._require_user()
        if not user:
            return
        parsed = urllib.parse.urlparse(self.path)
        if method == "GET" and parsed.path == "/api/conversations":
            q = (urllib.parse.parse_qs(parsed.query).get("q") or [""])[0].strip()[:100]
            convs = store.list_conversations(user["id"], q)
            self._send_json(200, {"conversations": convs})
            return
        m = re.fullmatch(r"/api/conversations/([0-9a-f]{32})", parsed.path)
        if not m:
            self._handle_404()
            return
        conv_id = m.group(1)
        if method == "GET":
            conv = store.get_conversation(user["id"], conv_id)
            if not conv:
                self._send_json(404, {"error": "记录不存在"})
                return
            conv = dict(conv)
            if conv["kind"] == "learn":
                conv["messages"] = store.get_messages(conv_id)
            self._send_json(200, {"conversation": conv})
        else:
            ok = store.delete_conversation(user["id"], conv_id)
            self._send_json(200, {"ok": ok})

    def _handle_profile_route(self, method: str):
        """GET 画像列表；DELETE /api/profile/<id> 软删除一条记忆。"""
        user = self._require_user()
        if not user:
            return
        m = re.fullmatch(r"/api/profile(?:/(\d+))?", urllib.parse.urlparse(self.path).path)
        if method == "GET":
            self._send_json(200, {
                "memories": store.list_memories(user["id"]),
                "recent": store.list_recent_notes(user["id"]),
            })
        else:
            mem_id = int(m.group(1))
            ok = store.drop_memory(user["id"], mem_id)
            self._send_json(200, {"ok": ok})

    # -- 抓题模式：抓网页 + LLM 提取题目 ---------------------------------------

    def _extract_paper_payload(self, req: dict, url: str):
        """抓取 url 并用 LLM 提取 {materials, questions, reference}。
        成功返回 dict（已可下发给前端）；失败时已发送错误响应，返回 None。"""
        try:
            page_text = fetch_page_text(url)
        except Exception as e:  # noqa: BLE001 — 抓取失败统一返回可读错误
            self._send_json(400, {"error": f"抓取网页失败：{e}"})
            return None

        api = req.get("api") or {}
        use_default = all(self.default_cfg.get(k) for k in ("base_url", "api_key", "model"))
        use_byo = all(api.get(k) for k in ("baseUrl", "key", "model"))
        if not use_default and not use_byo:
            self._send_json(400, {"error": "服务未配置默认模型，无法提取题目"})
            return None

        user_prompt = f"请从下面的网页正文中提取申论题目：\n\n{page_text}"
        data = None
        for attempt in range(2):  # 模型偶发格式抖动，失败自动重试一次
            try:
                if use_default:
                    raw = self._llm_extract_agent(user_prompt)
                else:
                    raw = self._llm_extract_openai(api["baseUrl"].rstrip("/"),
                                                   api["key"], api["model"], user_prompt)
            except Exception as e:  # noqa: BLE001
                self._send_json(502, {"error": f"提取题目失败：{e}"})
                return None

            raw = raw.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", raw.strip(), flags=0).strip()
            # 容忍模型在 JSON 前后偶尔带出的说明文字：取首尾大括号之间再解析
            lo, hi = raw.find("{"), raw.rfind("}")
            if lo >= 0 and hi > lo:
                raw = raw[lo:hi + 1]
            try:
                data = json.loads(raw)
                break
            except ValueError:
                if attempt == 0:
                    continue
                self._send_json(502, {"error": "模型未能按格式返回，请重试或换一份试卷"})
                return None
        materials = str(data.get("materials") or "")
        questions = data.get("questions") or []
        if isinstance(questions, list):
            questions = [
                {"stem": str(q.get("stem") or q if isinstance(q, dict) else q)}
                for q in questions if q
            ]
        else:
            questions = []
        if not materials or not questions:
            self._send_json(200, {"materials": "", "questions": [], "reference": ""})
            return None
        return {
            "materials": materials,
            "questions": questions,
            "reference": str(data.get("reference") or ""),
        }

    def _handle_grab_paper(self):
        """抓题模式主入口：按内置真题库的试卷 id 抓取整卷，用固定脚本解析。"""
        req = self._read_body()
        if req is None:
            return
        paper_id = str(req.get("paperId") or "").strip()
        if not re.fullmatch(r"[0-9a-zA-Z]{5,40}", paper_id):
            self._send_json(400, {"error": "试卷 ID 不合法"})
            return
        try:
            page_text = fetch_page_text(f"{PAPER_BASE_URL}/paper/{paper_id}")
            payload = parse_paper_text(page_text)
        except ValueError as e:
            self._send_json(422, {"error": str(e)})
            return
        except Exception as e:  # noqa: BLE001
            self._send_json(400, {"error": f"抓取网页失败：{e}"})
            return
        self._send_json(200, payload)

    def _handle_fetch_url(self):
        req = self._read_body()
        if req is None:
            return
        url = (req.get("url") or "").strip()
        if not url:
            self._send_json(400, {"error": "请填写网页链接"})
            return
        payload = self._extract_paper_payload(req, url)
        if payload is not None:
            self._send_json(200, payload)

    def _llm_extract_agent(self, user_prompt: str, system_prompt: str = EXTRACT_SYSTEM) -> str:
        """默认路径：Agent SDK 针对性提示词单轮提取。"""
        from claude_agent_sdk import ClaudeAgentOptions, query

        cfg = self.default_cfg
        options = ClaudeAgentOptions(
            system_prompt=EXTRACT_SYSTEM,
            setting_sources=[],
            env={
                "ANTHROPIC_BASE_URL": cfg["base_url"],
                "ANTHROPIC_AUTH_TOKEN": cfg["api_key"],
                "ANTHROPIC_MODEL": cfg["model"],
            },
            model=cfg["model"],
            max_turns=1,
            allowed_tools=[],
        )

        async def drain() -> str:
            out = []
            async for message in query(prompt=user_prompt, options=options):
                if type(message).__name__ == "AssistantMessage":
                    for block in getattr(message, "content", None) or []:
                        text = getattr(block, "text", None)
                        if text:
                            out.append(text)
            return "".join(out)

        return asyncio.run(drain())

    def _llm_extract_openai(self, base_url: str, key: str, model: str,
                            user_prompt: str) -> str:
        """BYO 路径：OpenAI 兼容非流式提取。"""
        payload = {
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": EXTRACT_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
        }
        request = urllib.request.Request(
            base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(request, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return (data.get("choices", [{}])[0].get("message", {}) or {}).get("content") or ""

    # -- 解析/批改 -------------------------------------------------------------

    def _handle_review(self):
        user = self._require_user()
        if not user:
            return
        req = self._read_body()
        if req is None:
            return

        user_text = self._build_user_message(req)
        if not user_text:
            self._send_json(400, {"error": "题干和给定资料不能为空"})
            return

        # 落库钩子：流结束后保存记录；批改模式额外触发画像提炼
        self._after = (lambda acc, req=req, uid=user["id"]:
                       self._save_review(uid, req, acc))

        api = req.get("api") or {}
        if all(api.get(k) for k in ("baseUrl", "key", "model")):
            # 前端自带 OpenAI 兼容配置（个人 BYO 路径）
            self._stream_openai_compat(api["baseUrl"].rstrip("/"), api["key"], api["model"],
                                       [{"role": "system", "content": self.system_prompt},
                                        {"role": "user", "content": user_text}])
        elif all(self.default_cfg.get(k) for k in ("base_url", "api_key", "model")):
            # 服务端内置默认配置（Claude Agent SDK 路径）
            self._stream_agent(self.system_prompt, user_text)
        else:
            self._send_json(400, {"error": "服务未配置默认模型，请在页面设置中填入 API 信息，"
                                          "或在服务器 webapp/.env 中配置默认 AK"})

    # -- 业务：学习模式（多轮对话） --------------------------------------------

    def _handle_learn(self):
        user = self._require_user()
        if not user:
            return
        req = self._read_body()
        if req is None:
            return
        question = (req.get("question") or "").strip()
        material = (req.get("material") or "").strip()
        if not question and not material:
            self._send_json(400, {"error": "题干和给定资料不能为空"})
            return

        # 学生画像注入：历史沉淀的坑/进度/优势/偏好，教学时据此调整
        memories = store.list_memories(user["id"])
        if memories:
            lines = []
            for m in memories[:8]:
                label = MEMO_LABELS.get(m["kind"], "备注")
                lines.append("- 【%s】%s" % (label, m["content"]))
            profile_text = "\n".join(lines)
        else:
            profile_text = "（新学生，暂无画像，按完整流程从审题开始。）"

        # 一级记忆：近期练习摘要（滚动窗口），供教学衔接
        notes = store.list_recent_notes(user["id"], limit=5)
        if notes:
            recent_text = "\n".join(
                "- %s %s" % (time.strftime("%m-%d", time.localtime(n["created_at"])),
                             n["summary"])
                for n in notes
            )
        else:
            recent_text = "（暂无近期练习记录）"

        system_prompt = LEARN_SYSTEM.replace("{question}", question) \
                                    .replace("{material}", material) \
                                    .replace("{reference}", (req.get("reference") or "").strip()) \
                                    .replace("{profile}", profile_text) \
                                    .replace("{recent}", recent_text)

        # 前端传入完整对话历史 [{role, content}]；服务端无状态
        history = []
        for m in (req.get("history") or [])[-30:]:  # 限制轮数防膨胀
            role = m.get("role")
            content = (m.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                history.append({"role": role, "content": content})
        if not history:
            history = [{"role": "user", "content": "老师好，我们开始学这道题吧。"}]

        # 会话持久化：流开始前就创建/校验会话，id 随 SSE 首事件下发给前端，
        # 否则前端每轮都带空 id，服务端会重复建会话（历史只剩零散一两轮）。
        conv_id = (req.get("conversationId") or "").strip() or None
        user_turn = (req.get("userTurn") or "").strip()
        if conv_id and not store.get_conversation(user["id"], conv_id):
            conv_id = None  # 不属于该用户，按新会话处理
        if not conv_id:
            conv_id = store.create_conversation(
                user["id"], "learn", title=question[:40] or "未命名题目",
                question=question, material=material,
                reference=(req.get("reference") or "").strip(),
            )
        self._first_event = {"c": conv_id}

        # 流结束后的落库与画像提炼钩子
        self._after = (lambda acc, req=req, uid=user["id"], conv_id=conv_id, user_turn=user_turn:
                       self._save_learn(uid, req, conv_id, user_turn, acc))

        api = req.get("api") or {}
        if all(api.get(k) for k in ("baseUrl", "key", "model")):
            self._stream_openai_compat(api["baseUrl"].rstrip("/"), api["key"], api["model"],
                                       [{"role": "system", "content": system_prompt}] + history)
        elif all(self.default_cfg.get(k) for k in ("base_url", "api_key", "model")):
            transcript = "\n\n".join(
                ("学生：" if m["role"] == "user" else "（你上一轮的回复）") + m["content"]
                for m in history
            )
            transcript += "\n\n请以私教身份回复学生的最新消息。"
            self._stream_agent(system_prompt, transcript)
        else:
            self._send_json(400, {"error": "服务未配置默认模型，无法进入学习模式"})

    # -- 业务：用户消息 ------------------------------------------------------

    @staticmethod
    def _section(label: str, text: str) -> str:
        text = (text or "").strip()
        return f"\n【{label}】\n{text}\n" if text else ""

    def _build_user_message(self, req: dict) -> str:
        mode = req.get("mode") or "auto"
        question = (req.get("question") or "").strip()
        material = (req.get("material") or "").strip()
        reference = (req.get("reference") or "").strip()
        answer = (req.get("answer") or "").strip()

        if not question and not material:
            return ""
        if mode == "grade" and not answer:
            return "【题干】与【给定资料】已收到，但未提供考生作答，无法批改。请补充作答，或改为解析模式。"

        if mode == "analyze" or (mode == "auto" and not answer):
            directive = "请用申论技能对下面的题目做完整解析（无考生作答，不评分；题目没有分值时明确写不评分）。"
        else:
            directive = "请用申论技能批改下面的作答，按输出要求先教学讲解、后报档位与建议分。"

        parts = [directive]
        parts.append(self._section("题干", question))
        parts.append(self._section("给定资料", material))
        if reference:
            parts.append(self._section("参考答案", reference))
        if answer:
            parts.append(self._section("考生作答", answer))
        return "".join(parts)

    # -- 业务：Claude Agent SDK 路径 -----------------------------------------

    def _stream_agent(self, system_prompt: str, user_text: str):
        cfg = self.default_cfg
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        first = getattr(self, "_first_event", None)
        self._first_event = None
        if first:
            self.wfile.write(sse_event(first))
            self.wfile.flush()

        self._acc = ""  # 累积正文，流结束后交给 _after 落库

        def emit(obj):
            if obj.get("t"):
                self._acc += obj["t"]
            self.wfile.write(sse_event(obj))
            self.wfile.flush()

        try:
            from claude_agent_sdk import ClaudeAgentOptions, query
        except ImportError:
            emit({"e": "服务器未安装 claude-agent-sdk，无法使用内置默认模型"})
            return

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            setting_sources=[],  # 不加载任何用户/项目配置，纯净环境
            include_partial_messages=True,  # 逐字流式
            env={
                "ANTHROPIC_BASE_URL": cfg["base_url"],
                "ANTHROPIC_AUTH_TOKEN": cfg["api_key"],
                "ANTHROPIC_MODEL": cfg["model"],
            },
            model=cfg["model"],
            max_turns=1,  # 单轮问答，禁止工具循环
            allowed_tools=[],
        )

        async def drain():
            async for message in query(prompt=user_text, options=options):
                cls = type(message).__name__
                if cls == "AssistantMessage":
                    for block in getattr(message, "content", None) or []:
                        text = getattr(block, "text", None)
                        if text:
                            emit({"t": text})
                elif cls == "ResultMessage":
                    if getattr(message, "is_error", False):
                        emit({"e": getattr(message, "result", "") or "上游返回错误"})
            emit({"done": True})

        try:
            asyncio.run(drain())
        except Exception as e:  # noqa: BLE001 — 边界处统一转为 SSE 错误事件
            emit({"e": f"Agent 调用失败：{e}"})
        finally:
            self._run_after()

    # -- 业务：OpenAI 兼容路径（前端自带配置时使用） ---------------------------

    def _stream_openai_compat(self, base_url: str, key: str, model: str, messages: list):
        url = base_url + "/chat/completions"
        payload = {
            "model": model,
            "stream": True,
            "temperature": 0.4,
            "messages": messages,
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
            method="POST",
        )

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        first = getattr(self, "_first_event", None)
        self._first_event = None
        if first:
            self.wfile.write(sse_event(first))
            self.wfile.flush()

        self._acc = ""  # 累积正文，流结束后交给 _after 落库

        def emit(obj):
            if obj.get("t"):
                self._acc += obj["t"]
            self.wfile.write(sse_event(obj))
            self.wfile.flush()

        try:
            with urllib.request.urlopen(request, timeout=300) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    delta = (
                        chunk.get("choices", [{}])[0].get("delta", {}).get("content")
                        or ""
                    )
                    if delta:
                        emit({"t": delta})
            emit({"done": True})
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:300]
            emit({"e": f"上游 API 返回 {e.code}：{detail}"})
        except Exception as e:  # noqa: BLE001
            emit({"e": f"请求上游 API 失败：{e}"})
        finally:
            self._run_after()

    # -- 落库与画像提炼 ---------------------------------------------------------

    def _run_after(self):
        """流结束后统一调用 _after 钩子（落库/提炼）。"""
        after = getattr(self, "_after", None)
        self._after = None
        if after:
            try:
                after(self._acc)
            except store.Full as e:
                print("[save] %s" % e, file=sys.stderr)
            except Exception as e:  # noqa: BLE001
                print("[save] 落库失败：%s" % e, file=sys.stderr)

    def _save_review(self, uid: int, req: dict, output: str):
        """解析/批改完成后落库，并写一条一级摘要（不强制改二级画像）。"""
        if not (output or "").strip():
            return
        question = (req.get("question") or "").strip()
        mode = req.get("mode") or "analyze"
        conv_id = store.create_conversation(
            uid, "review", title=question[:40] or "未命名题目", question=question,
            mode=mode, material=(req.get("material") or "").strip(),
            reference=(req.get("reference") or "").strip(),
            answer=(req.get("answer") or "").strip(), output=output,
        )
        if mode == "grade" and (req.get("answer") or "").strip():
            transcript = ("题干：%s\n考生作答：%s\n批改结论（节选）：%s"
                          % (question, (req.get("answer") or "")[:1500], output[:2000]))
        else:
            # 解析模式也提炼：题干与讲什么题型/身份相关，是进度与背景的重要来源
            transcript = ("学生请求解析的题干：%s\n解析讲到的考点（节选）：%s"
                          % (question, output[:1500]))
        # 解析/批改只写一级摘要，不强制改二级画像（避免高频重写）
        self._spawn_extraction(uid, transcript, conv_id=conv_id, force_l2=False)

    def _save_learn(self, uid: int, req: dict, conv_id, user_turn: str, reply: str):
        """会话已在 _handle_learn 中创建；这里只追加本轮消息并触发提炼。"""
        if not conv_id or not (user_turn or reply or "").strip():
            return
        if user_turn:
            store.append_message(conv_id, "user", user_turn)
        if reply:
            store.append_message(conv_id, "assistant", reply)
        self._spawn_extraction_from_conv(uid, conv_id)

    # -- 画像提炼（后台线程，失败只记日志不影响主流程） --------------------------

    def _spawn_extraction_from_conv(self, uid: int, conv_id: str, force: bool = False) -> None:
        msgs = store.get_messages(conv_id)
        n = len(msgs)
        if force:
            pass  # 退出学习等场景：只要有内容就立即提炼
        elif n < 3:
            return  # 消息数 1,3,5,7…（开场为老师）；第 2 回合起每轮提炼
        tail = msgs[-6:]
        transcript = "\n\n".join(("学生：" if m["role"] == "user" else "老师：") + m["content"]
                                 for m in tail)
        self._spawn_extraction(uid, transcript, conv_id=conv_id, force_l2=force)

    def _spawn_extraction(self, uid: int, transcript: str, conv_id=None, force_l2=False):
        if not all(self.default_cfg.get(k) for k in ("base_url", "api_key", "model")):
            return  # 提炼仅走服务端默认模型，BYO 用户跳过
        threading.Thread(target=self._extract_memories,
                         args=(uid, transcript, conv_id, force_l2), daemon=True).start()

    def _extract_memories(self, uid: int, transcript: str, conv_id=None, force_l2=False):
        """两级提炼：一级摘要每轮必写（滚动 ≤10 条）；
        二级画像只在趋势明确时更新（每 3 轮或退出/批改强制）。"""
        try:
            memories = store.list_memories(uid)
            notes = store.list_recent_notes(uid)
            mem_json = json.dumps(
                [{"id": m["id"], "kind": m["kind"], "content": m["content"]}
                 for m in memories],
                ensure_ascii=False,
            )
            recent_json = json.dumps(
                [{"time": time.strftime("%m-%d %H:%M", time.localtime(n["created_at"])),
                  "summary": n["summary"]} for n in notes],
                ensure_ascii=False,
            )
            user_prompt = MEMO_SYSTEM.replace("{memories}", mem_json) \
                                     .replace("{recent}", recent_json or "（暂无）") \
                                     .replace("{transcript}", transcript[:6000])
            raw = self._llm_extract_agent(user_prompt, system_prompt=MEMO_SYSTEM).strip()
            lo, hi = raw.find("{"), raw.rfind("}")
            if lo < 0 or hi <= lo:
                return
            data = json.loads(raw[lo:hi + 1])
            # 一级：本轮摘要进滚动窗口（满了自动挤掉最旧）
            store.add_recent_note(uid, conv_id, data.get("note") or "")
            # 二级：节流整体重写——每 3 轮一次；退出/批改强制。
            # 空数组表示"维持原样"，不清空画像。
            with _L2_LOCK:
                _L2_COUNTER[uid] = _L2_COUNTER.get(uid, 0) + 1
                due = force_l2 or _L2_COUNTER[uid] % L2_EVERY == 0
            profile = data.get("profile")
            if due and isinstance(profile, list) and profile:
                store.replace_memories(uid, profile)
        except Exception as e:  # noqa: BLE001
            print("[memo] 提炼失败：%s" % e, file=sys.stderr)

    # -- 静态文件 ------------------------------------------------------------

    def _serve_static(self, name: str, ctype: str):
        path = STATIC_DIR / name
        if not path.exists():
            self._send_json(404, {"error": "not found"})
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")  # 前端迭代期禁缓存
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def lan_addresses():
    """枚举本机局域网地址，方便手机直接访问。"""
    addrs = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in addrs:
                addrs.append(ip)
    except OSError:
        pass
    return addrs


def main():
    parser = argparse.ArgumentParser(description="申论复盘 Web 版后端")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--skill-dir", type=Path, default=DEFAULT_SKILL_DIR)
    parser.add_argument("--vacuum", action="store_true",
                        help="压缩历史库并退出（运维命令）")
    args = parser.parse_args()

    data_dir = os.environ.get("SHENLUN_DATA_DIR") or str(Path(__file__).resolve().parent / "data")
    if args.vacuum:
        store.init(data_dir)
        print("清理完成，库大小: %.1f MB" % store.vacuum())
        sys.exit(0)

    if not args.skill_dir.exists():
        sys.exit(f"未找到 Skill 目录: {args.skill_dir}")

    Handler.system_prompt = build_system_prompt(args.skill_dir)
    print(f"已加载方法论 prompt：{len(Handler.system_prompt)} 字符")

    Handler.default_cfg = agent_cfg_from_env(load_dotenv(ENV_FILE))
    ready = all(Handler.default_cfg.get(k) for k in ("base_url", "api_key", "model"))
    print(f"内置默认模型：{'已配置 (' + Handler.default_cfg.get('model', '') + ')' if ready else '未配置（需填写 webapp/.env 或用页面设置）'}")

    # 持久层初始化 + 配置覆盖
    env = load_dotenv(ENV_FILE)
    store.configure(
        days=int(env.get("SHENLUN_HISTORY_DAYS") or 180),
        max_mb=int(env.get("SHENLUN_HISTORY_MAX_MB") or 512),
    )
    Handler.invite_code = env.get("SHENLUN_INVITE_CODE") or ""
    store.init(data_dir)
    summary = store.enforce_limits()
    print(f"数据目录：{data_dir}（库 {store.db_size_mb():.1f} MB）"
          + (f"，启动清理：{summary}" if any(summary.values()) else ""))

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"本机访问:   http://localhost:{args.port}")
    for ip in lan_addresses():
        print(f"手机访问:   http://{ip}:{args.port}  （需与手机同一 Wi-Fi）")
    print("按 Ctrl+C 退出")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
