"""
monitor.health — Pre-flight checks before running a pipeline.

Usage:
    from monitor import HealthCheck

    h = HealthCheck(name="RYZE pipeline")
    h.require_env("GOOGLE_API_KEY", hint="Set in .env — see .env.example")
    h.require_env("OLLAMA_MODEL", required=False, default="qwen2.5vl:3b")
    h.require_command("python", hint="Python not found")
    h.require_command("ollama", required=False, hint="Ollama not installed — local extraction unavailable")
    h.require_file(".venv-idp/Scripts/python.exe", hint="Run: py -3.13 -m venv .venv-idp && pip install docling")
    h.require_url("http://localhost:11434", required=False, hint="Ollama not running — start with: ollama serve")
    h.run_or_exit()   # prints report, exits with code 1 if any required check failed

    # Or collect results manually:
    report = h.run()
    print(report.summary())
    if not report.ok:
        sys.exit(1)
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CheckResult:
    name: str
    ok: bool
    required: bool
    value: str = ""
    hint: str = ""
    kind: str = "generic"

    @property
    def icon(self) -> str:
        if self.ok:
            return "✓"
        return "✗" if self.required else "⚠"

    def __str__(self) -> str:
        line = f"  {self.icon} [{self.kind}] {self.name}"
        if self.value:
            line += f" = {self.value}"
        if not self.ok and self.hint:
            line += f"\n      hint: {self.hint}"
        return line


@dataclass
class HealthReport:
    project: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks if c.required)

    @property
    def failed_required(self) -> list[CheckResult]:
        return [c for c in self.checks if c.required and not c.ok]

    @property
    def warnings(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.required and not c.ok]

    def summary(self) -> str:
        lines = [f"\n── Health check: {self.project} ──"]
        for check in self.checks:
            lines.append(str(check))
        if self.ok:
            lines.append(f"\n  ✓ All required checks passed.")
        else:
            lines.append(f"\n  ✗ {len(self.failed_required)} required check(s) failed — fix them before running.")
        if self.warnings:
            lines.append(f"  ⚠  {len(self.warnings)} optional check(s) unavailable.")
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "ok": self.ok,
            "checks": [
                {"name": c.name, "ok": c.ok, "required": c.required, "kind": c.kind, "value": c.value, "hint": c.hint}
                for c in self.checks
            ],
        }


class HealthCheck:
    """Collect and run pre-flight checks for a pipeline."""

    def __init__(self, name: str = "pipeline", cwd: Path | str | None = None) -> None:
        self.name = name
        self.cwd = Path(cwd) if cwd else Path.cwd()
        self._checks: list[tuple[str, dict[str, Any]]] = []

    def require_env(
        self,
        var: str,
        *,
        required: bool = True,
        default: str | None = None,
        hint: str = "",
    ) -> "HealthCheck":
        """Check that an environment variable is set (non-empty)."""
        self._checks.append(("env", {"var": var, "required": required, "default": default, "hint": hint}))
        return self

    def require_command(self, cmd: str, *, required: bool = True, hint: str = "") -> "HealthCheck":
        """Check that a CLI command is available on PATH."""
        self._checks.append(("command", {"cmd": cmd, "required": required, "hint": hint}))
        return self

    def require_file(self, path: str | Path, *, required: bool = True, hint: str = "") -> "HealthCheck":
        """Check that a file or directory exists."""
        self._checks.append(("file", {"path": path, "required": required, "hint": hint}))
        return self

    def require_url(
        self,
        url: str,
        *,
        required: bool = False,
        timeout: int = 3,
        hint: str = "",
    ) -> "HealthCheck":
        """Check that an HTTP endpoint is reachable (GET, no auth)."""
        self._checks.append(("url", {"url": url, "required": required, "timeout": timeout, "hint": hint}))
        return self

    def require_python_import(self, module: str, *, required: bool = True, hint: str = "") -> "HealthCheck":
        """Check that a Python module can be imported."""
        self._checks.append(("import", {"module": module, "required": required, "hint": hint}))
        return self

    def run(self) -> HealthReport:
        """Execute all checks and return a HealthReport."""
        report = HealthReport(project=self.name)
        for kind, cfg in self._checks:
            if kind == "env":
                result = self._check_env(**cfg)
            elif kind == "command":
                result = self._check_command(**cfg)
            elif kind == "file":
                result = self._check_file(**cfg)
            elif kind == "url":
                result = self._check_url(**cfg)
            elif kind == "import":
                result = self._check_import(**cfg)
            else:
                result = CheckResult(name=kind, ok=False, required=True, hint="Unknown check kind")
            report.checks.append(result)
        return report

    def run_or_exit(self, *, stream: Any = None) -> HealthReport:
        """Run checks, print summary, exit(1) if any required check failed."""
        out = stream or sys.stdout
        report = self.run()
        print(report.summary(), file=out)
        if not report.ok:
            sys.exit(1)
        return report

    # ── internal ───────────────────────────────────────────────────────────

    def _check_env(self, var: str, required: bool, default: str | None, hint: str) -> CheckResult:
        value = os.getenv(var) or default or ""
        ok = bool(value.strip())
        display = (value[:12] + "…") if len(value) > 12 else value
        return CheckResult(name=var, ok=ok, required=required, value=display if ok else "(not set)", hint=hint, kind="env")

    def _check_command(self, cmd: str, required: bool, hint: str) -> CheckResult:
        path = shutil.which(cmd)
        ok = path is not None
        return CheckResult(name=cmd, ok=ok, required=required, value=path or "", hint=hint, kind="command")

    def _check_file(self, path: str | Path, required: bool, hint: str) -> CheckResult:
        resolved = Path(path) if Path(path).is_absolute() else self.cwd / path
        ok = resolved.exists()
        return CheckResult(name=str(path), ok=ok, required=required, value=str(resolved) if ok else "", hint=hint, kind="file")

    def _check_url(self, url: str, required: bool, timeout: int, hint: str) -> CheckResult:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                ok = resp.status < 500
        except Exception:
            ok = False
        return CheckResult(name=url, ok=ok, required=required, hint=hint, kind="url")

    def _check_import(self, module: str, required: bool, hint: str) -> CheckResult:
        try:
            __import__(module)
            ok = True
            value = "importable"
        except ImportError as exc:
            ok = False
            value = str(exc)
        return CheckResult(name=module, ok=ok, required=required, value=value, hint=hint, kind="import")
