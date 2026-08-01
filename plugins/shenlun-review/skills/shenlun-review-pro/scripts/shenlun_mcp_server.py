#!/usr/bin/env python3
"""Dependency-free, cross-client stdio MCP bridge for Shenlun grading.

The MCP client submits a JSON object as tool arguments. This bridge serializes
that object itself and invokes only the fixed, deterministic grading runner.
Natural-language content is never embedded in generated code or shell syntax.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


SERVER_NAME = "shenlun_review_mcp"
SERVER_VERSION = "1.3.0"
PROTOCOL_VERSION = "2024-11-05"
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
TIMEOUT_SECONDS = 70


COMMON_PROPERTIES = {
    "question_text": {
        "type": "string",
        "minLength": 1,
        "description": "完整原题题干，保持原文，不为命中题型而改写。",
    },
    "full_score": {
        "type": "number",
        "exclusiveMinimum": 0,
        "description": "本题题面满分。",
    },
    "min_length": {
        "type": "integer",
        "minimum": 0,
        "description": "题面最低字数；未规定时省略。",
    },
    "max_length": {
        "type": "integer",
        "minimum": 1,
        "description": "题面最高字数；未规定时省略。",
    },
    "material_text": {
        "type": "string",
        "minLength": 1,
        "description": "本题对应的完整材料。",
    },
    "user_answer": {
        "type": "string",
        "minLength": 1,
        "description": "考生完整作答；中文引号、换行和反斜杠直接作为字符串提交。",
    },
    "points": {
        "type": "array",
        "minItems": 1,
        "items": {
            "type": "object",
            "properties": {
                "material_evidence": {
                    "description": (
                        "材料连续原文。优先使用独立片段数组；数组元素不要求"
                        "材料顺序。单个字符串中的省略号仅表示按原文顺序省略"
                        "中间内容，跨段、跨人物或顺序不确定时必须拆成数组元素。"
                    ),
                    "oneOf": [
                        {"type": "string", "minLength": 1},
                        {
                            "type": "array",
                            "minItems": 1,
                            "items": {"type": "string", "minLength": 1},
                        },
                    ],
                }
            },
            "additionalProperties": True,
        },
        "description": "材料点池及考生证据映射；材料证据按连续原文片段提交。",
    },
    "point_pool_review": {
        "type": "object",
        "description": "点池查漏、拆并、背景和例子边界复核。",
    },
    "holistic_review": {
        "type": "object",
        "description": "归纳概括、综合分析或提出对策题的整体审核。",
    },
    "application_review": {
        "type": "object",
        "description": "贯彻执行题的应用文整体审核。",
    },
    "essay_review": {
        "type": "object",
        "description": "申发论述题的整体审核。",
    },
}


TOOLS = [
    {
        "name": "shenlun_grade_once",
        "title": "申论正式评分",
        "description": (
            "普通申论批改唯一评分入口。直接提交由题干、材料、考生作答、"
            "点池和对应题型审核组成的 JSON 对象；工具自动安全序列化并"
            "调用确定性评分引擎。不得改用 Bash 或临时脚本。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": COMMON_PROPERTIES,
            "required": [
                "question_text",
                "full_score",
                "material_text",
                "user_answer",
                "points",
                "point_pool_review",
            ],
            "anyOf": [
                {"required": ["holistic_review"]},
                {"required": ["application_review"]},
                {"required": ["essay_review"]},
            ],
            "additionalProperties": True,
        },
        "outputSchema": {
            "type": "object",
            "additionalProperties": True,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "shenlun_validate_user_output",
        "title": "申论批改成稿校验",
        "description": (
            "校验申论批改最终正文。提交完整正文和 shenlun_grade_once 原样"
            "返回的 output_validation_context；成功后只发送 approved_text。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "minLength": 1,
                    "description": "准备发给用户的完整批改正文。",
                },
                "output_validation_context": {
                    "type": "object",
                    "description": "评分工具返回的同名对象，必须原样复用。",
                },
                "reference_answer_text": {
                    "type": "string",
                    "description": "仅在正文包含技能生成的完整示范答案时提交。",
                },
            },
            "required": ["text", "output_validation_context"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "additionalProperties": True,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
]


def find_skill_dir():
    configured = os.environ.get("SHENLUN_SKILL_DIR")
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    # When the server is bundled inside the Skill, this is the canonical and
    # most reliable location for every supported client.
    candidates.append(Path(__file__).resolve().parents[1])
    candidates.extend(
        [
            Path.home() / ".workbuddy" / "skills" / "申论复盘一体版",
            Path.home() / ".workbuddy" / "skills" / "shenlun-review-pro",
            Path.home() / ".codex" / "skills" / "shenlun-review-pro",
            Path.home() / ".codex" / "skills" / "申论复盘一体版",
            Path.home() / ".agents" / "skills" / "shenlun-review-pro",
            Path.home() / ".agents" / "skills" / "申论复盘一体版",
            Path.home() / ".claude" / "skills" / "shenlun-review-pro",
            Path.home() / ".claude" / "skills" / "申论复盘一体版",
            Path.home() / ".trae" / "skills" / "shenlun-review-pro",
            Path.home() / ".trae" / "skills" / "申论复盘一体版",
            Path.home() / ".trae-cn" / "skills" / "shenlun-review-pro",
            Path.home() / ".trae-cn" / "skills" / "申论复盘一体版",
        ]
    )
    seen = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve())
        except OSError:
            key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        runner = candidate / "scripts" / "safe_review_runner.py"
        if runner.is_file():
            return candidate
    return None


def parse_engine_output(stdout, stderr):
    for raw in (stdout, stderr):
        if not raw or not raw.strip():
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def call_fixed_runner(command, arguments):
    if not isinstance(arguments, dict):
        return None, {
            "code": "invalid_arguments",
            "message": "工具参数必须是 JSON 对象。",
        }

    encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        return None, {
            "code": "payload_too_large",
            "message": "输入超过 8 MiB，请只提交当前题目的题干、材料和作答。",
        }

    skill_dir = find_skill_dir()
    if skill_dir is None:
        return None, {
            "code": "skill_not_found",
            "message": "未找到“申论复盘一体版”技能目录，请安装完整插件或设置 SHENLUN_SKILL_DIR。",
        }

    runner = skill_dir / "scripts" / "safe_review_runner.py"
    try:
        completed = subprocess.run(
            [sys.executable, str(runner), command],
            input=encoded,
            text=True,
            capture_output=True,
            check=False,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return None, {
            "code": "timeout",
            "message": "申论评分入口超过 70 秒，按技能止损规则只允许统一修正后重试一次。",
        }
    except OSError as exc:
        return None, {
            "code": "runner_start_failed",
            "message": f"无法启动固定评分入口：{type(exc).__name__}。",
        }

    parsed = parse_engine_output(completed.stdout, completed.stderr)
    if completed.returncode != 0:
        return None, {
            "code": "grading_rejected",
            "message": "固定评分入口拒绝了本次输入；按完整错误列表统一修正一次。",
            "engine_error": parsed or {
                "exit_code": completed.returncode,
                "message": "入口未返回可解析的 JSON 错误。",
            },
        }
    if parsed is None:
        return None, {
            "code": "invalid_engine_response",
            "message": "固定评分入口成功退出，但未返回 JSON 对象。",
        }
    return parsed, None


def tool_result(payload, is_error=False):
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False),
            }
        ],
        "structuredContent": payload,
        "isError": bool(is_error),
    }


def handle_tool_call(params):
    if not isinstance(params, dict):
        return tool_result(
            {"ok": False, "error": {"code": "invalid_request", "message": "tools/call 参数无效。"}},
            True,
        )
    name = params.get("name")
    arguments = params.get("arguments", {})
    command_by_tool = {
        "shenlun_grade_once": "grade-once",
        "shenlun_validate_user_output": "validate-user-output",
    }
    command = command_by_tool.get(name)
    if command is None:
        return tool_result(
            {"ok": False, "error": {"code": "unknown_tool", "message": f"未知工具：{name}"}},
            True,
        )

    result, error = call_fixed_runner(command, arguments)
    if error is not None:
        return tool_result({"ok": False, "error": error}, True)
    return tool_result(result, False)


def dispatch(request):
    method = request.get("method")
    params = request.get("params", {})
    if method == "initialize":
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        return {
            "protocolVersion": requested or PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "申论普通批改使用 shenlun_grade_once，成稿使用 "
                "shenlun_validate_user_output；不要通过终端调用评分脚本。"
            ),
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        return handle_tool_call(params)
    if method == "resources/list":
        return {"resources": []}
    if method == "prompts/list":
        return {"prompts": []}
    if method == "shutdown":
        return None
    raise KeyError(method)


def emit(message):
    sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main():
    for raw_line in sys.stdin.buffer:
        if not raw_line.strip():
            continue
        request_id = None
        try:
            request = json.loads(raw_line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            request_id = request.get("id")
            method = request.get("method")
            if not isinstance(method, str):
                raise ValueError("method must be a string")

            if request_id is None:
                if method == "notifications/initialized":
                    continue
                continue

            result = dispatch(request)
            emit({"jsonrpc": "2.0", "id": request_id, "result": result})
        except KeyError as exc:
            if request_id is not None:
                emit(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32601,
                            "message": f"Method not found: {exc.args[0]}",
                        },
                    }
                )
        except (json.JSONDecodeError, UnicodeError, ValueError) as exc:
            emit(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32600,
                        "message": f"Invalid Request: {type(exc).__name__}",
                    },
                }
            )
        except Exception as exc:
            if request_id is not None:
                emit(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32603,
                            "message": f"Internal error: {type(exc).__name__}",
                        },
                    }
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())
