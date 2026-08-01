#!/usr/bin/env python3
"""Build self-contained client integrations from the canonical Shenlun Skill."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
SOURCE_SKILL = PROJECT_ROOT / "redskill-version"
TARGET_SKILLS = (
    ROOT / "skills" / "shenlun-review-pro",
    ROOT / "integrations" / "workbuddy" / "plugins" / "shenlun-review-tools" / "skills" / "shenlun-review-pro",
    ROOT / "integrations" / "codex" / "shenlun-review" / "skills" / "shenlun-review-pro",
    ROOT / "integrations" / "claude-code" / "shenlun-review" / "skills" / "shenlun-review-pro",
)


def ignore_generated(directory: str, names: list[str]) -> set[str]:
    ignored = {name for name in names if name == "__pycache__" or name.endswith(".pyc")}
    if Path(directory).resolve() == SOURCE_SKILL.resolve() and "tests" in names:
        ignored.add("tests")
    return ignored


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_versions() -> None:
    skill_text = (SOURCE_SKILL / "SKILL.md").read_text(encoding="utf-8")
    engine_text = (SOURCE_SKILL / "scripts" / "review_engine.py").read_text(encoding="utf-8")
    server_text = (SOURCE_SKILL / "scripts" / "shenlun_mcp_server.py").read_text(encoding="utf-8")
    expected = f'"{VERSION}"'
    checks = {
        "SKILL.md": f"Skill v{VERSION}" in skill_text,
        "review_engine.py": f"ENGINE_VERSION = {expected}" in engine_text,
        "shenlun_mcp_server.py": f"SERVER_VERSION = {expected}" in server_text,
        "WorkBuddy marketplace": read_json(
            ROOT / "integrations" / "workbuddy" / ".codebuddy-plugin" / "marketplace.json"
        )["plugins"][0]["version"] == VERSION,
        "WorkBuddy plugin": read_json(
            ROOT / "integrations" / "workbuddy" / "plugins" / "shenlun-review-tools" / ".codebuddy-plugin" / "plugin.json"
        )["version"] == VERSION,
        "Codex plugin": read_json(
            ROOT / "integrations" / "codex" / "shenlun-review" / ".codex-plugin" / "plugin.json"
        )["version"] == VERSION,
        "Claude Code plugin": read_json(
            ROOT / "integrations" / "claude-code" / "shenlun-review" / ".claude-plugin" / "plugin.json"
        )["version"] == VERSION,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("版本不一致：" + "、".join(failed))


def copy_skill(destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SOURCE_SKILL, destination, ignore=ignore_generated)


def main() -> int:
    validate_versions()
    for destination in TARGET_SKILLS:
        copy_skill(destination)
        print(f"built {destination.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
