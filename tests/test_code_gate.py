from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from code_gate import __version__, hook, judge, mcp  # noqa: E402
from code_gate.__main__ import main  # noqa: E402
from code_gate.review import Config, review_file, review_text  # noqa: E402
from code_gate.rules import RULES  # noqa: E402
from code_gate.source import Source, blank  # noqa: E402


_SAVED: dict = {}
_HOME = None


def setUpModule():
    """No test reads the settings of the person who runs it."""
    global _HOME
    _HOME = tempfile.TemporaryDirectory()
    for name in ("XDG_CONFIG_HOME", "CODE_GATE_CONFIG"):
        _SAVED[name] = os.environ.pop(name, None)
    os.environ["XDG_CONFIG_HOME"] = _HOME.name


def tearDownModule():
    for name, value in _SAVED.items():
        os.environ.pop(name, None)
        if value is not None:
            os.environ[name] = value
    _HOME.cleanup()


def fixture(name: str) -> str:
    return os.path.join(HERE, "fixtures", name)


def rules_in(name: str) -> set[str]:
    return {f.rule for f in review_file(fixture(name)).findings}


class Fixtures(unittest.TestCase):
    def test_python(self):
        expected = {
            "hardcoded-secret", "sql-built-from-strings", "shell-injection", "eval", "unsafe-deserialisation",
            "tls-check-off", "no-timeout", "deeply-nested-loops", "mutable-default", "resource-not-closed",
            "swallowed-error", "placeholder", "print-instead-of-log",
        }
        self.assertEqual(expected - rules_in("bad.py"), set())

    def test_typescript(self):
        expected = {
            "hardcoded-secret", "sql-built-from-strings", "shell-injection", "eval", "html-injection", "io-in-loop",
            "sequential-await-in-loop", "list-membership-in-loop", "swallowed-error", "placeholder", "any-type",
        }
        self.assertEqual(expected - rules_in("bad.ts"), set())

    def test_java(self):
        expected = {
            "hardcoded-secret", "sql-built-from-strings", "shell-injection", "weak-hash", "list-membership-in-loop",
            "string-built-in-loop", "swallowed-error", "stack-trace-printed",
        }
        self.assertEqual(expected - rules_in("Bad.java"), set())

    def test_go(self):
        expected = {
            "hardcoded-secret", "sql-built-from-strings", "shell-injection", "weak-hash", "ignored-error",
            "io-in-loop", "no-timeout", "defer-in-loop", "deeply-nested-loops", "placeholder",
        }
        self.assertEqual(expected - rules_in("bad.go"), set())

    def test_good_code_is_quiet(self):
        self.assertEqual(review_file(fixture("good.py")).findings, [])

    def test_this_package_has_no_high_findings(self):
        for name in sorted(os.listdir(os.path.join(ROOT, "src", "code_gate"))):
            if name.endswith(".py"):
                high = [f for f in review_file(os.path.join(ROOT, "src", "code_gate", name)).findings if f.severity == "high"]
                self.assertEqual(high, [], name)


class Review(unittest.TestCase):
    def test_strings_and_comments_do_not_confuse_structure(self):
        self.assertEqual(blank('a = "{" # }\n', "python"), 'a = " "    \n')
        self.assertEqual(blank("x = `{`; // }\n", "typescript"), "x = ` `;     \n")

    def test_ignore_comment(self):
        code = "def f(x):\n    return eval(x)  # code-gate: ignore[eval]\n"
        self.assertEqual(review_text(code, language="python").findings, [])
        other = "def f(x):\n    return eval(x)  # code-gate: ignore[placeholder]\n"
        self.assertEqual([f.rule for f in review_text(other, language="python").findings], ["eval"])

    def test_explained_empty_handler_is_allowed(self):
        quiet = "try:\n    f()\nexcept Exception:\n    pass  # best effort\n"
        loud = "try:\n    f()\nexcept Exception:\n    pass\n"
        self.assertEqual(review_text(quiet, language="python").findings, [])
        self.assertEqual([f.rule for f in review_text(loud, language="python").findings], ["swallowed-error"])
        self.assertEqual(review_text("try { f(); } catch { /* not JSON */ }\n", language="ts").findings, [])
        self.assertEqual(len(review_text("try { f(); } catch (e) {}\n", language="ts").findings), 1)

    def test_syntax_error_is_one_finding(self):
        self.assertEqual([f.rule for f in review_text("def f(:\n", language="python").findings], ["syntax-error"])

    def test_unknown_language_is_skipped(self):
        result = review_text("SELECT 1", "query.sql")
        self.assertEqual(result.findings, [])
        self.assertIn("skipped", result.note)

    def test_noisy_rule_is_reported_once(self):
        code = "def f():\n    print(1)\n    print(2)\n    print(3)\n"
        found = review_text(code, language="python").findings
        self.assertEqual(len(found), 1)
        self.assertIn("3 places", found[0].message)

    def test_config_disables_rules_and_changes_limits(self):
        config = Config()
        config.apply({"disable": ["eval", "no-such-rule"], "limits": {"function_lines": 3}, "hook": {"block_on": "never"}})
        self.assertEqual(config.disable, {"eval"})
        self.assertEqual(config.block_on, "never")
        code = "def f(x):\n    a = eval(x)\n    b = a\n    c = b\n    return c\n"
        self.assertEqual([f.rule for f in review_text(code, language="python", config=config).findings], ["long-function"])

    def test_every_rule_has_a_known_category_and_severity(self):
        from code_gate.finding import CATEGORIES, SEVERITIES

        for rule, (category, severity, message, hint) in RULES.items():
            self.assertIn(category, CATEGORIES, rule)
            self.assertIn(severity, SEVERITIES, rule)
            self.assertTrue(message and hint, rule)


class Judge(unittest.TestCase):
    CODE = 'KEY = "sk-live-9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c"\n\ndef f(items):\n    return [i for i in items]\n'

    def test_secret_lines_never_reach_the_model(self):
        sent = []

        def asker(prompt, settings):
            sent.append(prompt)
            return '{"findings": []}', "local"

        result = review_text(self.CODE, language="python", deep=True, asker=asker)
        self.assertEqual(len(sent), 1)
        self.assertNotIn("sk-live", sent[0])
        self.assertIn("line removed", sent[0])
        self.assertEqual(result.note, "model: local")

    def test_bad_findings_are_dropped(self):
        answer = json.dumps({"findings": [
            {"category": "design", "severity": "medium", "line": 3, "message": "does two jobs", "hint": "split"},
            {"category": "design", "severity": "medium", "line": 99, "message": "no such line"},
            {"category": "style", "severity": "medium", "line": 3, "message": "not a category"},
            {"category": "algorithm", "severity": "huge", "line": 4, "message": "not a severity"},
            {"category": "security", "severity": "high", "line": 1, "message": "on the secret line"},
            "nonsense",
        ]})
        source = Source.of(self.CODE, language="python")
        found, note = judge.judge(source, [], asker=lambda p, s: ("```json\n" + answer + "\n```", "local"))
        self.assertEqual([(f.line, f.category, f.source) for f in found], [(3, "design", "model")])

    def test_answer_from_a_remote_backend_is_dropped(self):
        source = Source.of(self.CODE, language="python")
        found, note = judge.judge(source, [], asker=lambda p, s: ('{"findings": []}', "openai"))
        self.assertEqual(found, [])
        self.assertIn("not the local model", note)

    def test_model_down_is_a_note_not_an_error(self):
        def asker(prompt, settings):
            raise OSError("connection refused")

        result = review_text(self.CODE, language="python", deep=True, asker=asker)
        self.assertIn("model not used", result.note)
        self.assertEqual([f.rule for f in result.findings], ["hardcoded-secret"])

    def test_talk_instead_of_json_is_dropped(self):
        source = Source.of(self.CODE, language="python")
        found, note = judge.judge(source, [], asker=lambda p, s: ("Looks fine to me!", "local"))
        self.assertEqual(found, [])
        self.assertIn("dropped", note)


class Transport(unittest.TestCase):
    def serve(self, models):
        import http.server
        import threading

        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, body, extra=()):
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                for name, value in extra:
                    self.send_header(name, value)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self.reply({"data": [{"id": name} for name in models]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                seen.append((body["model"], self.headers.get("x-harness-route")))
                self.reply({"choices": [{"message": {"content": '{"findings": []}'}}]}, [("x-harness-backend", "local")])

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}/v1/chat/completions", seen

    def test_http_is_used_with_the_lock_when_the_harness_lists_it(self):
        url, seen = self.serve(["llm-harness/local", "llm-harness/local-only"])
        answer, backend = judge.ask("hi", {**judge.DEFAULTS, "url": url})
        self.assertEqual((answer, backend), ('{"findings": []}', "local"))
        self.assertEqual(seen, [("llm-harness/local-only", "local-only")])

    def test_an_old_harness_gets_no_code_over_http(self):
        url, seen = self.serve(["llm-harness/local"])
        settings = {**judge.DEFAULTS, "url": url, "command": [sys.executable, "-c", "print('from the command')"]}
        self.assertEqual(judge.ask("hi", settings), ("from the command\n", "local"))
        self.assertEqual(seen, [])

    def test_no_harness_and_no_command_is_a_note(self):
        settings = {"url": "http://127.0.0.1:9/v1/chat/completions", "command": ["no-such-command-xyz"]}
        found, note = judge.judge(Source.of("x = 1\n", language="python"), [], settings)
        self.assertEqual(found, [])
        self.assertIn("model not used", note)


class Hook(unittest.TestCase):
    def run_hook(self, event) -> dict:
        out = io.StringIO()
        self.assertEqual(hook.run(io.StringIO(event if isinstance(event, str) else json.dumps(event)), out), 0)
        return json.loads(out.getvalue())

    def test_blocks_on_high(self):
        out = self.run_hook({"tool_name": "Write", "tool_input": {"file_path": fixture("bad.py")}})
        self.assertEqual(out["decision"], "block")
        self.assertIn("hardcoded-secret", out["reason"])
        self.assertNotIn("sk-live", out["reason"])

    def test_medium_is_context_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "a.py")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("def f(items=[]):\n    return items\n")
            out = self.run_hook({"tool_name": "Edit", "tool_input": {"file_path": path}})
        self.assertNotIn("decision", out)
        self.assertIn("mutable-default", out["hookSpecificOutput"]["additionalContext"])

    def test_quiet_cases(self):
        self.assertEqual(self.run_hook({"tool_input": {"file_path": fixture("good.py")}}), {})
        self.assertEqual(self.run_hook({"tool_input": {"file_path": "/no/such/file.py"}}), {})
        self.assertEqual(self.run_hook({}), {})
        self.assertEqual(self.run_hook("not json"), {})


class Mcp(unittest.TestCase):
    def ask(self, method, params=None, id_=1):
        return mcp.handle({"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}})

    def test_initialize_and_list(self):
        reply = self.ask("initialize", {"protocolVersion": "2024-11-05"})["result"]
        self.assertEqual(reply["protocolVersion"], "2024-11-05")
        self.assertEqual(reply["serverInfo"], {"name": "code-gate", "version": __version__})
        self.assertEqual(self.ask("initialize", {"protocolVersion": "1999"})["result"]["protocolVersion"], mcp.PROTOCOLS[0])
        names = [tool["name"] for tool in self.ask("tools/list")["result"]["tools"]]
        self.assertEqual(names, ["review_code", "review_files", "list_rules"])

    def test_notification_gets_no_answer(self):
        self.assertIsNone(mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_review_code(self):
        reply = self.ask("tools/call", {"name": "review_code", "arguments": {"code": "eval(x)\n", "language": "python"}})
        self.assertFalse(reply["result"]["isError"])
        self.assertEqual(reply["result"]["structuredContent"]["results"][0]["findings"][0]["rule"], "eval")

    def test_review_files_and_rules(self):
        reply = self.ask("tools/call", {"name": "review_files", "arguments": {"paths": [fixture("bad.go"), "/no/such.go"]}})
        results = reply["result"]["structuredContent"]["results"]
        self.assertTrue(results[0]["findings"])
        self.assertIn("skipped", results[1]["note"])
        rules = self.ask("tools/call", {"name": "list_rules"})["result"]["structuredContent"]["rules"]
        self.assertEqual(len(rules), len(RULES))

    def test_errors(self):
        for arguments in ({}, {"code": "x = 1"}):
            self.assertTrue(self.ask("tools/call", {"name": "review_code", "arguments": arguments})["result"]["isError"])
        self.assertTrue(self.ask("tools/call", {"name": "nope"})["result"]["isError"])
        self.assertEqual(self.ask("nope")["error"]["code"], -32601)

    def test_serve_reads_lines(self):
        out = io.StringIO()
        mcp.serve(io.StringIO('{"jsonrpc":"2.0","id":7,"method":"ping"}\nnot json\n'), out)
        first, second = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(first, {"jsonrpc": "2.0", "id": 7, "result": {}})
        self.assertEqual(second["error"]["code"], -32700)


class Packaging(unittest.TestCase):
    def test_plugin_version_matches(self):
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["version"], __version__)

    def test_json_files_parse(self):
        for name in ("hooks/hooks.json", ".mcp.json", ".claude-plugin/marketplace.json"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as handle:
                json.load(handle)

    def test_readme_and_metadata_agree_with_the_code(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as handle:
            readme = handle.read()
        self.assertIn(f"the {len(RULES)} static rules", readme)
        with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as handle:
            project = handle.read()
        self.assertIn('name = "code-gate-llm"', project)
        self.assertIn('code-gate = "code_gate.__main__:main"', project)
        with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as handle:
            self.assertIn(f"## {__version__}", handle.read())

    def test_version_option(self):
        import contextlib

        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as stopped:
            main(["--version"])
        self.assertEqual(stopped.exception.code, 0)
        self.assertEqual(out.getvalue().strip(), f"code-gate {__version__}")

    def test_exit_codes(self):
        import contextlib

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["check", fixture("bad.py")]), 1)
            self.assertEqual(main(["check", fixture("bad.py"), "--fail-on", "never"]), 0)
            self.assertEqual(main(["check", fixture("good.py")]), 0)


if __name__ == "__main__":
    unittest.main()
