# transactions-pipeline-ci

[![CI](https://github.com/Sglinggling/transactions-pipeline-ci-v2/actions/workflows/ci.yml/badge.svg)](https://github.com/Sglinggling/transactions-pipeline-ci-v2/actions/workflows/ci.yml)

Pipeline data de transactions financières construite pas à pas dans le cadre
d'un cours CI/CD. Trois sources convergent vers une base PostgreSQL :

- des **fichiers CSV** ;
- des **images** d'avis de virement, lues par **OCR** (Tesseract) ;
- des **textes en langage naturel**, interprétés par un **SLM local** (Ollama).

Les fichiers sont déposés dans un stockage **S3 local** (RustFS), récupérés en
mémoire, validés par des règles communes, traités par des fonctions pures puis
insérés en base dans une transaction par fichier, avec anti-doublon par hash
du contenu et retries sur la base, le S3 et le SLM. Tout se lance avec `make`,
et la même chaîne tourne dans GitHub Actions.

## Architecture

```
  CSV                         Images                        Textes
  make generate               make generate-images          make generate-texts
  data/incoming/*.csv         data/incoming/*.png           data/incoming/*.txt
            │                           │                             │
            └───────────────────── make s3-upload ────────────────────┘
                                        ▼
                 S3 (RustFS) s3://bucket/prefix/   ◄── make s3-verify
                                        │
                                        ▼  make run
         récupération : storage.py (listing, objet -> bytes, SHA-256, retry S3)
                                        │
            ┌───────────────────────────┼─────────────────────────────┐
            ▼ .csv                      ▼ .png                        ▼ .txt
      parsing CSV                OCR : ocr.py                  SLM : slm.py
      load_transactions          (Tesseract)                   (Ollama, retry SLM)
            │                           ▼                             ▼
            │                    extraction.py (pur)           text_extraction.py (pur)
            │                           │                      normalisation + garde-fou
            └───────────────────────────┼─────────────────────────────┘
                                        ▼
         validation commune : loading.validate_transactions (même schéma)
                                        ▼
         traitement : processing.py (fonctions pures)
                                        ▼
         base : db.py -> PostgreSQL (1 transaction SQL par objet, retry DB)
```

| Couche | Module | Rôle |
|---|---|---|
| Génération (zone de dépôt locale) | `generate.py`, `generate_images.py`, `generate_texts.py` | données de test déterministes |
| Stockage | `storage.py` | bucket, upload, liste, téléchargement en mémoire, vérification, classement des erreurs S3 |
| Chargement | `loading.py`, `ocr.py` + `extraction.py`, `slm.py` + `text_extraction.py` | CSV, OCR, SLM, puis validation commune (`validate_transactions`) |
| Traitement | `processing.py` | flag des montants > 5000, sommes par IBAN / banque (fonctions pures) |
| Base | `db.py`, `sql/schema.sql` | schéma, insertion transactionnelle, anti-doublon |
| Orchestration | `run.py` | aiguillage par extension, retries, une ligne de log par objet |
| Fiabilité | `retry.py`, `fault_injection.py` | retry générique (backoff exponentiel), pannes simulées |

## Démarrage rapide

Prérequis :

- **Python 3.12** ;
- **Docker** avec Docker Compose (PostgreSQL, RustFS et Ollama tournent en conteneurs ;
  prévoir environ 9 Go d'images et 1,3 Go pour le modèle SLM) ;
- **Tesseract** : `brew install tesseract` (macOS) ou
  `sudo apt-get install tesseract-ocr tesseract-ocr-eng` (Ubuntu) ;
- `make` et `git`.

```bash
git clone https://github.com/Sglinggling/transactions-pipeline-ci-v2.git
cd transactions-pipeline-ci-v2
make install        # si python3.12 n'est pas dans le PATH : make install PY=/chemin/vers/python3.12
make pipeline       # services, modèle, génération, upload S3, vérification, schéma, traitement
```

`make pipeline` est rejouable : un second lancement ne réinsère rien et sort
en code 0. `make help` liste toutes les cibles. La configuration par défaut
convient au développement ; pour la modifier, `cp .env.example .env` puis
éditer (toutes les variables y sont documentées).

```
INFO    Source : s3://transactions/incoming/ (types : .csv, .png, .txt)
INFO    TRAITÉ        incoming/message_001.txt : 1 transaction(s), 1 flag(s)
INFO    TRAITÉ        incoming/transactions_001.csv : 5 transaction(s), 1 flag(s)
INFO    TRAITÉ        incoming/virement_001.png : 1 transaction(s), 1 flag(s)
…
INFO    Résumé : 10 traité(s), 0 déjà traité(s), 0 en erreur, 0 non examiné(s)
```

Arrêt des services : `make db-down` (les volumes de données sont conservés).

## Correspondance avec le sujet

La numérotation suit l'énoncé.

| Exigence du sujet | Implémentation | Vérification |
|---|---|---|
| **N1.1** Génération des données CSV (plusieurs fichiers de 5 à 10 transactions, 7 colonnes, données non totalement aléatoires) | `generate.py`, `make generate` | `tests/test_generate.py` (fichiers identiques octet pour octet, cas de test obligatoires présents) ; étape CI « Générer les CSV » |
| **N1.2** Chargement et traitement (sommes par `iban_origine`, `banque_source`, `iban_destinataire`, flag si montant > 5000 ; chargement séparé du traitement ; fonctions pures) | `loading.py` (chargement et validation), `processing.py` (traitements purs) | `tests/test_loading.py` (CSV invalides), `tests/test_processing.py` (valeurs en dur, 5000.00 → False, 5000.01 → True, pureté) |
| **N1.3** Base de données relationnelle et fonction d'insertion | `docker-compose.yml` (postgres:16), `sql/schema.sql`, `db.py`, `make db-up` / `db-init` | `tests/integration/test_db.py` (valeurs exactes, cumul, rollback complet) |
| **N1.4** Pipeline complète automatique (génération, chargement et traitement, mise en place de la base, insertion) | `run.py`, `make pipeline` | étapes CI « Lancer la pipeline » et « Relancer make pipeline » ; `tests/integration/test_pipeline.py` |
| **N1.5** Tests d'intégration automatiques (données insérées, valeurs correctes, traitements attendus, flags), exécutés dans la CI | `tests/integration/`, `make test-integration` | job CI `integration` ; `test_pipeline.py::test_full_run` et `::test_flags`, `test_db.py` |
| **N1.6** Simulation d'un échec de pipeline avec captures (cause, échec, message d'erreur, correction, réussite) | PR #1 : régression volontaire puis correctif | [docs/scenario-echec.md](docs/scenario-echec.md) et ses captures (`docs/img/`) |
| **N1.7** Retry lors de l'insertion (détection, attente, plusieurs tentatives, maximum, arrêt de la pipeline, erreurs affichées) et anti-doublon, y compris lors d'un retry | `retry.py`, `fault_injection.py`, `db.py` (table `processed_files`, hash du contenu, même transaction), `make demo-retry` / `demo-retry-exhausted` | `tests/test_retry.py`, `tests/integration/test_retry_pipeline.py` (pannes puis succès, panne après commit sans doublon, épuisement) ; étape CI « Démo retry » |
| **N2.1** Stockage des fichiers dans un service S3 local | RustFS dans `docker-compose.yml`, `storage.py`, `make s3-up` | `tests/integration/test_storage.py` (aller-retour octet pour octet) |
| **N2.2** Upload automatique (démarrer le service, créer le bucket, générer, envoyer, vérifier la présence) | `make s3-up` / `s3-init` / `s3-upload` / `s3-verify`, enchaînées par `make pipeline` | étapes CI « Démarrer S3 », « Créer le bucket », « Upload », « Vérifier la présence » ; `test_storage.py` (objet manquant, taille différente) |
| **N2.3** Lecture des données depuis le S3 (plus de dépendance au système de fichiers ; anti-doublon conservé) | `run.py`, `storage.download_object` | `test_pipeline.py::test_does_not_depend_on_local_files`, `::test_second_run_inserts_nothing`, `::test_same_content_under_another_key_is_ignored` |
| **N2.4** Retry lors de la récupération depuis le S3 (détection, erreur affichée, attente, plusieurs tentatives, maximum, arrêt propre) | classement des erreurs dans `storage.py`, `retry.py`, `make demo-s3-retry` / `demo-s3-retry-exhausted` / `demo-s3-outage` | `tests/test_storage.py`, `tests/integration/test_s3_retry.py` ; étapes CI « Démo retry S3 » et « Démo panne S3 réelle » |
| **N3 option 2** Extraction de transactions depuis des images avec OCR | `generate_images.py`, `ocr.py`, `extraction.py`, `make generate-images` | `tests/test_extraction.py`, `tests/integration/test_ocr.py`, `test_pipeline_images.py` ; étape CI « Générer les images » |
| **N3 option 3** Extraction depuis du texte avec un SLM | `generate_texts.py`, `slm.py`, `text_extraction.py`, Ollama, `make generate-texts` / `slm-pull` | `tests/test_text_extraction.py`, `tests/test_slm.py`, `tests/integration/test_slm_extraction.py`, `test_pipeline_texts.py` ; étapes CI « Ollama » et « Générer les textes » |

Le niveau 3 demande deux améliorations : les options 1 (pipeline CD) et 4
(Terraform / OpenTofu) n'ont pas été retenues.

## Choix techniques

**PostgreSQL 16.** Base relationnelle standard, transactions ACID et
`INSERT ... ON CONFLICT` natif : l'anti-doublon et le cumul des totaux
(`total = table.total + EXCLUDED.total`) sont garantis par la base. Montants
en `NUMERIC(14,2)`, convertis en `Decimal` avant insertion (jamais de float).
Accès en psycopg 3, connexion construite uniquement depuis `DATABASE_URL`.

**RustFS 1.0.4 plutôt que SeaweedFS.** Un seul conteneur, identifiants passés
directement par `RUSTFS_ACCESS_KEY` / `RUSTFS_SECRET_KEY`, endpoint `/health`
officiel (curl présent dans l'image) pour le healthcheck. Chez SeaweedFS, les
identifiants par variables d'environnement ne sont qu'un repli, ignoré dès
qu'une configuration existe, et aucun endpoint de santé n'est documenté.
Image épinglée, client boto3.

**Tesseract** (modèle `eng`, `--psm 6 --dpi 300`). Moteur OCR libre, installé
par le gestionnaire de paquets en local comme en CI. Les avis de virement
sont rendus avec une police embarquée (DejaVu Sans Mono, licence libre
Bitstream Vera / DejaVu, texte dans `src/pipeline/fonts/LICENSE-DejaVu.txt`),
en 48 px binaire, 1800 x 900 px, 300 dpi : parmi 12 réglages comparés,
c'est le seul sans aucune erreur à la fois sur Tesseract 5.5.4 (brew, macOS)
et 5.3.4 (Ubuntu 24.04). Le runner CI est épinglé sur `ubuntu-24.04` pour
que la version de Tesseract ne change pas toute seule ; le réglage a aussi
passé sur Ubuntu 26.04 (Tesseract 5.5.0) avant cet épinglage.

**SLM : `qwen3.5:0.8b` servi par Ollama 0.40.15.** Cinq modèles de 0,5 à 1,5
milliard de paramètres comparés sur les 4 textes de test (28 champs), plus
un texte sans transaction qui doit être rejeté ; mesures sous Docker sur CPU
(Mac, 10 cœurs) :

| Modèle (tag Ollama) | Paramètres | Champs exacts | Texte sans transaction | Temps / texte |
|---|---|---|---|---|
| `qwen2.5:0.5b` | 0,5 B | 23/28 (émetteur et destinataire inversés) | rejeté | ~2,5 s |
| **`qwen3.5:0.8b`** | **0,8 B** | **28/28** | **rejeté** | **~5–8 s** |
| `gemma3:1b` | 1 B | 26/28 (IBAN altéré, bloqué par le garde-fou) | rejeté | ~8 s |
| `llama3.2:1b` | 1,2 B | 24/28 (sens inversé, IBAN altéré, mauvaise date) | rejeté | ~3,7 s |
| `qwen2.5:1.5b` | 1,5 B | 26/28 (émetteur et destinataire inversés) | rejeté | ~3,1 s |

Retenu : le plus petit à 100 % de champs exacts, en local et dans la CI
(identifiant `de63045f2975`). Appels déterministes : température 0, seed 42,
raisonnement désactivé (`think: false`), sortie JSON contrainte par un
schéma. Le modèle ne fait que **repérer et recopier** les valeurs ; les
conversions (dates en toutes lettres, « 14h30 », « 1 875,40 euros », pays →
code ISO) sont faites par du code pur et testé, puis un **garde-fou** vérifie
que chaque IBAN figure tel quel dans le texte et que le montant correspond à
un nombre du texte. Pendant les essais, il a bloqué de vraies hallucinations
(IBAN altérés, IBAN de l'exemple du prompt recopié sur un texte sans
transaction).

## Anti-doublon

- **Clé : le hash SHA-256 du contenu téléchargé**, pas le nom de l'objet.
  `processed_files.content_hash` est `UNIQUE`.
- **Même transaction SQL** : `insert_file_results` commence par
  `INSERT INTO processed_files ... ON CONFLICT (content_hash) DO NOTHING RETURNING id`.
  Si aucune ligne n'est renvoyée, le fichier est déjà traité et rien n'est
  écrit ; sinon les transactions et les totaux sont écrits dans la même
  transaction. Une erreur au milieu annule tout, y compris `processed_files`.
- **Cas `after_commit`** : si la base a validé mais que le client croit à un
  échec (panne juste après le commit), la tentative suivante du retry
  rencontre le hash existant et répond DÉJÀ TRAITÉ : jamais de doublon
  (`test_failure_after_commit_is_detected_as_already_processed`).
- **Optimisation** : avant tout chargement, `run.py` vérifie en lecture
  (`db.is_processed`, via le retry DB) si le hash est connu ; si oui, l'objet
  est ignoré sans OCR ni appel au SLM. Un second `make pipeline` passe ainsi
  de 32 s à 4 s en local. La garantie reste l'`ON CONFLICT` ci-dessus.
- **Conséquence assumée** : le même contenu déposé sous deux noms n'est
  traité qu'une fois (`test_same_content_under_another_key_is_ignored`,
  `test_same_image_under_another_key_is_ignored`) ; `processed_files.filename`
  garde la clé S3 du premier dépôt.

## Retries

Un seul mécanisme générique (`retry.py` : tentatives max, délai initial,
backoff exponentiel x2, exceptions temporaires, `sleep` injectable), un
WARNING par échec (`Tentative 1/4 échouée (clé) : erreur — nouvelle tentative
dans 1.0 s`), et `RetryExhaustedError` à l'épuisement : ligne ERREUR sans
traceback, code de sortie non nul, rien en base pour l'objet, objets suivants
non examinés. Les erreurs définitives ne sont jamais retentées.

| Opération retentée | Erreurs retentées | Jamais retentées | Variables (défaut) |
|---|---|---|---|
| **DB** : connexion neuve + insertion ; vérification « déjà traité » | `psycopg.OperationalError` | validation, intégrité | `DB_MAX_ATTEMPTS` (4), `DB_RETRY_DELAY` (1 s) |
| **S3** : listing du bucket, téléchargement d'un objet | connexion refusée, timeouts, connexion coupée, 5xx, `SlowDown` | `NoSuchKey`, `NoSuchBucket`, `AccessDenied`, autres 4xx | `S3_MAX_ATTEMPTS` (4), `S3_RETRY_DELAY` (1 s), `S3_CONNECT_TIMEOUT` (2 s), `S3_READ_TIMEOUT` (5 s) |
| **SLM** : appel à Ollama | service indisponible, timeout, connexion coupée, 5xx, 429 | modèle absent (404), autres 4xx | `SLM_MAX_ATTEMPTS` (4), `SLM_RETRY_DELAY` (2 s), `SLM_TIMEOUT` (120 s) |

Les retries internes de botocore sont désactivés (une tentative par appel)
pour que chaque échec passe par ce mécanisme. Pannes simulables :
`SIMULATE_DB_FAILURES` / `SIMULATE_DB_FAILURE_POINT` (`mid`, `after_commit`)
et `SIMULATE_S3_FAILURES` / `SIMULATE_S3_FAILURE_KIND` (`connection`,
`timeout`, `unavailable`). Démos : `make demo-retry`, `demo-retry-exhausted`,
`demo-s3-retry`, `demo-s3-retry-exhausted`, et `demo-s3-outage`, qui arrête
réellement le conteneur S3 puis le redémarre pendant que la pipeline retente.
Les démos ne traitent que les CSV (`PIPELINE_EXTENSIONS=.csv`) : elles
démontrent le retry, pas le SLM.

## Tests

```bash
make lint               # ruff check + ruff format --check
make test               # 195 tests unitaires, < 1 s, sans aucun service
make pipeline           # (ou make prepare) : démarre les services et le modèle
make test-integration   # 64 tests d'intégration, ~2 min en local
```

- **Unitaires** (195) : génération déterministe, validation, traitements purs
  (valeurs en dur, cas limites du seuil, pureté), retry (faux `sleep`),
  injecteurs de pannes, classement des erreurs S3 et SLM, extraction OCR et
  SLM à partir de textes et de réponses simulés (normalisation, garde-fou,
  pureté), politiques lues depuis l'environnement.
- **Intégration** (64), contre les vrais services : PostgreSQL (insertion,
  cumul, rollback), RustFS (bucket, aller-retour, vérification), Tesseract
  (chaque image donne la transaction attendue), le vrai modèle SLM (chaque
  texte donne la transaction attendue), et la pipeline réelle lancée en
  sous-processus : flux mixte CSV + images + textes (29 transactions, 5 flags,
  totaux exacts dont le cumul d'un IBAN présent dans les trois sources),
  relancement sans réinsertion et sans OCR ni SLM, objets invalides, retries
  DB et S3 avec pannes simulées.
- Les valeurs attendues sont écrites en dur, calculées une fois
  indépendamment du code testé.

## Scénario d'échec de la CI

Une régression volontaire (cumul des totaux remplacé par un écrasement) passe
le lint et les tests unitaires mais est bloquée par les tests d'intégration
dans la pull request, puis corrigée : [docs/scenario-echec.md](docs/scenario-echec.md)
(diff, run rouge, message d'erreur exact, correctif, run vert, captures).

## CI

GitHub Actions (`.github/workflows/ci.yml`) sur chaque push sur `main` et
chaque pull request, avec les mêmes cibles `make` qu'en local :

1. `lint-and-unit` : `make install`, `make lint`, `make test` ;
2. `integration` : installation de Tesseract, une étape par cible de
   `make pipeline` (services, modèle SLM mis en cache via `actions/cache`,
   génération des CSV, images et textes, upload et vérification S3, schéma,
   traitement), `make pipeline` relancé, `make test-integration`, les démos
   de retry, puis arrêt de tous les services dans tous les cas.

Durées mesurées avant l'optimisation de l'étape finale (relancement sans
OCR ni SLM, démos sur les CSV seuls) : de 8 min 51 s à 10 min 50 s selon le
runner.

## Limites connues

- **Réglage OCR sensible** : la police, la taille (48 px) et le rendu binaire
  ont été choisis pour donner 0 erreur sur Tesseract 5.5.4, 5.3.4 et 5.5.0 (CI) ; les tailles
  voisines (44, 56 px) produisent des erreurs. Tout changement de rendu ou
  de version de Tesseract doit être revérifié.
- **Garde-fou SLM** : il vérifie les IBAN et le montant, pas la date, les pays
  ni la banque ; un montant inventé égal à un autre nombre du texte (l'année
  par exemple) passerait.
- **Hash des PNG dépendant de la plateforme** : le rendu est identique pixel
  pour pixel entre macOS et Linux, mais la compression zlib embarquée dans
  Pillow diffère (fichiers de 13 850 et 14 479 octets pour la même image) ;
  le hash d'une image dépend donc de la machine qui l'a générée.
- **Les tests d'intégration vident la base de dev** (`TRUNCATE` avant chaque
  test) ; le bucket de test `pipeline-tests` est séparé du bucket de dev.
- **Durée de la CI** : dominée par les appels au SLM sur CPU (environ 10 s
  par appel sur un runner GitHub à 4 vCPU) et par l'image Ollama (7,2 Go) ;
  elle varie d'environ une minute d'un runner à l'autre.
- **Ressources** : environ 9 Go d'images Docker et 1,3 Go de modèle.

## Structure

```
src/pipeline/   code (un module par couche, voir le tableau Architecture)
  fonts/        police embarquée pour le rendu des images (+ licence)
sql/            schéma PostgreSQL idempotent
tests/          tests unitaires ; tests/integration/ : tests contre les vrais services
docs/           scénario d'échec de la CI et captures (docs/img/)
.github/        workflow CI
Makefile        toutes les commandes (make help)
docker-compose.yml  PostgreSQL, RustFS, Ollama
```
