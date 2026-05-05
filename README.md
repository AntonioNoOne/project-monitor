# project-monitor

**Lightweight pipeline monitor for Python projects.**  
Zero mandatory dependencies (stdlib only). Extracted from [AgentsLab](https://github.com/jump-group/AgentsLab).

---

## Install

```bash
# From GitHub (recommended — works on any PC)
pip install git+https://github.com/AntonioNoOne/project-monitor.git

# Specific branch or tag
pip install git+https://github.com/AntonioNoOne/project-monitor.git@main
pip install git+https://github.com/AntonioNoOne/project-monitor.git@v0.1.0

# Local clone
pip install -e /path/to/project-monitor
```

Add to `requirements.txt`:
```
git+https://github.com/AntonioNoOne/project-monitor.git
```

---

## Componenti

| Classe | Import | Funzione |
|---|---|---|
| `HealthCheck` | `from monitor import HealthCheck` | Pre-flight: verifica env, comandi, file, URL prima di avviare |
| `CheckpointStore` | `from monitor import CheckpointStore` | Cache skip-if-done con `--force`, context manager |
| `FailureLogger` | `from monitor import FailureLogger` | Classifica errori + log JSONL con hint umano |
| `StepRunner` | `from monitor import StepRunner` | Esegue step in ordine con log JSONL, retry, dipendenze |

---

## Uso rapido

### Health check pre-pipeline

```python
from monitor import HealthCheck

h = HealthCheck(name="RYZE pipeline")
h.require_env("GOOGLE_API_KEY", hint="Imposta in .env")
h.require_env("OLLAMA_MODEL", required=False, default="qwen2.5vl:7b")
h.require_command("python")
h.require_command("ollama", required=False, hint="ollama serve")
h.require_file(".venv/Scripts/python.exe", hint="Crea il virtualenv prima")
h.require_url("http://localhost:11434", required=False, hint="Ollama non in ascolto")
h.run_or_exit()   # stampa report, esce con codice 1 se un check required fallisce
```

### Step runner con checkpoint

```python
from monitor import StepRunner

def preprocess(pdf): ...
def extract(folder): ...
def validate(folder): ...

runner = StepRunner(
    log_file="logs/pipeline.jsonl",
    checkpoint_dir=".cache/pipeline",  # skip step già fatti
)
results = runner.run([
    {"id": "preprocess", "fn": preprocess, "args": {"pdf": "doc.pdf"}},
    {"id": "extract",    "fn": extract,    "args": {"folder": "out/"}, "depends_on": "preprocess"},
    {"id": "validate",   "fn": validate,   "args": {"folder": "out/"}, "depends_on": "extract"},
])

print(runner.report_md())    # Markdown con tabella step/status/elapsed
# runner.report_html()       # oppure HTML
```

### Checkpoint manuale

```python
from monitor import CheckpointStore

store = CheckpointStore(".cache/pipeline")

# Context manager — auto-salva se cp.result viene impostato
with store.step("extract", "visura.pdf") as cp:
    if not cp.hit:
        cp.result = run_expensive_extraction("visura.pdf")
# result è in cp.result

# Forza riesecuzione di tutti gli step
store_force = CheckpointStore(".cache/pipeline", force=True)
```

### Failure logger

```python
from monitor import FailureLogger

log = FailureLogger("logs/failures.jsonl")

try:
    result = run_step()
except Exception as exc:
    event = log.record(source="pipeline", name="extract_step", ok=False, error=str(exc))
    print(event["hint"])        # suggerimento umano
    print(event["failure_type"])  # missing_path | auth_required | network | output_parse | ...
```

---

## CLI (`pmonitor`)

```bash
# Pre-flight check
pmonitor health --name "RYZE" \
  --env GOOGLE_API_KEY OLLAMA_MODEL \
  --cmd python ollama \
  --url http://localhost:11434

# Stato fallimenti recenti
pmonitor status --log logs/failures.jsonl

# Esegui pipeline da YAML (richiede: pip install project-monitor[yaml])
pmonitor run pipeline.yaml --checkpoint .cache/pipeline

# Cache info e pulizia
pmonitor cache-stats --checkpoint .cache/pipeline
pmonitor cache-clear extract --checkpoint .cache/pipeline
```

---

## Uso in RYZE (POC Perizia)

```bash
pip install git+https://github.com/jump-group/project-monitor.git
```

```python
# poc/pipeline.py
from monitor import HealthCheck, StepRunner
from scripts.preprocess_fascicolo import run as preprocess
from scripts.extract_with_ollama import run as extract
from scripts.validate_fascicolo import run as validate
from scripts.generate_matrix_validation_report import run as report

h = HealthCheck(name="RYZE")
h.require_env("OLLAMA_MODEL", default="qwen2.5vl:7b")
h.require_command("ollama")
h.require_url("http://localhost:11434", hint="ollama serve")
h.run_or_exit()

runner = StepRunner(log_file="poc/logs/pipeline.jsonl", checkpoint_dir=".cache/ryze")
runner.run([
    {"id": "preprocess", "fn": preprocess, "args": {...}},
    {"id": "extract",    "fn": extract,    "args": {...}, "depends_on": "preprocess"},
    {"id": "validate",   "fn": validate,   "args": {...}, "depends_on": "extract"},
    {"id": "report",     "fn": report,     "args": {...}, "depends_on": "validate"},
])
```

---

## Uso in AgentsLab

```bash
# Nel venv di AgentsLab
pip install git+https://github.com/jump-group/project-monitor.git
```

```python
from monitor import FailureLogger, CheckpointStore

# Sostituisce failure_learning.py (retrocompatibile)
log = FailureLogger("logs/failure_events.jsonl")
```

---

## Struttura

```
project-monitor/
├── pyproject.toml
├── README.md
├── .gitignore
└── monitor/
    ├── __init__.py       # StepRunner, HealthCheck, CheckpointStore, FailureLogger
    ├── health.py
    ├── checkpoint.py
    ├── failures.py
    ├── runner.py
    └── cli.py
```

## Requirements

- Python ≥ 3.10
- Dipendenze obbligatorie: **nessuna** (solo stdlib)
- Dipendenze opzionali: `pyyaml` (per `run_yaml()`), `rich` (output colorato)
