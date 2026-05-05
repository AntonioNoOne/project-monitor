"""
monitor.checkpoint — Skip-if-done cache for expensive pipeline steps.

Extracted and generalized from AgentsLab's GLM-OCR second-pass caching pattern.

Usage:
    from monitor import CheckpointStore

    store = CheckpointStore(".cache/pipeline")

    if store.is_done("extract", "visura_catastale.pdf"):
        data = store.load("extract", "visura_catastale.pdf")
    else:
        data = run_expensive_extraction(...)
        store.save("extract", "visura_catastale.pdf", data)

    # Or use the context helper:
    with store.step("extract", "visura_catastale.pdf") as cp:
        if not cp.hit:
            cp.result = run_expensive_extraction(...)
    data = cp.result
"""
from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator


def _cache_key(step: str, item_id: str) -> str:
    """Stable filesystem-safe key from step name + item identifier."""
    raw = f"{step}::{item_id}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    safe_step = "".join(c if c.isalnum() or c in "-_" else "_" for c in step)[:40]
    return f"{safe_step}_{digest}"


@dataclass
class CheckpointEntry:
    """Holds result + metadata for a single cached item."""
    step: str
    item_id: str
    result: Any = None
    hit: bool = False
    saved_at: str = ""
    elapsed_s: float = 0.0


class CheckpointStore:
    """File-based checkpoint store. Thread-safe for single-process use."""

    def __init__(self, cache_dir: str | Path, *, force: bool = False) -> None:
        self.cache_dir = Path(cache_dir)
        self.force = force  # if True, always re-run even if cached

    def _path(self, step: str, item_id: str) -> Path:
        key = _cache_key(step, item_id)
        return self.cache_dir / step / f"{key}.json"

    def is_done(self, step: str, item_id: str) -> bool:
        if self.force:
            return False
        return self._path(step, item_id).exists()

    def load(self, step: str, item_id: str) -> Any:
        """Load cached result. Returns None if not found."""
        path = self._path(step, item_id)
        if not path.exists():
            return None
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            return envelope.get("result")
        except Exception:
            return None

    def save(self, step: str, item_id: str, result: Any, *, elapsed_s: float = 0.0) -> Path:
        """Save result to cache. Creates directories as needed."""
        path = self._path(step, item_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {
            "step": step,
            "item_id": item_id,
            "saved_at": _iso_now(),
            "elapsed_s": round(elapsed_s, 3),
            "result": result,
        }
        path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def invalidate(self, step: str, item_id: str) -> bool:
        """Remove a cached entry. Returns True if deleted."""
        path = self._path(step, item_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def invalidate_step(self, step: str) -> int:
        """Remove all cached entries for a step. Returns count deleted."""
        step_dir = self.cache_dir / step
        if not step_dir.exists():
            return 0
        count = 0
        for path in step_dir.glob("*.json"):
            path.unlink()
            count += 1
        return count

    def stats(self) -> dict[str, Any]:
        """Return a dict with cache statistics."""
        total = 0
        by_step: dict[str, int] = {}
        if self.cache_dir.exists():
            for path in self.cache_dir.rglob("*.json"):
                step = path.parent.name
                by_step[step] = by_step.get(step, 0) + 1
                total += 1
        return {"cache_dir": str(self.cache_dir), "total": total, "by_step": by_step}

    @contextmanager
    def step(self, step: str, item_id: str) -> Generator[CheckpointEntry, None, None]:
        """Context manager: loads cache hit or marks entry for saving after block.

        Example:
            with store.step("extract", "doc.pdf") as cp:
                if not cp.hit:
                    cp.result = run_extraction("doc.pdf")
            # result is auto-saved if cp.result was set and not a cache hit
        """
        entry = CheckpointEntry(step=step, item_id=item_id)
        if self.is_done(step, item_id):
            entry.result = self.load(step, item_id)
            entry.hit = True
        t0 = time.perf_counter()
        yield entry
        elapsed = time.perf_counter() - t0
        if not entry.hit and entry.result is not None:
            self.save(step, item_id, entry.result, elapsed_s=elapsed)
            entry.elapsed_s = elapsed


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
