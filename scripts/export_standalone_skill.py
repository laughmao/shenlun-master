#!/usr/bin/env python3
"""Export the canonical Shenlun Skill as a channel-neutral standalone package."""

from __future__ import annotations

import argparse
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_SKILL = ROOT / "skills" / "shenlun-review-pro"
DEFAULT_DESTINATION = ROOT.parent / "shenlun-review-skill"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出渠道无关的申论独立 Skill")
    parser.add_argument(
        "--destination",
        type=Path,
        default=DEFAULT_DESTINATION,
        help=f"导出目录，默认 {DEFAULT_DESTINATION}",
    )
    return parser.parse_args()


def ignore_development_files(_directory: str, names: list[str]) -> set[str]:
    ignored = {name for name in names if name == "__pycache__" or name.endswith(".pyc")}
    if "tests" in names:
        ignored.add("tests")
    return ignored


def validate_destination(destination: Path) -> None:
    if destination == ROOT or ROOT in destination.parents or destination in ROOT.parents:
        raise SystemExit("导出目录必须位于主项目之外，且不能是主项目的上级目录")
    if destination == SOURCE_SKILL:
        raise SystemExit("不能覆盖主项目中的 Skill 源码")


def main() -> int:
    args = parse_args()
    destination = args.destination.expanduser().resolve()
    validate_destination(destination)
    if not (SOURCE_SKILL / "SKILL.md").is_file():
        raise SystemExit(f"主 Skill 不完整：缺少 {SOURCE_SKILL / 'SKILL.md'}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    backup: Path | None = None
    try:
        shutil.copytree(
            SOURCE_SKILL,
            staging,
            dirs_exist_ok=True,
            ignore=ignore_development_files,
        )
        if destination.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = destination.with_name(f"{destination.name}.backup.{stamp}")
            destination.rename(backup)
        staging.rename(destination)
    except Exception:
        if backup is not None and backup.exists() and not destination.exists():
            backup.rename(destination)
        if staging.exists():
            shutil.rmtree(staging)
        raise

    print("申论独立 Skill 导出成功。")
    print(f"源码：{SOURCE_SKILL}")
    print(f"发布目录：{destination}")
    if backup is not None:
        print(f"旧目录备份：{backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
