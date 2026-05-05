"""
monitor.runner — Step-by-step pipeline runner with JSONL logging and checkpointing.

Generalized from AgentsLab's run_checklist.py (stripped of Telegram, Postgres,
tool_hub — only stdlib + optional pyyaml).

Usage (dict-based):
    from monitor import StepRunner

    def extract(pdf, output): ...
    def validate(folder): ...

    runner = StepRunner(
        log_file="logs/pipeline.jsonl",
        checkpoint_dir=".cache/pipeline",
    )
    results = runner.run([
        {"id": "extract",  "fn": extract,  "args": {"pdf": "doc.pdf", "output": "out.json"}},
        {"id": "validate", "fn": validate, "args": {"folder": "out/"}, "depends_on": "extract"},
    ])
    print(runner.report_md())

Usage (YAML file, requires pyyaml):
    results = runner.run_yaml("pipeline.yaml")
"""
from __future__ import annotations

import json
import sys
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from monitor.checkpoint import CheckpointStore
from monitor.failures import FailureLogger


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event_id() -> str:
    return uuid.uuid4().hex[:12]


# ── Step definition ────────────────────────────────────────────────────────────

@dataclass
class Step:
    id: str
    fn: Callable[..., Any]
    args: dict[str, Any] = field(default_factory=dict)
    depends_on: str | list[str] | None = None
    skip_on_dep_failure: bool = True   # skip this step if a dependency failed
    checkpoint: bool = True            # use CheckpointStore if available
    retry: int = 0                     # number of retries on exception
    retry_delay_s: float = 2.0


@dataclass
class StepResult:
    step_id: str
    status: str          # "done" | "failed" | "skipped" | "blocked"
    result: Any = None
    error: str = ""
    elapsed_s: float = 0.0
    cached: bool = False


# ── Runner ─────────────────────────────────────────────────────────────────────

class StepRunner:
    """
    Runs a list of steps in order, with:
    - JSONL event log (start / done / failed / skipped)
    - Optional per-step checkpointing (skip if already done)
    - Failure classification via FailureLogger
    - Dependency graph (depends_on)
    - Retry on transient failures
    """

    def __init__(
        self,
        *,
        log_file: str | Path = "logs/pipeline.jsonl",
        checkpoint_dir: str | Path | None = None,
        force: bool = False,
        verbose: bool = True,
    ) -> None:
        self.log_file = Path(log_file)
        self.checkpoint = CheckpointStore(checkpoint_dir, force=force) if checkpoint_dir else None
        self.failure_logger = FailureLogger(Path(log_file).with_name("failures.jsonl"))
        self.verbose = verbose
        self._results: list[StepResult] = []
        self._run_id = _event_id()

    # ── Public API ──────────────────────────────────────────────────────────

    def run(self, steps: list[dict[str, Any] | Step]) -> list[StepResult]:
        """Execute steps in order. Returns list of StepResult."""
        normalised = [self._normalise(s) for s in steps]
        self._results = []
        done_ids: set[str] = set()
        failed_ids: set[str] = set()

        self._log({"event": "run_start", "run_id": self._run_id, "steps": [s.id for s in normalised]})
        self._print(f"\n── Pipeline run {self._run_id[:8]} — {len(normalised)} step(s) ──")

        for step in normalised:
            deps = [step.depends_on] if isinstance(step.depends_on, str) else (step.depends_on or [])
            blocked_deps = [d for d in deps if d in failed_ids]
            missing_deps = [d for d in deps if d not in done_ids and d not in failed_ids]

            if blocked_deps and step.skip_on_dep_failure:
                result = self._skip(step, reason=f"dependency failed: {blocked_deps}")
            elif missing_deps:
                result = self._skip(step, reason=f"dependency not run: {missing_deps}")
            else:
                result = self._run_step(step)

            self._results.append(result)
            if result.status == "done":
                done_ids.add(step.id)
            else:
                failed_ids.add(step.id)

        self._log({"event": "run_end", "run_id": self._run_id, "summary": self._summary_dict()})
        self._print(f"\n── Run complete: {self._summary_str()} ──\n")
        return self._results

    def run_yaml(self, yaml_file: str | Path) -> list[StepResult]:
        """Load pipeline from YAML and run. Requires pyyaml."""
        try:
            import yaml  # type: ignore[import]
        except ImportError:
            raise ImportError("pyyaml is required for run_yaml(). Install with: pip install pyyaml")
        data = yaml.safe_load(Path(yaml_file).read_text(encoding="utf-8"))
        steps_raw = data.get("steps", [])
        steps = []
        for raw in steps_raw:
            fn = _resolve_callable(raw.get("fn") or raw.get("function", ""))
            steps.append({
                "id":         raw["id"],
                "fn":         fn,
                "args":       raw.get("args", {}),
                "depends_on": raw.get("depends_on"),
                "retry":      raw.get("retry", 0),
                "checkpoint": raw.get("checkpoint", True),
            })
        return self.run(steps)

    def report_md(self) -> str:
        """Generate a Markdown summary of the last run."""
        lines = ["# Pipeline Run Report", "", f"Run ID: `{self._run_id}`", ""]
        lines += ["| Step | Status | Elapsed | Cached | Error |", "|---|---|---:|---|---|"]
        for r in self._results:
            cached = "✓" if r.cached else ""
            error = r.error[:80] + "…" if len(r.error) > 80 else r.error
            icon = {"done": "✅", "failed": "❌", "skipped": "⏭", "blocked": "🚫"}.get(r.status, "?")
            lines.append(f"| {r.step_id} | {icon} {r.status} | {r.elapsed_s:.1f}s | {cached} | {error} |")
        lines += ["", f"**Result:** {self._summary_str()}"]
        return "\n".join(lines)

    def report_html(self) -> str:
        """Generate a minimal HTML summary of the last run."""
        import html
        rows = ""
        for r in self._results:
            icon = {"done": "✅", "failed": "❌", "skipped": "⏭", "blocked": "🚫"}.get(r.status, "?")
            rows += (
                f"<tr><td>{html.escape(r.step_id)}</td>"
                f"<td>{icon} {html.escape(r.status)}</td>"
                f"<td>{r.elapsed_s:.1f}s</td>"
                f"<td>{'✓' if r.cached else ''}</td>"
                f"<td>{html.escape(r.error[:120])}</td></tr>\n"
            )
        ok = all(r.status == "done" for r in self._results)
        color = "#12b76a" if ok else "#d92d20"
        return f"""<!doctype html>
<html lang="it">
<head><meta charset="utf-8"><title>Pipeline Run</title>
<style>
  body{{font-family:Arial,sans-serif;background:#f5f7fb;color:#172033;margin:0;padding:24px}}
  h1{{font-size:20px}} table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid #d8dee9}}
  th,td{{border-bottom:1px solid #e5e9f0;padding:8px;text-align:left;font-size:13px}}
  th{{background:#eef3f8}} .badge{{color:{color};font-weight:700}}
</style></head>
<body>
<h1>Pipeline Run <code>{self._run_id[:8]}</code></h1>
<p class="badge">{self._summary_str()}</p>
<table>
<thead><tr><th>Step</th><th>Status</th><th>Elapsed</th><th>Cached</th><th>Error</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</body></html>"""

    @property
    def ok(self) -> bool:
        return bool(self._results) and all(r.status == "done" for r in self._results)

    # ── Internal ────────────────────────────────────────────────────────────

    def _run_step(self, step: Step) -> StepResult:
        t0 = time.perf_counter()

        # Checkpoint hit?
        if step.checkpoint and self.checkpoint:
            cached = self.checkpoint.load(step.id, step.id)
            if cached is not None:
                self._print(f"  ⏭ {step.id} (cached)")
                self._log({"event": "step_cached", "step_id": step.id})
                return StepResult(step.id, "done", result=cached, elapsed_s=0.0, cached=True)

        self._log({"event": "step_start", "step_id": step.id, "args_keys": list(step.args.keys())})
        self._print(f"  ▶ {step.id}…", end="")

        last_error = ""
        for attempt in range(max(1, step.retry + 1)):
            if attempt > 0:
                self._print(f" retry {attempt}…", end="")
                time.sleep(step.retry_delay_s)
            try:
                result = step.fn(**step.args)
                elapsed = time.perf_counter() - t0
                self._print(f" ✓ ({elapsed:.1f}s)")
                self._log({"event": "step_done", "step_id": step.id, "elapsed_s": round(elapsed, 3)})
                self.failure_logger.record_ok("runner", step.id)
                if step.checkpoint and self.checkpoint:
                    self.checkpoint.save(step.id, step.id, result, elapsed_s=elapsed)
                return StepResult(step.id, "done", result=result, elapsed_s=elapsed)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}"

        elapsed = time.perf_counter() - t0
        self._print(f" ✗ ({elapsed:.1f}s) — {last_error.splitlines()[0]}")
        self._log({"event": "step_failed", "step_id": step.id, "error": last_error[:400], "elapsed_s": round(elapsed, 3)})
        self.failure_logger.record("runner", step.id, ok=False, error=last_error)
        return StepResult(step.id, "failed", error=last_error, elapsed_s=elapsed)

    def _skip(self, step: Step, reason: str) -> StepResult:
        self._print(f"  ⏭ {step.id} (skipped — {reason})")
        self._log({"event": "step_skipped", "step_id": step.id, "reason": reason})
        return StepResult(step.id, "skipped", error=reason)

    @staticmethod
    def _normalise(s: dict[str, Any] | Step) -> Step:
        if isinstance(s, Step):
            return s
        fn = s.get("fn")
        if fn is None or not callable(fn):
            raise ValueError(f"Step '{s.get('id', '?')}' must have a callable 'fn'.")
        return Step(
            id=str(s.get("id", "step")),
            fn=fn,
            args=dict(s.get("args", {})),
            depends_on=s.get("depends_on"),
            skip_on_dep_failure=bool(s.get("skip_on_dep_failure", True)),
            checkpoint=bool(s.get("checkpoint", True)),
            retry=int(s.get("retry", 0)),
            retry_delay_s=float(s.get("retry_delay_s", 2.0)),
        )

    def _log(self, event: dict[str, Any]) -> None:
        record = {"ts": _iso_now(), "run_id": self._run_id, **event}
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with self.log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def _print(self, msg: str, **kwargs: Any) -> None:
        if self.verbose:
            print(msg, **kwargs, flush=True)

    def _summary_dict(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self._results:
            counts[r.status] = counts.get(r.status, 0) + 1
        return counts

    def _summary_str(self) -> str:
        d = self._summary_dict()
        parts = [f"{v} {k}" for k, v in d.items()]
        return " | ".join(parts) if parts else "no steps"


def _resolve_callable(name: str) -> Callable[..., Any]:
    """Resolve 'module.submodule:function' or 'module.function' to a callable."""
    if not name:
        raise ValueError("fn/function name is empty in YAML step.")
    if ":" in name:
        mod_path, attr = name.rsplit(":", 1)
    else:
        mod_path, attr = name.rsplit(".", 1)
    import importlib
    mod = importlib.import_module(mod_path)
    return getattr(mod, attr)
