#!/usr/bin/env python3
"""Build self-contained client packages from lightweight integration templates."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
SOURCE_SKILL = ROOT / "skills" / "shenlun-review-pro"
WORKBUDDY_TEMPLATE = ROOT / "integrations" / "workbuddy"
CODEX_TEMPLATE = ROOT / "integrations" / "codex" / "shenlun-review"
CLAUDE_TEMPLATE = ROOT / "integrations" / "claude-code" / "shenlun-review"
DEFAULT_OUTPUT = ROOT / "dist"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建申论客户端完整安装包")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"构建输出目录，默认 {DEFAULT_OUTPUT}",
    )
    return parser.parse_args()


def ignore_package_noise(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name == ".DS_Store" or name == "__pycache__" or name.endswith(".pyc")
    }


def ignore_skill_development_files(directory: str, names: list[str]) -> set[str]:
    ignored = ignore_package_noise(directory, names)
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
            WORKBUDDY_TEMPLATE / ".codebuddy-plugin" / "marketplace.json"
        )["plugins"][0]["version"] == VERSION,
        "WorkBuddy plugin": read_json(
            WORKBUDDY_TEMPLATE
            / "plugins"
            / "shenlun-review-tools"
            / ".codebuddy-plugin"
            / "plugin.json"
        )["version"] == VERSION,
        "Codex plugin": read_json(
            CODEX_TEMPLATE / ".codex-plugin" / "plugin.json"
        )["version"] == VERSION,
        "Claude Code plugin": read_json(
            CLAUDE_TEMPLATE / ".claude-plugin" / "plugin.json"
        )["version"] == VERSION,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("版本不一致：" + "、".join(failed))


def validate_output(output: Path) -> Path:
    output = output.expanduser().resolve()
    protected_roots = (
        ROOT / "integrations",
        ROOT / "skills",
        ROOT / "scripts",
        ROOT / "tests",
    )
    if output == ROOT or any(
        output == protected or protected in output.parents
        for protected in protected_roots
    ):
        raise SystemExit("构建输出目录不能覆盖或进入源码目录")
    return output


def replace_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, ignore=ignore_package_noise)


def copy_skill(destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        SOURCE_SKILL,
        destination,
        ignore=ignore_skill_development_files,
    )


def build_workbuddy(destination: Path) -> Path:
    replace_tree(WORKBUDDY_TEMPLATE, destination)
    copy_skill(
        destination
        / "plugins"
        / "shenlun-review-tools"
        / "skills"
        / "shenlun-review-pro"
    )
    return destination


def build_plugin(template: Path, destination: Path) -> Path:
    replace_tree(template, destination)
    copy_skill(destination / "skills" / "shenlun-review-pro")
    return destination


def build_all(output: Path) -> tuple[Path, Path, Path]:
    validate_versions()
    output = validate_output(output)
    packages = (
        build_workbuddy(output / "workbuddy"),
        build_plugin(CODEX_TEMPLATE, output / "codex" / "shenlun-review"),
        build_plugin(
            CLAUDE_TEMPLATE,
            output / "claude-code" / "shenlun-review",
        ),
    )
    return packages


def main() -> int:
    args = parse_args()
    for package in build_all(args.output):
        print(f"built {package}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
