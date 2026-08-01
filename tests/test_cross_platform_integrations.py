# -*- coding: utf-8 -*-
"""Cross-client packaging tests for the standalone Shenlun project."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
SKILL = ROOT / "skills" / "shenlun-review-pro"
SERVER = SKILL / "scripts" / "shenlun_mcp_server.py"
GUARD = SKILL / "scripts" / "pretooluse_guard.py"
GENERATED_SKILLS = (
    DIST / "workbuddy" / "plugins" / "shenlun-review-tools" / "skills" / "shenlun-review-pro",
    DIST / "codex" / "shenlun-review" / "skills" / "shenlun-review-pro",
    DIST / "claude-code" / "shenlun-review" / "skills" / "shenlun-review-pro",
)

sys.path.insert(0, str(SKILL / "tests"))
from test_v0380_behavior import small_payload


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def skill_hashes(path: Path) -> dict[str, str]:
    result = {}
    for file_path in sorted(path.rglob("*")):
        relative = file_path.relative_to(path)
        if not file_path.is_file() or "tests" in relative.parts or "__pycache__" in relative.parts:
            continue
        if file_path.suffix == ".pyc":
            continue
        result[str(relative)] = hashlib.sha256(file_path.read_bytes()).hexdigest()
    return result


class CrossPlatformIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build_integrations.py")],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr or completed.stdout)

    def test_generated_plugins_contain_the_exact_canonical_skill(self):
        expected = skill_hashes(SKILL)
        self.assertGreater(len(expected), 150)
        for generated in GENERATED_SKILLS:
            self.assertEqual(skill_hashes(generated), expected, generated)

    def test_all_manifests_use_the_release_version(self):
        manifests = (
            DIST / "workbuddy" / "plugins" / "shenlun-review-tools" / ".codebuddy-plugin" / "plugin.json",
            DIST / "codex" / "shenlun-review" / ".codex-plugin" / "plugin.json",
            DIST / "claude-code" / "shenlun-review" / ".claude-plugin" / "plugin.json",
        )
        for manifest in manifests:
            self.assertEqual(load_json(manifest)["version"], VERSION, manifest)

    def test_integration_templates_do_not_duplicate_the_canonical_skill(self):
        generated_paths = (
            ROOT / "integrations" / "workbuddy" / "plugins" / "shenlun-review-tools" / "skills",
            ROOT / "integrations" / "codex" / "shenlun-review" / "skills",
            ROOT / "integrations" / "claude-code" / "shenlun-review" / "skills",
        )
        for path in generated_paths:
            self.assertFalse(path.exists(), path)

    def test_builder_refuses_to_write_into_integration_templates(self):
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "build_integrations.py"),
                "--output",
                str(ROOT / "integrations"),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("不能覆盖或进入源码目录", completed.stderr + completed.stdout)
        self.assertFalse(
            (
                ROOT
                / "integrations"
                / "workbuddy"
                / "plugins"
                / "shenlun-review-tools"
                / "skills"
            ).exists()
        )

    def test_standalone_skill_export_is_channel_neutral_and_recoverable(self):
        exporter = ROOT / "scripts" / "export_standalone_skill.py"
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "shenlun-review-skill"
            command = [
                sys.executable,
                str(exporter),
                "--destination",
                str(destination),
            ]
            first = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(skill_hashes(destination), skill_hashes(SKILL))
            self.assertFalse((destination / "tests").exists())

            marker = destination / "local-note.txt"
            marker.write_text("保留旧发布目录", encoding="utf-8")
            second = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(second.returncode, 0, second.stderr)
            backups = list(destination.parent.glob("shenlun-review-skill.backup.*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "local-note.txt").read_text(encoding="utf-8"), "保留旧发布目录")

    def test_client_configs_launch_the_bundled_generic_server(self):
        cases = (
            (
                DIST / "workbuddy" / "plugins" / "shenlun-review-tools" / ".mcp.json",
                "CODEBUDDY_PLUGIN_ROOT",
            ),
            (
                DIST / "codex" / "shenlun-review" / ".mcp.json",
                "CODEX_PLUGIN_ROOT",
            ),
            (
                DIST / "claude-code" / "shenlun-review" / ".mcp.json",
                "CLAUDE_PLUGIN_ROOT",
            ),
        )
        for config, root_variable in cases:
            server = load_json(config)["mcpServers"]["shenlun-review"]
            self.assertEqual(server["command"], "python3")
            self.assertIn(root_variable, server["args"][0])
            self.assertTrue(server["args"][0].endswith("scripts/shenlun_mcp_server.py"))
            self.assertIn(root_variable, server["env"]["SHENLUN_SKILL_DIR"])

    def test_mcp_preserves_quote_rich_chinese_and_calls_fixed_runner(self):
        env = os.environ.copy()
        env.pop("SHENLUN_SKILL_DIR", None)
        process = subprocess.Popen(
            [sys.executable, str(SERVER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            initialize = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            }
            process.stdin.write(json.dumps(initialize, ensure_ascii=False) + "\n")
            process.stdin.flush()
            initialized = json.loads(process.stdout.readline())
            self.assertEqual(initialized["result"]["serverInfo"]["version"], VERSION)

            payload = small_payload()
            special = (
                '群众称政策为“以奖代补”，也写作"以奖代补"。\n'
                "路径 C:\\review\\draft 与字符 $()、`command` 都只是作答文本。"
            )
            payload["material_text"] += special
            payload["user_answer"] += special
            call = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "shenlun_grade_once", "arguments": payload},
            }
            process.stdin.write(json.dumps(call, ensure_ascii=False) + "\n")
            process.stdin.flush()
            response = json.loads(process.stdout.readline())["result"]
            self.assertFalse(response["isError"], response)
            self.assertTrue(response["structuredContent"]["formal_score"])
            self.assertTrue(
                response["structuredContent"]["grade_once_receipt"]["single_compile"]
            )
        finally:
            if process.stdin:
                process.stdin.close()
            process.wait(timeout=5)
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()

    def test_guard_blocks_direct_grading_but_allows_normal_tests(self):
        unsafe = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {
                "command": "python3 review_engine.py grade-once --input grade_once_input.json"
            },
        }
        blocked = subprocess.run(
            [sys.executable, str(GUARD)],
            input=json.dumps(unsafe, ensure_ascii=False),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(blocked.returncode, 0, blocked.stderr)
        self.assertEqual(
            load := json.loads(blocked.stdout)["hookSpecificOutput"]["permissionDecision"],
            "deny",
            load,
        )

        normal = dict(unsafe)
        normal["tool_input"] = {"command": "python3 -m unittest discover -s tests -v"}
        allowed = subprocess.run(
            [sys.executable, str(GUARD)],
            input=json.dumps(normal, ensure_ascii=False),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(json.loads(allowed.stdout), {})

    def test_trae_installer_preserves_other_servers_and_uses_absolute_paths(self):
        installer = ROOT / "integrations" / "trae" / "install.py"
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            mcp_path = project / ".trae" / "mcp.json"
            mcp_path.parent.mkdir(parents=True)
            mcp_path.write_text(
                json.dumps(
                    {"mcpServers": {"existing": {"command": "existing-server"}}},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [sys.executable, str(installer), "--project", str(project)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = load_json(mcp_path)
            self.assertIn("existing", payload["mcpServers"])
            server = payload["mcpServers"]["shenlun-review"]
            self.assertTrue(Path(server["args"][0]).is_absolute())
            self.assertTrue(Path(server["env"]["SHENLUN_SKILL_DIR"]).is_dir())


if __name__ == "__main__":
    unittest.main(verbosity=2)
