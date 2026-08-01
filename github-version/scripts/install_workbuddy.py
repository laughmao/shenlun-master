#!/usr/bin/env python3
"""Install the self-contained Shenlun plugin into WorkBuddy."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_MARKETPLACE = ROOT / "integrations" / "workbuddy"
MARKETPLACE_NAME = "shenlun-review-local"
PLUGIN_KEY = "shenlun-review-tools@shenlun-review-local"
LEGACY_PLUGIN_KEY = "shenlun-review-tools@gongkao-review-local"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="安装申论复盘一体版 WorkBuddy 完整插件")
    parser.add_argument(
        "--workbuddy-home",
        type=Path,
        default=Path.home() / ".workbuddy",
        help="WorkBuddy 数据目录，默认 ~/.workbuddy",
    )
    return parser.parse_args()


def load_object(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} 必须是 JSON 对象")
    return payload


def write_json(path: Path, payload: dict, stamp: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(f"{path.name}.bak.{stamp}"))
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def replace_marketplace(source: Path, destination: Path, backup_root: Path, stamp: str) -> None:
    if destination.exists():
        backup_root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(destination), str(backup_root / f"{MARKETPLACE_NAME}.{stamp}"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def main() -> int:
    args = parse_args()
    workbuddy_home = args.workbuddy_home.expanduser().resolve()
    if not SOURCE_MARKETPLACE.is_dir():
        raise SystemExit(f"安装包不完整：缺少 {SOURCE_MARKETPLACE}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = workbuddy_home / "plugins" / "marketplaces" / MARKETPLACE_NAME
    replace_marketplace(
        SOURCE_MARKETPLACE,
        destination,
        workbuddy_home / "backups" / "shenlun-review",
        stamp,
    )

    known_path = workbuddy_home / "plugins" / "known_marketplaces.json"
    known = load_object(known_path, {})
    known[MARKETPLACE_NAME] = {
        "type": "directory",
        "source": {"source": "directory", "path": str(destination)},
        "installLocation": str(destination),
        "description": f"Marketplace from {destination}",
        "lastUpdated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "autoUpdate": False,
    }
    write_json(known_path, known, stamp)

    settings_path = workbuddy_home / "settings.json"
    settings = load_object(settings_path, {})
    enabled = settings.setdefault("enabledPlugins", {})
    if not isinstance(enabled, dict):
        raise ValueError(f"{settings_path} 的 enabledPlugins 必须是对象")
    if LEGACY_PLUGIN_KEY in enabled:
        enabled[LEGACY_PLUGIN_KEY] = False
    enabled[PLUGIN_KEY] = True
    write_json(settings_path, settings, stamp)

    print("申论复盘一体版 WorkBuddy 完整插件安装成功。")
    print(f"插件目录：{destination}")
    print("请重启 WorkBuddy 或新建任务，以加载 Skill、MCP 和 Hook。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
