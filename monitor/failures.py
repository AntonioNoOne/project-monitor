"""
monitor.failures — Failure classification and append-only event log.

Extracted and generalized from AgentsLab's failure_learning.py.
Zero mandatory dependencies — stdlib only.

Usage:
    from monitor import FailureLogger

    log = FailureLogger("logs/failures.jsonl")

    try:
        result = run_step(...)
    except Exception as exc:
        event = log.record(
            source="ryze_pipeline",
            name="extract_visura",
            ok=False,
            error=str(exc),
        )
        print(f"Failure type: {event['failure_type']}")
        print(f"Hint: {event['hint']}")
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


# ── Sanitisation ──────────────────────────────────────────────────────────────

_SECRET_RE = re.compile(r"(Bearer\s+\S+|token=\S+|key=\S+|api_key=\S+)", re.IGNORECASE)


def sanitize(text: str, *, limit: int = 2000) -> str:
    """Strip secrets and truncate."""
    return _SECRET_RE.sub("[redacted]", str(text or ""))[:limit]


# ── Classification ─────────────────────────────────────────────────────────────

_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("missing_path",       ("winerror 3", "path not found", "no such file", "impossibile trovare il percorso")),
    ("wrong_params",       ("unrecognized arguments", "unexpected keyword", "validationerror", "required argument", "invalid argument")),
    ("missing_config",     ("not set", "missing", "mancante", "not configured", "non configurato", "imposta")),
    ("auth_required",      ("login", "oauth", "token", "authenticated", "unauthorized", "forbidden", "api key")),
    ("network",            ("connection", "timeout", "resolve", "dns", "network", "temporarily unavailable", "urlopen error")),
    ("permission_required",("permission", "access denied", "permesso", "denied")),
    ("tool_flaky",         ("rate limit", "quota", "retry", "temporarily", "flaky", "429", "503")),
    ("output_parse",       ("jsondecodeerror", "invalid json", "risposta non valida", "parse error", "unexpected token")),
    ("resource",           ("out of memory", "cuda", "gpu", "oom", "memory error")),
]

_HINTS: dict[str, str] = {
    "missing_path":        "Check that the path/drive is mounted and update config if it changed.",
    "wrong_params":        "Check required option names, types and formats for this script/tool.",
    "missing_config":      "Set the missing environment variable or config key before running.",
    "auth_required":       "Provide or refresh the API key / OAuth token for the affected service.",
    "network":             "Network or external service issue — retry or use a local fallback.",
    "permission_required": "Operator approval or elevated permissions required before this step.",
    "tool_flaky":          "External tool unstable (rate limit / quota) — add retry logic or wait.",
    "output_parse":        "Model/tool produced unparseable output — tighten the prompt or add robust JSON parsing.",
    "resource":            "Out of GPU/CPU memory — reduce batch size, use a smaller model or free VRAM.",
}


def classify_failure(text: str) -> dict[str, str]:
    """Return {'category': ..., 'confidence': 'high'|'low'} from error text."""
    body = str(text or "").lower()
    for category, markers in _RULES:
        if any(m in body for m in markers):
            return {"category": category, "confidence": "high"}
    return {"category": "unknown", "confidence": "low"}


def failure_hint(category: str) -> str:
    return _HINTS.get(str(category or ""), "Unclassified error — save context and add a rule after diagnosis.")


# ── Event builders ─────────────────────────────────────────────────────────────

def _event_id() -> str:
    return uuid.uuid4().hex[:12]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_failure_event(
    *,
    source: str,
    name: str,
    ok: bool,
    returncode: int | None = None,
    error: str = "",
    stdout: str = "",
    stderr: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a structured failure event dict (does NOT write to disk)."""
    combined = "\n".join(part for part in (error, stderr, stdout) if part)
    classification = classify_failure(combined)
    return {
        "event":          "failure_event",
        "id":             _event_id(),
        "created_at":     _now_iso(),
        "source":         source,
        "name":           name,
        "ok":             bool(ok),
        "returncode":     returncode,
        "failure_type":   classification["category"],
        "confidence":     classification["confidence"],
        "hint":           failure_hint(classification["category"]),
        "error":          sanitize(error, limit=1000),
        "stderr_snippet": sanitize(stderr, limit=300),
        "stdout_snippet": sanitize(stdout, limit=300),
        "metadata":       dict(metadata or {}),
    }


# ── FailureLogger ──────────────────────────────────────────────────────────────

class FailureLogger:
    """Append-only failure event logger. Thread-safe for single-process use."""

    def __init__(self, path: str | Path = "logs/failures.jsonl") -> None:
        self.path = Path(path)

    def record(
        self,
        source: str,
        name: str,
        *,
        ok: bool = False,
        error: str = "",
        returncode: int | None = None,
        stdout: str = "",
        stderr: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build, persist and return a failure event."""
        event = build_failure_event(
            source=source,
            name=name,
            ok=ok,
            returncode=returncode,
            error=error,
            stdout=stdout,
            stderr=stderr,
            metadata=metadata,
        )
        self._append(event)
        return event

    def record_ok(self, source: str, name: str, *, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Record a successful step (for complete audit trail)."""
        event = {
            "event": "step_ok",
            "id": _event_id(),
            "created_at": _now_iso(),
            "source": source,
            "name": name,
            "ok": True,
            "metadata": dict(metadata or {}),
        }
        self._append(event)
        return event

    def read(self, *, limit: int = 500) -> list[dict[str, Any]]:
        """Read the last N events from the log."""
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    rows.append(item)
            except Exception:
                continue
        return rows

    def summary(self, *, limit: int = 200) -> dict[str, Any]:
        """Return stats on recent failures."""
        events = self.read(limit=limit)
        failures = [e for e in events if e.get("event") == "failure_event"]
        by_type: dict[str, int] = {}
        for e in failures:
            ft = str(e.get("failure_type") or "unknown")
            by_type[ft] = by_type.get(ft, 0) + 1
        return {
            "total_events": len(events),
            "total_failures": len(failures),
            "by_type": by_type,
            "log_path": str(self.path),
        }

    def _append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
