# transactions-pipeline-ci

[![CI](https://github.com/Sglinggling/transactions-pipeline-ci-v2/actions/workflows/ci.yml/badge.svg)](https://github.com/Sglinggling/transactions-pipeline-ci-v2/actions/workflows/ci.yml)

Pipeline data de transactions financières construite pas à pas dans le cadre
d'un cours CI/CD.

## Démarrage

```bash
make install    # .venv + dépendances (Python 3.12)
make generate   # CSV déterministes dans data/incoming/
make lint
make test
```

`make help` liste toutes les cibles.
