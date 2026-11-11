PY      ?= python3.12
VENV    ?= .venv
PYTHON  := $(VENV)/bin/python
N_FILES ?= 3
INCOMING_DIR ?= data/incoming
export INCOMING_DIR

# Variables locales facultatives (voir .env.example), lues aussi par docker compose.
-include .env
# Toutes les variables définies dans .env sont transmises aux commandes (Python, compose).
export $(shell sed -n 's/^\([A-Z_][A-Z0-9_]*\)=.*/\1/p' .env 2>/dev/null)
DATABASE_URL ?= postgresql://pipeline:pipeline@localhost:5432/transactions
export DATABASE_URL

.DEFAULT_GOAL := help
.PHONY: help install lint test test-integration generate db-up db-init db-reset db-down

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

test-integration: ## Tests d'intégration (services démarrés requis ; vide les tables)
	$(PYTHON) -m pytest -m integration

generate: ## Génère les CSV déterministes dans data/incoming/ (N_FILES=3)
	$(PYTHON) -m pipeline.generate --n-files $(N_FILES) --output-dir $(INCOMING_DIR)

db-up: ## Démarre PostgreSQL et attend qu'il soit healthy
	docker compose up -d --wait postgres

db-init: ## Applique sql/schema.sql (idempotent)
	$(PYTHON) -m pipeline.db init

db-reset: ## Vide toutes les tables (données de dev perdues)
	$(PYTHON) -m pipeline.db reset

db-down: ## Arrête tous les services ; volumes conservés
	docker compose down
