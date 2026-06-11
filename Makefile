# lead_gen - developer task runner
#
# Run `make` or `make help` to see all available targets.

VENV        := .venv
PY          := $(VENV)/bin/python
PIP         := $(VENV)/bin/pip
OLLAMA_URL  ?= http://localhost:11434
OLLAMA_MODEL ?= llama3.2
UI_PORT     ?= 8000

.DEFAULT_GOAL := help

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------
.PHONY: help
help: ## Show this help
	@echo ""
	@echo "lead_gen - available targets:"
	@echo ""
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
$(VENV): ## Create the virtual environment
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip

.PHONY: install
install: $(VENV) ## Install runtime dependencies
	$(PIP) install -r requirements.txt

.PHONY: install-dev
install-dev: $(VENV) ## Install runtime + test/dev dependencies
	$(PIP) install -r requirements-dev.txt

.PHONY: setup
setup: install-dev ## Full local setup (venv + all deps)
	@echo "Setup complete. Next: 'make ollama-pull' then 'make run'."

# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------
.PHONY: healthcheck
healthcheck: ## Verify Python env, Ollama server, and model availability
	@echo "==> Python"
	@$(PY) --version || (echo "  venv missing: run 'make setup'"; exit 1)
	@echo "==> Ollama server ($(OLLAMA_URL))"
	@curl -s --max-time 5 $(OLLAMA_URL)/api/tags >/dev/null \
		&& echo "  OK: reachable" \
		|| (echo "  FAIL: not reachable. Start it with 'ollama serve'"; exit 1)
	@echo "==> Models installed"
	@curl -s --max-time 5 $(OLLAMA_URL)/api/tags \
		| $(PY) -c "import sys,json; m=[x['name'] for x in json.load(sys.stdin)['models']]; print('  '+', '.join(m) if m else '  (none) - run make ollama-pull')"
	@echo "==> Database"
	@test -f data/leads.db && echo "  OK: data/leads.db exists" || echo "  (none yet) - run 'make run' to populate"

.PHONY: ollama-pull
ollama-pull: ## Pull the Ollama model used for generation
	ollama pull $(OLLAMA_MODEL)

# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
.PHONY: run
run: ## Run the full pipeline locally (no Airflow)
	$(PY) run_local.py

.PHONY: run-fast
run-fast: ## Run the pipeline on a small sample (limit 5)
	$(PY) run_local.py --limit 5

.PHONY: export
export: ## Export new leads to leads_export.csv
	$(PY) export.py

.PHONY: db-shell
db-shell: ## Open a SQLite shell on the leads database
	sqlite3 data/leads.db

.PHONY: emails
emails: ## Print all stored emails (subject + type)
	@sqlite3 -header -column data/leads.db \
		"SELECT business_name, email_type, subject FROM emails ORDER BY generated_at DESC;"

# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------
.PHONY: kill-port
kill-port: ## Kill any process on UI_PORT
	-lsof -ti :$(UI_PORT) | xargs kill -9 2>/dev/null || true

.PHONY: ui
ui: kill-port ## Serve the dashboard at http://localhost:$(UI_PORT)
	$(PY) -m uvicorn webapp.server:app --host 0.0.0.0 --port $(UI_PORT) --reload

# ---------------------------------------------------------------------------
# Tests & quality
# ---------------------------------------------------------------------------
.PHONY: test
test: ## Run the test suite
	$(PY) -m pytest

.PHONY: test-cov
test-cov: ## Run tests with a coverage report
	$(PY) -m pytest --cov=. --cov-report=term-missing

.PHONY: test-watch
test-watch: ## Re-run tests on the most recent failures first
	$(PY) -m pytest --lf -q

# ---------------------------------------------------------------------------
# Airflow (Docker Compose)
# ---------------------------------------------------------------------------
.PHONY: docker-up
docker-up: ## Start Airflow + Postgres (detached)
	@echo "AIRFLOW_UID=$$(id -u)" > .env.airflow
	docker compose --env-file .env.airflow up -d

.PHONY: docker-down
docker-down: ## Stop Airflow + Postgres (keep data)
	docker compose down

.PHONY: docker-reset
docker-reset: ## Stop and wipe Airflow metadata volumes
	docker compose down -v

.PHONY: docker-logs
docker-logs: ## Tail the Airflow scheduler/webserver logs
	docker compose logs -f airflow-scheduler airflow-webserver

.PHONY: trigger
trigger: ## Trigger the DAG inside the running scheduler container
	docker compose exec airflow-scheduler airflow dags trigger lead_gen_pipeline

# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------
.PHONY: clean
clean: ## Remove caches and generated artifacts (keeps the venv)
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .coverage htmlcov
	rm -f data/leads.db leads_export.csv data/leads_export.csv

.PHONY: clean-all
clean-all: clean ## Remove everything including the venv
	rm -rf $(VENV)