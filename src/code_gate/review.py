"""Run the checks on one file and put the findings in one list.

Settings come from two places with different trust. The user's own file
(`~/.config/code-gate/config.json`, or the file named by `CODE_GATE_CONFIG`)
may set everything. A `.code-gate.json` in a project is written by whoever
wrote the project, so it may tune the checks but never choose the command
that is run or the server the code is sent to.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import checks_braces, checks_common, checks_python, judge
from .finding import SEVERITIES, Finding, clean, ordered
from .rules import RULES
from .source import Source, language_of

CONFIG_NAME = ".code-gate.json"
USER_CONFIG_ENV = "CODE_GATE_CONFIG"
MAX_BYTES = 400_000
# Model settings that decide what is run and where the code goes: user config only.
TRUSTED_MODEL_KEYS = ("command", "url", "model", "allow_remote")
# Model settings a project may tune, with the largest value that is accepted.
_MODEL_NUMBERS = {"timeout": 3600, "max_lines": 5000, "max_findings": 50}
_KNOWN_KEYS = {"disable", "limits", "hook", "model", "allow_remote"}
# Rules that fire on many lines of one file are reported once.
_ONCE_PER_FILE = {"print-instead-of-log", "any-type", "stack-trace-printed"}


def user_config_path() -> Path:
    """The user's own settings file: the only place that may choose the model command and URL."""
    named = os.environ.get(USER_CONFIG_ENV)
    if named:
        return Path(named).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "code-gate" / "config.json"


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass
class Config:
    disable: set[str] = field(default_factory=set)
    limits: dict[str, int] = field(default_factory=lambda: dict(checks_common.LIMITS))
    model: dict = field(default_factory=dict)
    block_on: str = "high"
    report_from: str = "medium"
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def near(cls, path: str | Path | None) -> "Config":
        """The user's settings, then the nearest .code-gate.json at or above `path`."""
        config = cls()
        config.load(user_config_path(), trusted=True)
        try:
            start = Path(path).resolve() if path else Path.cwd()
        except (OSError, ValueError):
            return config
        for folder in [start] + list(start.parents):
            candidate = folder / CONFIG_NAME
            if candidate.is_file():
                config.load(candidate, trusted=False)
                break
        return config

    def load(self, file: Path, trusted: bool) -> None:
        try:
            text = file.read_text(encoding="utf-8")
        except FileNotFoundError:
            if trusted and os.environ.get(USER_CONFIG_ENV):
                self.warnings.append(f"{file}: named by {USER_CONFIG_ENV} but not found")
            return
        except (OSError, ValueError) as error:
            self.warnings.append(f"{file}: cannot be read ({clean(error, 120)}); defaults are used")
            return
        try:
            data = json.loads(text)
        except (ValueError, RecursionError):
            self.warnings.append(f"{file}: not valid JSON; defaults are used")
            return
        self.apply(data, trusted=trusted, origin=str(file))

    def apply(self, data: object, trusted: bool = False, origin: str = CONFIG_NAME) -> None:
        """Take what is well formed, and say what was not. Nothing here raises."""

        def warn(text: str) -> None:
            self.warnings.append(f"{origin}: {text}")

        if not isinstance(data, dict):
            warn("must be a JSON object; defaults are used")
            return
        unknown = sorted(str(key) for key in data if key not in _KNOWN_KEYS)
        if unknown:
            warn("unknown setting ignored: " + ", ".join(clean(key, 40) for key in unknown[:8]))
        self._apply_checks(data, warn)
        ignored = self._apply_model(data, trusted, warn)
        if ignored:
            warn(
                "ignored " + ", ".join(ignored) + ": a project file cannot choose the model command, server or "
                f"name. Set them in your own config ({user_config_path()})"
            )

    def _apply_checks(self, data: dict, warn) -> None:
        disable = data.get("disable", [])
        if not isinstance(disable, list) or not all(isinstance(rule, str) for rule in disable):
            warn("`disable` must be a list of rule names; ignored")
        else:
            wrong = [rule for rule in disable if rule not in RULES and rule != "model"]
            if wrong:
                warn("unknown rule in `disable`: " + ", ".join(clean(rule, 40) for rule in wrong[:8]))
            self.disable |= {rule for rule in disable if rule in RULES or rule == "model"}
        limits = data.get("limits", {})
        if not isinstance(limits, dict):
            warn("`limits` must be an object; ignored")
        else:
            for name, value in limits.items():
                if name in self.limits and isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    self.limits[name] = value
                else:
                    warn(f"`limits.{clean(name, 40)}` must be a known limit and a whole number above 0; ignored")
        hook = data.get("hook", {})
        if not isinstance(hook, dict):
            warn("`hook` must be an object; ignored")
            return
        if "block_on" in hook:
            if isinstance(hook["block_on"], str) and hook["block_on"] in SEVERITIES + ("never",):
                self.block_on = hook["block_on"]
            else:
                warn("`hook.block_on` must be high, medium, low or never; ignored")
        if "report_from" in hook:
            if isinstance(hook["report_from"], str) and hook["report_from"] in SEVERITIES:
                self.report_from = hook["report_from"]
            else:
                warn("`hook.report_from` must be high, medium or low; ignored")

    def _apply_model(self, data: dict, trusted: bool, warn) -> list[str]:
        """Returns the names of the settings that were ignored because the file is not trusted."""
        model = data.get("model", {})
        if not isinstance(model, dict):
            warn("`model` must be an object; ignored")
            model = {}
        model = dict(model)
        ignored = []
        if "allow_remote" in data:  # accepted at the top level too
            model.setdefault("allow_remote", data["allow_remote"])
            if not trusted:
                ignored.append("allow_remote")
        for key in TRUSTED_MODEL_KEYS:
            if key in model and not trusted:
                if key != "allow_remote" or "allow_remote" not in ignored:
                    ignored.append(f"model.{key}")
                del model[key]
        checks = {
            "command": lambda v: isinstance(v, list) and bool(v) and all(isinstance(part, str) and part for part in v),
            "url": lambda v: isinstance(v, str) and (v == "" or v.startswith(("http://", "https://"))),
            "model": lambda v: isinstance(v, str) and bool(v),
            "allow_remote": lambda v: isinstance(v, bool),
            "enabled": lambda v: isinstance(v, bool),
        }
        for key, value in model.items():
            if key in checks:
                good = checks[key](value)
            elif key in _MODEL_NUMBERS:
                good = _is_number(value) and 0 < value <= _MODEL_NUMBERS[key]
                good = good and (key == "timeout" or isinstance(value, int))
            else:
                warn(f"unknown setting ignored: model.{clean(key, 40)}")
                continue
            if good:
                self.model[key] = value
            else:
                warn(f"`model.{key}` has the wrong type or is out of range; ignored")
        return ignored


@dataclass
class Result:
    path: str
    language: str
    findings: list[Finding]
    note: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "file": self.path,
            "language": self.language,
            "note": self.note,
            "warnings": list(self.warnings),
            "findings": [f.to_dict() for f in self.findings],
        }


def review_text(text: str, path: str = "", language: str = "", deep: bool = False, config: Config | None = None, asker=None) -> Result:
    config = config or Config()
    warnings = list(config.warnings)
    if not language_of(path, language):
        return Result(path, "", [], "skipped: not Python, TypeScript/JavaScript, Java or Go", warnings)
    if len(text) > MAX_BYTES:
        return Result(path, "", [], "skipped: larger than 400 KB", warnings)
    notes: list[str] = []
    source = Source.of(text, path, language)
    found: list[Finding] = []
    module = checks_python if source.language == "python" else checks_braces
    for check in (checks_common.check, module.check):
        try:
            found += list(check(source, config.limits))
        except checks_braces.TooLong as error:
            notes.append(str(error))
        except Exception as error:  # noqa: BLE001 - one file the parser cannot take must not end the run
            notes.append(f"unreadable: not fully checked ({type(error).__name__}: {clean(error, 100)})")
    found = _tidy(found, source, config)
    if deep and "model" not in config.disable and config.model.get("enabled", True):
        try:
            extra, note = judge.judge(source, found, config.model, **({"asker": asker} if asker else {}))
        except Exception as error:  # noqa: BLE001 - the static findings stand whatever the model pass does
            extra, note = [], f"model not used: {type(error).__name__}"
        found += extra
        notes.append(note)
    findings = ordered([replace(f, file=path) for f in found])
    return Result(path, source.language, findings, "; ".join(note for note in notes if note), warnings)


def review_file(path: str | Path, deep: bool = False, config: Config | None = None) -> Result:
    target = Path(path)
    try:
        info = target.stat()
        if not stat.S_ISREG(info.st_mode):
            return Result(str(path), "", [], "skipped: not a regular file")
        if info.st_size > MAX_BYTES:
            return Result(str(path), "", [], "skipped: larger than 400 KB")
        text = target.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError) as error:
        return Result(str(path), "", [], f"skipped: {clean(getattr(error, 'strerror', '') or error, 120)}")
    return review_text(text, str(path), deep=deep, config=config or Config.near(target.parent))


def _tidy(found: list[Finding], source: Source, config: Config) -> list[Finding]:
    kept: list[Finding] = []
    seen: set[tuple[str, int]] = set()
    counts: dict[str, int] = {}
    first: dict[str, int] = {}
    for finding in sorted(found, key=lambda f: f.line):
        if finding.rule in config.disable or source.ignored(finding.line, finding.rule):
            continue
        if (finding.rule, finding.line) in seen:
            continue
        seen.add((finding.rule, finding.line))
        if finding.rule in _ONCE_PER_FILE:
            counts[finding.rule] = counts.get(finding.rule, 0) + 1
            if finding.rule in first:
                continue
            first[finding.rule] = len(kept)
        kept.append(finding)
    for rule, index in first.items():
        if counts[rule] > 1:
            kept[index] = replace(kept[index], message=f"{kept[index].message} ({counts[rule]} places, first here)")
    return kept
