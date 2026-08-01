# -*- coding: utf-8 -*-
"""Tests for the WorkBuddy one-command installer."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_workbuddy.py"


class WorkBuddyInstallerTests(unittest.TestCase):
    def test_installer_preserves_settings_and_registers_complete_plugin(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workbuddy_home = Path(temp_dir) / ".workbuddy"
            workbuddy_home.mkdir()
            (workbuddy_home / "settings.json").write_text(
                json.dumps(
                    {
                        "theme": "dark",
                        "enabledPlugins": {
                            "another-plugin@example": True,
                            "shenlun-review-tools@gongkao-review-local": True,
                        },
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(INSTALLER),
                    "--workbuddy-home",
                    str(workbuddy_home),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

            settings = json.loads((workbuddy_home / "settings.json").read_text())
            self.assertEqual(settings["theme"], "dark")
            self.assertTrue(settings["enabledPlugins"]["another-plugin@example"])
            self.assertFalse(
                settings["enabledPlugins"]["shenlun-review-tools@gongkao-review-local"]
            )
            self.assertTrue(
                settings["enabledPlugins"]["shenlun-review-tools@shenlun-review-local"]
            )

            markets = json.loads(
                (workbuddy_home / "plugins" / "known_marketplaces.json").read_text()
            )
            installed = Path(markets["shenlun-review-local"]["installLocation"])
            plugin = installed / "plugins" / "shenlun-review-tools"
            self.assertTrue((plugin / ".codebuddy-plugin" / "plugin.json").is_file())
            self.assertTrue(
                (plugin / "skills" / "shenlun-review-pro" / "scripts" / "shenlun_mcp_server.py").is_file()
            )

    def test_reinstall_creates_recoverable_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workbuddy_home = Path(temp_dir) / ".workbuddy"
            command = [
                sys.executable,
                str(INSTALLER),
                "--workbuddy-home",
                str(workbuddy_home),
            ]
            self.assertEqual(subprocess.run(command, check=False).returncode, 0)
            self.assertEqual(subprocess.run(command, check=False).returncode, 0)
            backups = workbuddy_home / "backups" / "shenlun-review"
            self.assertTrue(any(backups.iterdir()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
