PY      ?= python3.12
VENV    ?= .venv
PYTHON  := $(VENV)/bin/python
N_FILES ?= 3
INCOMING_DIR ?= data/incoming
export INCOMING_DIR

.DEFAULT_GOAL := help
.PHONY: help install lint test generate

help: ## Liste les cibles disponibles
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) \
		| awk -F ':.*## ' '{printf "  %-26s %s\n", $$1, $$2}'

install: ## Crée .venv et installe les dépendances (PY=chemin/vers/python3.12 si besoin)
	$(PY) -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"

lint: ## Vérifie le code (ruff check + ruff format --check)
	$(PYTHON) -m ruff check src tests
	$(PYTHON) -m ruff format --check src tests

test: ## Tests unitaires (sans base, S3, Tesseract ni SLM)
	$(PYTHON) -m pytest -m "not integration"

generate: ## Génère les CSV déterministes dans data/incoming/ (N_FILES=3)
	$(PYTHON) -m pipeline.generate --n-files $(N_FILES) --output-dir $(INCOMING_DIR)
