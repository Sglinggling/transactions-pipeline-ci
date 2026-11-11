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
S3_ENDPOINT_URL ?= http://localhost:9000
S3_ACCESS_KEY ?= pipeline
S3_SECRET_KEY ?= pipelinesecret
S3_BUCKET ?= transactions
S3_PREFIX ?= incoming/
SLM_URL ?= http://localhost:11434
# Plus petit modèle à 100 % de champs exacts sur les textes de test (README).
SLM_MODEL ?= qwen3.5:0.8b
export DATABASE_URL S3_ENDPOINT_URL S3_ACCESS_KEY S3_SECRET_KEY S3_BUCKET S3_PREFIX
export SLM_URL SLM_MODEL

.DEFAULT_GOAL := help
.PHONY: help install lint test test-integration generate generate-images generate-texts \
        db-up db-init db-reset db-down s3-up s3-init s3-upload s3-verify slm-up slm-pull \
        run prepare pipeline demo-retry demo-retry-exhausted demo-s3-retry \
        demo-s3-retry-exhausted demo-s3-outage

# Les démos de retry ne traitent que les CSV : elles démontrent le retry, pas le SLM.
DEMO_ENV := PIPELINE_EXTENSIONS=.csv

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

generate-images: ## Génère les images PNG d'avis de virement (OCR)
	$(PYTHON) -m pipeline.generate_images --output-dir $(INCOMING_DIR)

generate-texts: ## Génère les textes en langage naturel (SLM)
	$(PYTHON) -m pipeline.generate_texts --output-dir $(INCOMING_DIR)

db-up: ## Démarre PostgreSQL et attend qu'il soit healthy
	docker compose up -d --wait postgres

db-init: ## Applique sql/schema.sql (idempotent)
	$(PYTHON) -m pipeline.db init

db-reset: ## Vide toutes les tables (données de dev perdues)
	$(PYTHON) -m pipeline.db reset

db-down: ## Arrête tous les services (Postgres, S3, Ollama) ; volumes conservés
	docker compose down

s3-up: ## Démarre RustFS (S3) et attend qu'il soit healthy
	docker compose up -d --wait s3

s3-init: ## Crée le bucket (idempotent)
	$(PYTHON) -m pipeline.storage init

s3-upload: ## Envoie data/incoming/*.csv, *.png, *.txt sous S3_PREFIX
	$(PYTHON) -m pipeline.storage upload --source-dir $(INCOMING_DIR)

s3-verify: ## Échoue si un fichier local manque dans le bucket ou a une autre taille
	$(PYTHON) -m pipeline.storage verify --source-dir $(INCOMING_DIR)

slm-up: ## Démarre Ollama (SLM) et attend qu'il soit healthy
	docker compose up -d --wait ollama

# Ollama (root) crée certains fichiers en 0600, que le cache CI ne pourrait pas
# archiver : on les rend lisibles hors du conteneur après le téléchargement.
slm-pull: ## Télécharge le modèle SLM_MODEL s'il est absent
	docker compose exec -T ollama ollama pull $(SLM_MODEL)
	docker compose exec -T ollama chmod -R a+rX /models

# Lit uniquement le bucket S3 (S3_BUCKET / S3_PREFIX), jamais le dossier local.
run: ## Traite les objets du bucket (.csv, .png, .txt) et les insère en base
	$(PYTHON) -m pipeline.run

# Étapes enchaînées explicitement : ordre garanti même avec make -j.
prepare: ## Tout make pipeline sauf run (services, modèle, génération, upload, vérification)
	$(MAKE) s3-up
	$(MAKE) db-up
	$(MAKE) slm-up
	$(MAKE) slm-pull
	$(MAKE) s3-init
	$(MAKE) generate
	$(MAKE) generate-images
	$(MAKE) generate-texts
	$(MAKE) s3-upload
	$(MAKE) s3-verify
	$(MAKE) db-init

pipeline: ## Pipeline complète en une commande (prepare puis run), rejouable
	$(MAKE) prepare
	$(MAKE) run

# Démos de retry : base vidée pour que les objets soient réellement insérés.
demo-retry: ## Démo retry DB : 2 pannes simulées puis succès (CSV seuls)
	$(MAKE) prepare
	$(MAKE) db-reset
	$(DEMO_ENV) SIMULATE_DB_FAILURES=2 $(PYTHON) -m pipeline.run

demo-retry-exhausted: ## Démo retry DB : plus de pannes que de tentatives, sort en erreur
	$(MAKE) prepare
	$(MAKE) db-reset
	$(DEMO_ENV) SIMULATE_DB_FAILURES=$$(( $${DB_MAX_ATTEMPTS:-4} + 1 )) $(PYTHON) -m pipeline.run

demo-s3-retry: ## Démo retry S3 : 2 pannes de téléchargement puis succès (CSV seuls)
	$(MAKE) prepare
	$(MAKE) db-reset
	$(DEMO_ENV) SIMULATE_S3_FAILURES=2 $(PYTHON) -m pipeline.run

demo-s3-retry-exhausted: ## Démo retry S3 : plus de pannes que de tentatives, sort en erreur
	$(MAKE) prepare
	$(MAKE) db-reset
	$(DEMO_ENV) SIMULATE_S3_FAILURES=$$(( $${S3_MAX_ATTEMPTS:-4} + 1 )) $(PYTHON) -m pipeline.run

# Panne RÉELLE : conteneur S3 arrêté, pipeline lancée, conteneur redémarré
# OUTAGE_SECONDS plus tard. Avec 6 tentatives (attentes 1+2+4+8+16 s), la
# dernière a lieu ~31 s après le début : large marge pour le redémarrage de
# RustFS, même sur un runner CI lent.
OUTAGE_SECONDS ?= 4
OUTAGE_S3_MAX_ATTEMPTS ?= 6

demo-s3-outage: ## Démo panne S3 réelle : conteneur arrêté puis redémarré (CSV seuls)
	$(MAKE) prepare
	$(MAKE) db-reset
	docker compose stop s3
	( sleep $(OUTAGE_SECONDS) && echo ">>> $(OUTAGE_SECONDS) s écoulées : redémarrage du conteneur S3" \
		&& docker compose start s3 ) & \
	$(DEMO_ENV) S3_MAX_ATTEMPTS=$(OUTAGE_S3_MAX_ATTEMPTS) $(PYTHON) -m pipeline.run; \
	status=$$?; wait; exit $$status
