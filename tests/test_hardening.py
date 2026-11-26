"""One test for each problem the first stress test found (CG1 to CG12) and for the missed cases."""

from __future__ import annotations

import contextlib
import http.server
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from code_gate import hook, judge, mcp  # noqa: E402
from code_gate.__main__ import main  # noqa: E402
from code_gate.review import Config, review_file, review_text  # noqa: E402
from code_gate.source import Source  # noqa: E402

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


def found(code: str, language: str = "python") -> list[tuple[str, str, int]]:
    return [(f.rule, f.severity, f.line) for f in review_text(code, language=language).findings]


def rules(code: str, language: str = "python") -> list[str]:
    return [rule for rule, _, _ in found(code, language)]


class FakeServer:
    """A model server on a free local port. `cut` makes it close the connection in the middle of an answer."""

    def __init__(self, case: unittest.TestCase, cut: bool = False, header: str | None = "local"):
        self.posts: list[dict] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, body: dict, extra=(), cut: bool = False):
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                for name, value in extra:
                    self.send_header(name, value)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data[: len(data) // 2] if cut else data)

            def do_GET(self):
                self.reply({"data": [{"id": "llm-harness/local"}, {"id": "llm-harness/local-only"}]})

            def do_POST(self):
                outer.posts.append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
                body = {"choices": [{"message": {"content": '{"findings": []}'}}]}
                extra = [("x-harness-backend", header)] if header is not None else []
                self.reply(body, extra, cut=cut)

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        case.addCleanup(self.server.server_close)
        case.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/chat/completions"


class WithFolder(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.folder = os.path.realpath(holder.name)

    def write(self, name: str, content) -> str:
        path = os.path.join(self.folder, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content if isinstance(content, str) else json.dumps(content))
        return path

    def user_config(self, content) -> None:
        os.environ["CODE_GATE_CONFIG"] = self.write("home/config.json", content)
        self.addCleanup(os.environ.pop, "CODE_GATE_CONFIG", None)


class ProjectConfigIsNotTrusted(WithFolder):
    """CG1: a repository must not choose the command that runs or the server that gets the code."""

    def test_project_file_cannot_choose_the_command_or_the_server(self):
        mine, theirs = FakeServer(self), FakeServer(self)
        marker = os.path.join(self.folder, "marker.txt")
        self.user_config({"model": {"url": mine.url, "command": ["no-such-command-xyz"]}})
        planted = [sys.executable, "-c", f"open({marker!r}, 'w').write('ran')"]
        self.write("repo/.code-gate.json", {"model": {"url": theirs.url, "command": planted, "model": "gpt", "allow_remote": True}})
        path = self.write("repo/a.py", "def f(items):\n    return [i for i in items]\n")

        result = review_file(path, deep=True)

        self.assertEqual(theirs.posts, [])
        self.assertFalse(os.path.exists(marker))
        self.assertEqual([post["model"] for post in mine.posts], ["llm-harness/local-only"])
        self.assertEqual(result.note, "model: local")
        warning = " ".join(result.warnings)
        for key in ("model.command", "model.url", "model.model", "allow_remote"):
            self.assertIn(key, warning)

    def test_project_file_cannot_send_the_code_by_emptying_the_url(self):
        marker = os.path.join(self.folder, "marker.txt")
        self.user_config({"model": {"url": "http://127.0.0.1:9/v1/chat/completions", "command": ["no-such-command-xyz"]}})
        self.write("repo/.code-gate.json", {"model": {"url": "", "command": ["/bin/sh", "-c", f"echo ran > {marker}"]}})
        result = review_file(self.write("repo/a.py", "x = 1\n"), deep=True)
        self.assertFalse(os.path.exists(marker))
        self.assertIn("model not used", result.note)

    def test_the_user_file_chooses_and_a_project_still_tunes(self):
        self.user_config({"model": {"command": ["my-model", "--file"], "url": "http://127.0.0.1:1/v1", "allow_remote": True}})
        self.write("repo/.code-gate.json", {"disable": ["eval"], "model": {"max_findings": 3, "timeout": 5}})
        config = Config.near(os.path.join(self.folder, "repo"))
        self.assertEqual(config.warnings, [])
        self.assertEqual(config.model["command"], ["my-model", "--file"])
        self.assertEqual(config.model["url"], "http://127.0.0.1:1/v1")
        self.assertTrue(config.model["allow_remote"])
        self.assertEqual((config.model["max_findings"], config.model["timeout"]), (3, 5))
        self.assertEqual(config.disable, {"eval"})


class Stubs(unittest.TestCase):
    """CG2: an empty body is a placeholder only when the code says it is unfinished."""

    def test_legitimate_empty_bodies_are_quiet(self):
        quiet = (
            'class H:\n    def handle(self):\n        """Override this."""\n',
            "import abc\nclass H(abc.ABC):\n    @abc.abstractmethod\n    def handle(self):\n        pass\n",
            "from typing import Protocol\nclass H(Protocol):\n    def handle(self) -> int: ...\n",
            "from typing import overload\n@overload\ndef f(x: int) -> int: ...\n@overload\ndef f(x: str) -> str: ...\ndef f(x):\n    return x\n",
            "class Base:\n    def handle(self):\n        raise NotImplementedError\n",
            "def on_close(*args):\n    pass\n",
            "class A:\n    def __enter__(self):\n        pass\n",
        )
        for code in quiet:
            self.assertEqual(rules(code), [], code)

    def test_plain_stub_is_medium_and_marked_stub_is_high(self):
        self.assertEqual(found("def load(path):\n    pass\n"), [("empty-function", "medium", 1)])
        self.assertEqual(found("def load(path):\n    pass  # TODO: read the file\n"), [("placeholder", "high", 1)])
        self.assertIn(("placeholder", "high", 1), found("def load(path):\n    ...  # not implemented yet\n"))
        self.assertEqual(found("def load(path):\n    raise NotImplementedError\n"), [("empty-function", "medium", 1)])


class Secrets(unittest.TestCase):
    """CG3: a name with `token` or `password` in it is not a secret unless the value looks like one."""

    def test_labels_and_names_are_not_secrets(self):
        quiet = (
            "token_type = 'comment'\n",
            "TOKEN_NAME = 'identifier'\nsecret_name = 'my-secret-name'\n",
            "password_label = 'Password:'\nPASSWORD_FIELD = 'password_field'\n",
            "password_prompt = 'Enter your password'\n",
            "api_key_header = 'X-Api-Key-Header'\n",
        )
        for code in quiet:
            self.assertEqual(rules(code), [], code)
        self.assertEqual(rules("const tokenUrl = `${base}/oauth/token/refresh`;\n", "typescript"), [])

    def test_real_looking_values_are_still_found(self):
        loud = (
            'API_KEY = "sk-live-9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c"\n',
            'password = "hunter2Zx9!"\n',
            'db_password = "Tr0ub4dor&3"\n',
            'auth_token = "tok_a8Kd92LmQx71ZpRt56YvBn30WsEc"\n',
        )
        for code in loud:
            self.assertEqual(rules(code), ["hardcoded-secret"], code)


class LongLines(unittest.TestCase):
    """CG4: the time taken grows with the size of the file, not with its square."""

    def test_long_dotted_line_is_fast(self):
        started = time.monotonic()
        for language, line in (("python", "x = " + "a." * 6000 + "b\n"), ("typescript", "const x = " + "a." * 4500 + "b;\n"), ("go", "x := " + "a." * 6000 + "b\n")):
            review_text(line, language=language)
        self.assertLess(time.monotonic() - started, 3.0)

    def test_many_calls_in_one_expression_are_fast(self):
        started = time.monotonic()
        review_text("x = " + "+".join(["f(1)"] * 5000) + "\n", language="python")
        self.assertLess(time.monotonic() - started, 2.0)

    def test_twice_the_size_is_about_twice_the_time(self):
        def seconds(count: int) -> float:
            code = "".join(f"value_{i} = call_{i}(a.b.c.d, 'text {i}') + " + "a." * 150 + "b\n" for i in range(count))
            best = 9e9
            for _ in range(3):
                started = time.perf_counter()
                review_text(code, language="python")
                best = min(best, time.perf_counter() - started)
            return best

        small, large = seconds(300), seconds(600)
        self.assertLess(large, small * 3.5 + 0.05)

    def test_minified_file_says_what_was_skipped(self):
        result = review_text("const a = " + "f(x)." * 4000 + "y;\nconst api_key = 'sk-live-9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c';\n", language="javascript")
        self.assertIn("structure checks skipped", result.note)
        self.assertEqual([f.rule for f in result.findings], ["hardcoded-secret"])


class ParserFailures(unittest.TestCase):
    """CG5: whatever the parser does with a file, the run goes on and says so."""

    def test_file_the_parser_cannot_take_is_a_note(self):
        nested = "x = " + "lambda: (" * 600 + "1" + ")" * 600 + "\n"
        for code in (nested, "x = 1\x00\ny = eval(x)\n", "x = " + "[" * 5000 + "]" * 5000 + "\n"):
            result = review_text('KEY = "sk-live-9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c"\n' + code, language="python")
            self.assertIn("hardcoded-secret", [f.rule for f in result.findings])
            if not any(f.rule in ("syntax-error", "eval") for f in result.findings):
                self.assertIn("not fully checked", result.note)

    def test_one_bad_file_does_not_end_a_run(self):
        with tempfile.TemporaryDirectory() as folder:
            for name, code in (("a.py", "x = " + "lambda: (" * 600 + "1" + ")" * 600 + "\n"), ("b.py", "eval(input())\n")):
                with open(os.path.join(folder, name), "w", encoding="utf-8") as handle:
                    handle.write(code)
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["check", folder]), 1)
            self.assertIn("eval", out.getvalue())


class ModelAnswers(unittest.TestCase):
    CODE = "def f(items):\n    return [i for i in items]\n"

    def test_answer_cut_off_by_the_server_is_a_note(self):
        """CG6"""
        server = FakeServer(self, cut=True)
        settings = {"url": server.url, "command": ["no-such-command-xyz"], "timeout": 5}
        result, note = judge.judge(Source.of(self.CODE, language="python"), [], settings)
        self.assertEqual(result, [])
        self.assertIn("model not used", note)
        self.assertEqual(len(server.posts), 1)

    def test_answer_without_a_backend_name_is_dropped(self):
        """CG7"""
        answer = json.dumps({"findings": [{"category": "design", "severity": "medium", "line": 1, "message": "m", "hint": "h"}]})
        source = Source.of(self.CODE, language="python")
        for backend in ("", None, " ", "LOCAL-ish"):
            result, note = judge.judge(source, [], asker=lambda p, s, b=backend: (answer, b))
            self.assertEqual(result, [], repr(backend))
            self.assertIn("dropped", note)
        result, note = judge.judge(source, [], {"allow_remote": True}, asker=lambda p, s: (answer, ""))
        self.assertEqual(len(result), 1)

    def test_server_that_sends_no_backend_header_is_dropped(self):
        """CG7, over HTTP"""
        server = FakeServer(self, header=None)
        result, note = judge.judge(Source.of(self.CODE, language="python"), [], {"url": server.url, "command": ["no-such-command-xyz"]})
        self.assertIn("dropped", note)
        self.assertIn("allow_remote", note)

    def test_control_characters_from_the_model_are_removed(self):
        """CG11"""
        nasty = "\x1b[2J\x1b[31mall clear\x1b[0m\r\nline two \u202egnp.exe\u200b \x07"
        answer = json.dumps({"findings": [{"category": "design", "severity": "medium", "line": 1, "message": nasty, "hint": nasty}]})
        result = review_text(self.CODE, language="python", deep=True, asker=lambda p, s: (answer, "local"))
        model = [f for f in result.findings if f.source == "model"]
        self.assertEqual(len(model), 1)
        for text in (model[0].message, model[0].hint, result.note):
            self.assertEqual([c for c in text if ord(c) < 32 or 127 <= ord(c) < 160 or c in "\u202e\u200b"], [])
        self.assertIn("all clear", model[0].message)
        dropped = review_text(self.CODE, language="python", deep=True, asker=lambda p, s: ("{}", "evil\x1b[31m\nlocal"))
        self.assertNotIn("\x1b", dropped.note)
        self.assertNotIn("\n", dropped.note)


class Paths(WithFolder):
    """CG8"""

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs named pipes")
    def test_named_pipe_is_skipped_not_read(self):
        pipe = os.path.join(self.folder, "pipe.py")
        os.mkfifo(pipe)
        results = []
        worker = threading.Thread(target=lambda: results.append(review_file(pipe)), daemon=True)
        worker.start()
        worker.join(5)
        if worker.is_alive():
            os.close(os.open(pipe, os.O_WRONLY | os.O_NONBLOCK))  # let the reader go
            self.fail("review_file waits on a named pipe")
        self.assertIn("not a regular file", results[0].note)
        self.assertIn("not a regular file", review_file(self.folder).note)

    def test_missing_path_is_an_error(self):
        out, err = io.StringIO(), io.StringIO()
        missing = os.path.join(self.folder, "nope.py")
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(main(["check", missing]), 2)
            self.assertEqual(main(["check", missing, self.write("ok.py", "x = 1\n"), "--fail-on", "never"]), 2)
        self.assertIn("no such file or folder", err.getvalue())
        self.assertIn("nope.py", err.getvalue())


class BrokenSettings(WithFolder):
    """CG9: a settings file with the wrong types is reported; the checks run with the defaults."""

    WRONG = (
        {"disable": 5}, {"disable": [1, None]}, {"hook": "x"}, {"hook": {"block_on": 3}}, {"hook": {"block_on": "sometimes"}},
        {"limits": []}, {"limits": {"function_lines": "many"}}, {"limits": {"function_lines": -1}}, {"model": "x"},
        {"model": {"timeout": "soon"}}, {"model": {"enabled": "yes"}}, [], "text", 7, None, {"surprise": 1},
    )

    def test_wrong_types_are_warnings(self):
        for data in self.WRONG:
            config = Config()
            config.apply(data)
            self.assertTrue(config.warnings, data)
            self.assertEqual((config.block_on, config.limits, config.disable), (Config().block_on, Config().limits, set()), data)
            for trusted in (Config(),):
                trusted.apply(data, trusted=True)
                self.assertTrue(trusted.warnings, data)

    def test_hook_still_reports_and_names_the_settings_problem(self):
        self.write(".code-gate.json", {"hook": "x", "disable": 5})
        out = io.StringIO()
        event = {"tool_name": "Write", "tool_input": {"file_path": self.write("a.py", "import os\nos.system('rm ' + name)\n")}}
        self.assertEqual(hook.run(io.StringIO(json.dumps(event)), out), 0)
        answer = json.loads(out.getvalue())
        self.assertEqual(answer.get("decision"), "block")
        self.assertIn("shell-injection", answer["reason"])
        self.assertIn("settings problem", answer["reason"])

    def test_hook_names_the_settings_problem_when_the_code_is_clean(self):
        self.write(".code-gate.json", "{not json")
        out = io.StringIO()
        event = {"tool_name": "Write", "tool_input": {"file_path": self.write("a.py", "x = 1\n")}}
        self.assertEqual(hook.run(io.StringIO(json.dumps(event)), out), 0)
        self.assertIn("not valid JSON", json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"])

    def test_command_line_prints_the_warning_once(self):
        self.write(".code-gate.json", {"limits": "x"})
        self.write("a.py", "x = 1\n")
        self.write("b.py", "y = 2\n")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(main(["check", self.folder]), 0)
        self.assertEqual(err.getvalue().count("code-gate: warning:"), 1)

    def test_hook_takes_any_json(self):
        for event in ([], 5, "x", None, {"tool_input": []}, {"tool_input": {"file_path": 5}}, {"tool_input": {"file_path": ["a"]}}):
            out = io.StringIO()
            self.assertEqual(hook.run(io.StringIO(json.dumps(event)), out), 0)
            self.assertEqual(json.loads(out.getvalue()), {})


class NewerSyntax(unittest.TestCase):
    """CG10: grammar newer than the Python that runs code-gate is not a syntax error in the code."""

    SAMPLES = (
        ((3, 10), "match command:\n    case 'go':\n        x = 1\n    case _:\n        x = 2\n"),
        ((3, 11), "try:\n    f()\nexcept* ValueError:\n    x = 1\n"),
        ((3, 12), "def first[T](items: list[T]) -> T:\n    return items[0]\n"),
        ((3, 14), "try:\n    f()\nexcept ValueError, TypeError:\n    x = 1\n"),
    )

    def test_newer_grammar_is_a_low_note(self):
        samples = [code for version, code in self.SAMPLES if sys.version_info < version]
        if not samples:
            self.skipTest("this Python reads every sample")
        for code in samples:
            self.assertEqual([(rule, severity) for rule, severity, _ in found(code)], [("newer-syntax", "low")], code)

    def test_code_this_python_reads_has_no_such_finding(self):
        for version, code in self.SAMPLES:
            if sys.version_info >= version:
                self.assertEqual(rules(code), [], code)

    def test_real_syntax_error_is_still_high(self):
        self.assertEqual([(rule, severity) for rule, severity, _ in found("def f(:\n    pass\n")], [("syntax-error", "high")])


class McpAbuse(unittest.TestCase):
    """CG12"""

    def test_params_and_arguments_of_the_wrong_type(self):
        for params in ([1, 2], "text", 5, True):
            for method in ("tools/call", "initialize", "tools/list"):
                reply = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
                self.assertEqual(reply["error"]["code"], -32602, (method, params))
        self.assertEqual(mcp.handle({"jsonrpc": "2.0", "id": 1, "method": 5})["error"]["code"], -32600)
        for params in ({"name": "review_code", "arguments": [1]}, {"name": ["review_code"]}, {"name": "review_files", "arguments": {"paths": "a.py"}},
                       {"name": "review_files", "arguments": {"paths": [1, {}]}}, {"name": "review_code", "arguments": {"code": "x = 1", "language": 5}}):
            reply = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
            self.assertTrue(reply["result"]["isError"], params)
            self.assertNotIn("Error:", reply["result"]["content"][0]["text"].replace("isError", ""))
            self.assertNotIn("object has no attribute", reply["result"]["content"][0]["text"])

    def test_large_snippet_is_refused_at_once(self):
        code = "x = 1\n" * 300_000
        started = time.monotonic()
        reply = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "review_code", "arguments": {"code": code, "language": "python"}}})
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("400 KB", reply["result"]["content"][0]["text"])

    def test_server_outlives_bad_input(self):
        lines = ["[" * 100_000, '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":[1]}', "[1, 2]", '"text"', "null",
                 '{"jsonrpc":"2.0","id":2,"method":{"a":1}}', '{"jsonrpc":"2.0","id":7,"method":"ping"}']
        out = io.StringIO()
        self.assertEqual(mcp.serve(io.StringIO("\n".join(lines) + "\n"), out), 0)
        replies = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(replies[-1], {"jsonrpc": "2.0", "id": 7, "result": {}})
        self.assertEqual(replies[0]["error"]["code"], -32700)
        self.assertEqual(len(replies), len(lines))

    def test_settings_warnings_reach_the_client(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, ".code-gate.json"), "w", encoding="utf-8") as handle:
                handle.write('{"model": {"command": ["x"]}}')
            path = os.path.join(folder, "a.py")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("x = 1\n")
            reply = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "review_files", "arguments": {"paths": [path]}}})
        self.assertIn("model.command", reply["result"]["content"][0]["text"])
        self.assertIn("model.command", reply["result"]["structuredContent"]["results"][0]["warnings"][0])


class MissedCases(unittest.TestCase):
    """Calls with no time limit that the first version did not see."""

    def test_python_calls_without_a_timeout(self):
        loud = (
            "import urllib.request\nurllib.request.urlopen(url)\n",
            "from urllib.request import urlopen\nurlopen(url)\n",
            "import requests as rq\nrq.get(url)\n",
            "from requests import get\nget(url)\n",
            "from requests import post as send\nsend(url, json=body)\n",
            "import requests\nrequests.Session().get(url)\n",
            "import requests\nsession = requests.Session()\nsession.post(url, data=body)\n",
            "import requests\nwith requests.Session() as s:\n    s.get(url)\n",
        )
        for code in loud:
            self.assertEqual(rules(code), ["no-timeout"], code)

    def test_python_calls_with_a_timeout_are_quiet(self):
        quiet = (
            "import urllib.request\nurllib.request.urlopen(url, timeout=5)\n",
            "from urllib.request import urlopen\nurlopen(url, None, 5)\n",
            "import requests as rq\nrq.get(url, timeout=3)\n",
            "import requests\nsession = requests.Session()\nsession.post(url, data=body, timeout=3)\n",
            "def get(key):\n    return key\nget('a')\n",
            "session = make()\nsession.get('key')\n",
        )
        for code in quiet:
            self.assertEqual(rules(code), [], code)

    def test_fetch_and_axios_without_a_time_limit(self):
        loud = (
            "const r = await fetch(url);\n",
            "const r = await fetch(url, { method: 'POST', body });\n",
            "const r = await axios.get(url);\n",
            "const r = await axios.post(url, body, { headers });\n",
        )
        for code in loud:
            self.assertEqual(rules(code, "typescript"), ["no-timeout"], code)
        quiet = (
            "const r = await fetch(url, { signal: AbortSignal.timeout(5000) });\n",
            "const r = await fetch(url, { signal });\n",
            "const r = await fetch(url, options);\n",
            "const r = await axios.get(url, { timeout: 5000 });\n",
            "axios.defaults.timeout = 5000;\nconst r = await axios.get(url);\n",
            "class Api {\n  fetch(url: string) {\n    return this.cache.get(url);\n  }\n}\n",
        )
        for code in quiet:
            self.assertEqual(rules(code, "typescript"), [], code)


if __name__ == "__main__":
    unittest.main()
