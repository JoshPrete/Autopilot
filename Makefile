VENV_BIN := $(if $(wildcard .venv/bin/python),.venv/bin,)
PYTHON ?= $(if $(VENV_BIN),$(VENV_BIN)/python,python3)
RUFF ?= $(if $(VENV_BIN),$(VENV_BIN)/ruff,ruff)
BLACK ?= $(if $(VENV_BIN),$(VENV_BIN)/black,black)
PYTEST ?= $(if $(VENV_BIN),$(VENV_BIN)/pytest,pytest)
UVICORN ?= $(if $(VENV_BIN),$(VENV_BIN)/uvicorn,uvicorn)

.PHONY: lint format check test run tomorrow verify

lint:
	$(RUFF) check . --config pyproject.toml
	$(BLACK) --check . --config pyproject.toml

format:
	$(BLACK) . --config pyproject.toml
	$(RUFF) check --fix . --config pyproject.toml

check:
	$(PYTHON) -m py_compile app/main.py
	$(PYTHON) -m py_compile scripts/daily_autopilot.py
	$(PYTHON) -m py_compile scripts/tomorrow_cli.py
	$(PYTHON) -m py_compile data/storage.py
	$(PYTHON) -m py_compile data/xero.py
	$(PYTHON) -m py_compile analysis/intelligence.py
	$(PYTHON) -m py_compile analysis/tomorrow_report.py
	$(PYTHON) -m py_compile app/chat.py

test:
	$(PYTEST) tests/ -x -q

tomorrow:
	$(PYTHON) scripts/tomorrow_cli.py tomorrow $(ARGS)

verify:
ifndef DATE
	$(error DATE is required. Example: make verify DATE=2026-02-20)
endif
	$(PYTHON) scripts/tomorrow_cli.py verify --date $(DATE) $(ARGS)

run:
	$(UVICORN) app.main:app --host 0.0.0.0 --port 8080 --reload

run-daily:
	$(PYTHON) scripts/daily_autopilot.py --step all

run-daily-dry:
	$(PYTHON) scripts/daily_autopilot.py --step all --dry-run
