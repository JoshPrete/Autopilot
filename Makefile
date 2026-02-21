PYTHON ?= python3

.PHONY: lint format check test run

lint:
	ruff check . --config pyproject.toml
	black --check . --config pyproject.toml

format:
	black . --config pyproject.toml
	ruff check --fix . --config pyproject.toml

check:
	$(PYTHON) -m py_compile app/main.py
	$(PYTHON) -m py_compile scripts/daily_autopilot.py
	$(PYTHON) -m py_compile data/storage.py
	$(PYTHON) -m py_compile data/xero.py
	$(PYTHON) -m py_compile analysis/intelligence.py
	$(PYTHON) -m py_compile app/chat.py

test:
	$(PYTHON) -m pytest tests/ -x -q

run:
	uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
