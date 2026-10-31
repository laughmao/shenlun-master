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
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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

## 输出格式
聊天体，短段落；可用少量加粗标关键词；禁表格、禁编号刷屏、禁整段引用材料；
每轮结尾一个问题。开场（学生还没说话时）先热情地用一两句话引入这道题的场景，
然后提出第一个审题问题。"""

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


class Handler(BaseHTTPRequestHandler):
    server_version = "ShenlunWeb/0.2"
    system_prompt = ""   # 启动时注入
    default_cfg = {}     # 启动时从 .env 注入：base_url / api_key / model

    # -- 基础设施 -----------------------------------------------------------

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("[api] %s\n" % (fmt % args))

    def _send_json(self, status: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
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
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/api/fetch-url":
            self._handle_fetch_url()
        elif self.path == "/api/grab-paper":
            self._handle_grab_paper()
        elif self.path == "/api/review":
            self._handle_review()
        elif self.path == "/api/learn":
            self._handle_learn()
        else:
            self._send_json(404, {"error": "not found"})

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

    def _llm_extract_agent(self, user_prompt: str) -> str:
        """默认路径：Agent SDK 非流式单轮提取。"""
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
        req = self._read_body()
        if req is None:
            return

        user_text = self._build_user_message(req)
        if not user_text:
            self._send_json(400, {"error": "题干和给定资料不能为空"})
            return

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
        req = self._read_body()
        if req is None:
            return
        question = (req.get("question") or "").strip()
        material = (req.get("material") or "").strip()
        if not question and not material:
            self._send_json(400, {"error": "题干和给定资料不能为空"})
            return

        system_prompt = LEARN_SYSTEM.replace("{question}", question) \
                                    .replace("{material}", material) \
                                    .replace("{reference}", (req.get("reference") or "").strip())

        # 前端传入完整对话历史 [{role, content}]；服务端无状态
        history = []
        for m in (req.get("history") or [])[-30:]:  # 限制轮数防膨胀
            role = m.get("role")
            content = (m.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                history.append({"role": role, "content": content})
        if not history:
            history = [{"role": "user", "content": "老师好，我们开始学这道题吧。"}]

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

        def emit(obj):
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

        def emit(obj):
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
            detail = e.read().decode("utf-8", errors="replace")[:500]
            emit({"e": f"上游 API 返回 {e.code}：{detail}"})
        except Exception as e:  # noqa: BLE001
            emit({"e": f"请求上游 API 失败：{e}"})

    # -- 静态文件 ------------------------------------------------------------

    def _serve_static(self, name: str, ctype: str):
        path = STATIC_DIR / name
        if not path.exists():
            self._send_json(404, {"error": "not found"})
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
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
    args = parser.parse_args()

    if not args.skill_dir.exists():
        sys.exit(f"未找到 Skill 目录: {args.skill_dir}")

    Handler.system_prompt = build_system_prompt(args.skill_dir)
    print(f"已加载方法论 prompt：{len(Handler.system_prompt)} 字符")

    Handler.default_cfg = agent_cfg_from_env(load_dotenv(ENV_FILE))
    ready = all(Handler.default_cfg.get(k) for k in ("base_url", "api_key", "model"))
    print(f"内置默认模型：{'已配置 (' + Handler.default_cfg.get('model', '') + ')' if ready else '未配置（需填写 webapp/.env 或用页面设置）'}")

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
