"""Simulation de pannes (base et S3), pour démontrer et tester le retry.

Inactive par défaut. Activée par variables d'environnement :

Base (db.insert_file_results appelle le hook aux points "mid" et "after_commit") :
- SIMULATE_DB_FAILURES=N : les N premières tentatives d'insertion échouent
  avec une psycopg.OperationalError ;
- SIMULATE_DB_FAILURE_POINT : "mid" (défaut) ou "after_commit".

S3 (storage.download_object appelle le hook au point "download") :
- SIMULATE_S3_FAILURES=N : les N premiers téléchargements échouent ;
- SIMULATE_S3_FAILURE_KIND : "connection" (défaut, connexion refusée),
  "timeout" (timeout de lecture) ou "unavailable" (réponse 503).
Les erreurs S3 simulées sont les vraies exceptions botocore : elles passent
par la même classification que les pannes réelles.
"""

import os

import psycopg
from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError

DB_FAILURES_ENV = "SIMULATE_DB_FAILURES"
DB_POINT_ENV = "SIMULATE_DB_FAILURE_POINT"
S3_FAILURES_ENV = "SIMULATE_S3_FAILURES"
S3_KIND_ENV = "SIMULATE_S3_FAILURE_KIND"

MID = "mid"  # après processed_files + transactions, avant les totaux et le commit
AFTER_COMMIT = "after_commit"  # la base a écrit, le client croit à un échec
DB_POINTS = (MID, AFTER_COMMIT)

DOWNLOAD = "download"
CONNECTION, TIMEOUT, UNAVAILABLE = "connection", "timeout", "unavailable"
S3_KINDS = (CONNECTION, TIMEOUT, UNAVAILABLE)

_SIMULATED_ENDPOINT = "http://s3-simule"


def _failures_from_env(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw or raw == "0":
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} doit être un entier, pas '{raw}'") from exc


class _CountingInjector:
    """Échoue les `failures` premières fois que le point surveillé est atteint."""

    failures_env = ""
    point = ""

    def __init__(self, failures: int) -> None:
        if failures < 0:
            raise ValueError(f"{self.failures_env} doit être >= 0")
        self.failures = failures
        self.triggered = 0

    def __call__(self, point: str) -> None:
        if point == self.point and self.triggered < self.failures:
            self.triggered += 1
            raise self._error(f"panne simulée {self.triggered}/{self.failures}")

    def _error(self, message: str) -> Exception:
        raise NotImplementedError


class DbFaultInjector(_CountingInjector):
    failures_env = DB_FAILURES_ENV

    def __init__(self, failures: int, point: str = MID) -> None:
        if point not in DB_POINTS:
            raise ValueError(f"{DB_POINT_ENV} doit valoir {' ou '.join(DB_POINTS)}, pas '{point}'")
        super().__init__(failures)
        self.point = point

    def _error(self, message: str) -> Exception:
        return psycopg.OperationalError(f"{message} (point {self.point})")

    @classmethod
    def from_env(cls) -> "DbFaultInjector | None":
        """Renvoie un injecteur si la simulation DB est activée, None sinon."""
        failures = _failures_from_env(DB_FAILURES_ENV)
        if failures is None:
            return None
        return cls(failures, os.environ.get(DB_POINT_ENV, MID).strip() or MID)


class S3FaultInjector(_CountingInjector):
    failures_env = S3_FAILURES_ENV
    point = DOWNLOAD

    def __init__(self, failures: int, kind: str = CONNECTION) -> None:
        if kind not in S3_KINDS:
            raise ValueError(f"{S3_KIND_ENV} doit valoir {', '.join(S3_KINDS)}, pas '{kind}'")
        super().__init__(failures)
        self.kind = kind

    def _error(self, message: str) -> Exception:
        if self.kind == CONNECTION:
            return EndpointConnectionError(endpoint_url=f"{_SIMULATED_ENDPOINT} ({message})")
        if self.kind == TIMEOUT:
            return ReadTimeoutError(endpoint_url=f"{_SIMULATED_ENDPOINT} ({message})")
        return ClientError(
            {
                "Error": {"Code": "ServiceUnavailable", "Message": message},
                "ResponseMetadata": {"HTTPStatusCode": 503},
            },
            "GetObject",
        )

    @classmethod
    def from_env(cls) -> "S3FaultInjector | None":
        """Renvoie un injecteur si la simulation S3 est activée, None sinon."""
        failures = _failures_from_env(S3_FAILURES_ENV)
        if failures is None:
            return None
        return cls(failures, os.environ.get(S3_KIND_ENV, CONNECTION).strip() or CONNECTION)
