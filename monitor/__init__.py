"""
project-monitor — lightweight pipeline monitor for Python projects.

Install from any PC (no local path needed):
    pip install "git+https://github.com/jump-group/RYZE-POC-Perizia.git#subdirectory=monitor"

Or from local clone:
    pip install -e C:\\Users\\anto_\\Desktop\\Progetti\\RYZE\\monitor
"""
from monitor.checkpoint import CheckpointStore
from monitor.failures import FailureLogger
from monitor.health import HealthCheck
from monitor.runner import StepRunner

__all__ = ["StepRunner", "HealthCheck", "CheckpointStore", "FailureLogger"]
__version__ = "0.1.0"
