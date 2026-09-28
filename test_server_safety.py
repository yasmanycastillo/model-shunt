"""Regression tests for MCP request isolation and workspace boundaries."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / "src"))
from model_shunt import server


class WorkspaceBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="shunt-safety-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.outside = self.root / "outside"
        self.workspace.mkdir()
        self.outside.mkdir()
        self.reference = self.workspace / "reference.py"
        self.reference.write_text("inside = True\n", encoding="utf-8")
        self.external = self.outside / "external.py"
        self.external.write_text("outside = True\n", encoding="utf-8")
        environment = patch.dict(os.environ, {
            "SHUNT_ALLOWED_ROOTS": str(self.workspace),
        })
        environment.start()
        self.addCleanup(environment.stop)

    def assert_rejected(self, result):
        self.assertTrue(result.get("isError"), result)
        self.assertIn("outside the allowed roots", result["content"][0]["text"])

    def test_read_through_external_file_symlink_is_rejected(self):
        linked = self.workspace / "linked.py"
        linked.symlink_to(self.external)
        with patch.object(server, "run_bulk_reader") as worker:
            result = server.handle_bulk_read({"question": "Read it", "file_paths": [str(linked)]})
        self.assert_rejected(result)
        worker.assert_not_called()

    def test_read_through_external_directory_symlink_is_rejected(self):
        linked = self.workspace / "linked"
        linked.symlink_to(self.outside, target_is_directory=True)
        with patch.object(server, "run_bulk_reader") as worker:
            result = server.handle_bulk_read({
                "question": "Read it", "file_paths": [str(linked / "external.py")],
            })
        self.assert_rejected(result)
        worker.assert_not_called()

    def test_external_reference_symlink_is_rejected(self):
        linked = self.workspace / "reference-link.py"
        linked.symlink_to(self.external)
        with patch.object(server, "run_worker") as worker:
            result = server.handle_code_write({"spec": "Generate code", "reference_path": str(linked)})
        self.assert_rejected(result)
        worker.assert_not_called()

    def test_write_through_external_file_symlink_is_rejected(self):
        linked = self.workspace / "target.py"
        linked.symlink_to(self.external)
        with patch.object(server, "run_worker") as worker:
            result = server.handle_code_write({
                "spec": "Generate code", "reference_path": str(self.reference),
                "target_path": str(linked),
            })
        self.assert_rejected(result)
        worker.assert_not_called()
        self.assertEqual(self.external.read_text(encoding="utf-8"), "outside = True\n")

    def test_new_target_under_external_symlink_is_rejected(self):
        linked = self.workspace / "linked"
        linked.symlink_to(self.outside, target_is_directory=True)
        with patch.object(server, "run_worker") as worker:
            result = server.handle_code_write({
                "spec": "Generate code", "reference_path": str(self.reference),
                "target_path": str(linked / "new" / "target.py"),
            })
        self.assert_rejected(result)
        worker.assert_not_called()
        self.assertFalse((self.outside / "new").exists())

    def test_internal_symlink_and_new_target_are_allowed(self):
        linked = self.workspace / "reference-link.py"
        linked.symlink_to(self.reference)
        target = self.workspace / "new" / "target.py"
        with patch.object(server, "run_worker", return_value="generated = True"):
            result = server.handle_code_write({
                "spec": "Generate code", "reference_path": str(linked),
                "target_path": str(target),
            })
        self.assertFalse(result.get("isError"), result)
        self.assertEqual(target.read_text(encoding="utf-8"), "generated = True\n")

    def test_symlinked_allowed_root_is_resolved(self):
        alias = self.root / "workspace-alias"
        alias.symlink_to(self.workspace, target_is_directory=True)
        with patch.dict(os.environ, {"SHUNT_ALLOWED_ROOTS": str(alias)}):
            self.assertEqual(server.resolve_allowed_path(str(self.reference)), str(self.reference))

    def test_filesystem_root_can_be_explicitly_allowed(self):
        with patch.dict(os.environ, {"SHUNT_ALLOWED_ROOTS": self.workspace.anchor}):
            self.assertEqual(server.resolve_allowed_path(str(self.reference)), str(self.reference))

    def test_target_changed_during_generation_is_rechecked(self):
        directory = self.workspace / "target-dir"
        directory.mkdir()

        def generate(**kwargs):
            directory.rmdir()
            directory.symlink_to(self.outside, target_is_directory=True)
            return "generated = True"

        with patch.object(server, "run_worker", side_effect=generate):
            result = server.handle_code_write({
                "spec": "Generate code", "reference_path": str(self.reference),
                "target_path": str(directory / "target.py"),
            })
        self.assert_rejected(result)
        self.assertFalse((self.outside / "target.py").exists())


class RequestIsolationTests(unittest.TestCase):
    def run_server(self, lines, extra_env=None):
        env = dict(os.environ)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, "-B", str(REPO / "src/model_shunt/server.py")],
            input="".join(line + "\n" for line in lines),
            capture_output=True, text=True, timeout=10, cwd=str(REPO), env=env,
        )

    def test_invalid_requests_do_not_prevent_following_ping(self):
        cases = [
            ("{", -32700),
            # Some Python versions reject nesting while parsing; others parse
            # it and then reject the array as an invalid request envelope.
            ("[" * 2000 + "0" + "]" * 2000, (-32700, -32600)),
            (json.dumps([]), -32600),
            (json.dumps({"jsonrpc": "2.0", "id": 1, "method": 7}), -32600),
            (json.dumps({"jsonrpc": "1.0", "id": 1, "method": "ping"}), -32600),
            (json.dumps({"jsonrpc": "2.0", "id": [], "method": "ping"}), -32600),
            (json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": []}), -32602),
        ]
        invalid_calls = [
            ("code_write", {"spec": "Generate code"}),
            ("code_write", []),
            ("code_write", None),
            ("code_write", {"spec": "Generate code", "reference_path": 42}),
            ("bulk_read", {"question": "Read", "file_paths": "reference.py"}),
            ("bulk_read", {"question": "Read", "file_paths": [42]}),
            ("bulk_read", {"question": " ", "file_paths": ["reference.py"]}),
            ("get_available_models", {"provider": False}),
            ("unknown_tool", {}),
        ]
        for name, arguments in invalid_calls:
            cases.append((json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }), -32602))

        ping = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        for index, (line, code) in enumerate(cases):
            with self.subTest(case=index):
                process = self.run_server([line, ping])
                self.assertEqual(process.returncode, 0, process.stderr)
                responses = [json.loads(response) for response in process.stdout.splitlines()]
                self.assertEqual(len(responses), 2, responses)
                expected_codes = code if isinstance(code, tuple) else (code,)
                self.assertIn(responses[0]["error"]["code"], expected_codes, responses)
                self.assertEqual(responses[1], {"jsonrpc": "2.0", "id": 2, "result": {}})

    def test_configuration_error_is_a_tool_error_and_server_continues(self):
        call = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "get_available_models", "arguments": {}},
        })
        ping = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        process = self.run_server([call, ping], {"SHUNT_TIMEOUT": "invalid"})
        self.assertEqual(process.returncode, 0, process.stderr)
        responses = [json.loads(response) for response in process.stdout.splitlines()]
        self.assertTrue(responses[0]["result"].get("isError"), responses)
        self.assertEqual(responses[1]["result"], {})

    def test_notifications_do_not_produce_responses(self):
        notification = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        unknown = json.dumps({"jsonrpc": "2.0", "method": "notifications/unknown"})
        ping = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        process = self.run_server([notification, unknown, ping])
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual([json.loads(line) for line in process.stdout.splitlines()], [
            {"jsonrpc": "2.0", "id": 2, "result": {}},
        ])

    def test_unexpected_handler_exception_is_isolated(self):
        request = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "bulk_read", "arguments": {
                "question": "Read it", "file_paths": ["reference.py"],
            }},
        }
        with patch.object(server, "handle_bulk_read", side_effect=RuntimeError("unexpected failure")):
            response = server.handle_request(request)
        self.assertTrue(response["result"].get("isError"), response)
        self.assertIn("unexpected failure", response["result"]["content"][0]["text"])
        self.assertEqual(server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "ping"}), {
            "jsonrpc": "2.0", "id": 2, "result": {},
        })


if __name__ == "__main__":
    unittest.main()
