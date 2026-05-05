"""
monitor.cli — Command-line interface for project-monitor.

Commands:
    pmonitor health [--env KEY ...] [--cmd CMD ...] [--file PATH ...] [--url URL ...]
    pmonitor status [--log FILE]
    pmonitor run YAML_FILE [--log FILE] [--checkpoint DIR] [--force]
    pmonitor cache-stats [--checkpoint DIR]
    pmonitor cache-clear STEP [--checkpoint DIR]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def cmd_health(args: argparse.Namespace) -> int:
    from monitor.health import HealthCheck
    h = HealthCheck(name=args.name)
    for var in args.env or []:
        h.require_env(var)
    for cmd in args.cmd or []:
        h.require_command(cmd)
    for path in args.file or []:
        h.require_file(path)
    for url in args.url or []:
        h.require_url(url, required=False)
    report = h.run()
    print(report.summary())
    return 0 if report.ok else 1


def cmd_status(args: argparse.Namespace) -> int:
    from monitor.failures import FailureLogger
    log_path = Path(args.log)
    logger = FailureLogger(log_path)
    summary = logger.summary()
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from monitor.runner import StepRunner
    runner = StepRunner(
        log_file=args.log,
        checkpoint_dir=args.checkpoint,
        force=args.force,
        verbose=not args.quiet,
    )
    results = runner.run_yaml(args.yaml)
    print(runner.report_md())
    return 0 if runner.ok else 1


def cmd_cache_stats(args: argparse.Namespace) -> int:
    from monitor.checkpoint import CheckpointStore
    store = CheckpointStore(args.checkpoint)
    print(json.dumps(store.stats(), indent=2, ensure_ascii=False))
    return 0


def cmd_cache_clear(args: argparse.Namespace) -> int:
    from monitor.checkpoint import CheckpointStore
    store = CheckpointStore(args.checkpoint)
    n = store.invalidate_step(args.step)
    print(f"Cleared {n} cached entries for step '{args.step}'.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pmonitor",
        description="project-monitor — pipeline health, checkpoints and failure logging.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # health
    p_health = sub.add_parser("health", help="Run pre-flight checks.")
    p_health.add_argument("--name", default="pipeline")
    p_health.add_argument("--env",  nargs="*", metavar="VAR",  help="Required env variables")
    p_health.add_argument("--cmd",  nargs="*", metavar="CMD",  help="Required CLI commands")
    p_health.add_argument("--file", nargs="*", metavar="PATH", help="Required files/dirs")
    p_health.add_argument("--url",  nargs="*", metavar="URL",  help="Optional URLs to ping")

    # status
    p_status = sub.add_parser("status", help="Show failure log summary.")
    p_status.add_argument("--log", default="logs/failures.jsonl")

    # run
    p_run = sub.add_parser("run", help="Execute a YAML pipeline.")
    p_run.add_argument("yaml", metavar="YAML_FILE")
    p_run.add_argument("--log",        default="logs/pipeline.jsonl")
    p_run.add_argument("--checkpoint", default=".cache/pipeline", metavar="DIR")
    p_run.add_argument("--force",      action="store_true", help="Ignore checkpoint cache")
    p_run.add_argument("--quiet",      action="store_true")

    # cache-stats
    p_cs = sub.add_parser("cache-stats", help="Show checkpoint cache stats.")
    p_cs.add_argument("--checkpoint", default=".cache/pipeline", metavar="DIR")

    # cache-clear
    p_cc = sub.add_parser("cache-clear", help="Clear cache for a step.")
    p_cc.add_argument("step", metavar="STEP_ID")
    p_cc.add_argument("--checkpoint", default=".cache/pipeline", metavar="DIR")

    args = parser.parse_args()
    handlers = {
        "health":      cmd_health,
        "status":      cmd_status,
        "run":         cmd_run,
        "cache-stats": cmd_cache_stats,
        "cache-clear": cmd_cache_clear,
    }
    sys.exit(handlers[args.command](args))


if __name__ == "__main__":
    main()
