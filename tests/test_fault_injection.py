import psycopg
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError

from pipeline.fault_injection import DbFaultInjector, S3FaultInjector
from pipeline.storage import is_temporary


def test_inactive_by_default(monkeypatch):
    monkeypatch.delenv("SIMULATE_DB_FAILURES", raising=False)
    assert DbFaultInjector.from_env() is None


def test_zero_failures_is_inactive(monkeypatch):
    monkeypatch.setenv("SIMULATE_DB_FAILURES", "0")
    assert DbFaultInjector.from_env() is None


def test_from_env_default_point_is_mid(monkeypatch):
    monkeypatch.setenv("SIMULATE_DB_FAILURES", "2")
    monkeypatch.delenv("SIMULATE_DB_FAILURE_POINT", raising=False)
    injector = DbFaultInjector.from_env()
    assert (injector.failures, injector.point) == (2, "mid")


def test_fails_n_times_at_configured_point_only():
    injector = DbFaultInjector(2, "mid")

    injector("after_commit")  # autre point : aucune panne
    with pytest.raises(psycopg.OperationalError, match="panne simulée 1/2"):
        injector("mid")
    with pytest.raises(psycopg.OperationalError, match="panne simulée 2/2"):
        injector("mid")
    injector("mid")  # quota épuisé : plus de panne

    assert injector.triggered == 2


def test_after_commit_point():
    injector = DbFaultInjector(1, "after_commit")
    injector("mid")
    with pytest.raises(psycopg.OperationalError):
        injector("after_commit")


@pytest.mark.parametrize(("failures", "point"), [("abc", "mid"), ("2", "avant"), ("-1", "mid")])
def test_invalid_config(monkeypatch, failures, point):
    monkeypatch.setenv("SIMULATE_DB_FAILURES", failures)
    monkeypatch.setenv("SIMULATE_DB_FAILURE_POINT", point)
    with pytest.raises(ValueError):
        DbFaultInjector.from_env()


# --- Injecteur S3 ---


def test_s3_inactive_by_default(monkeypatch):
    monkeypatch.delenv("SIMULATE_S3_FAILURES", raising=False)
    assert S3FaultInjector.from_env() is None


def test_s3_from_env_default_kind_is_connection(monkeypatch):
    monkeypatch.setenv("SIMULATE_S3_FAILURES", "3")
    monkeypatch.delenv("SIMULATE_S3_FAILURE_KIND", raising=False)
    injector = S3FaultInjector.from_env()
    assert (injector.failures, injector.kind) == (3, "connection")


@pytest.mark.parametrize(
    ("kind", "error_type"),
    [
        ("connection", EndpointConnectionError),
        ("timeout", ReadTimeoutError),
        ("unavailable", ClientError),
    ],
)
def test_s3_raises_real_botocore_errors_classified_temporary(kind, error_type):
    injector = S3FaultInjector(1, kind)

    with pytest.raises(error_type) as exc:
        injector("download")

    assert is_temporary(exc.value)
    assert "panne simulée 1/1" in str(exc.value)
    injector("download")  # quota épuisé : plus de panne


def test_s3_unavailable_is_a_503():
    with pytest.raises(ClientError) as exc:
        S3FaultInjector(1, "unavailable")("download")
    assert exc.value.response["ResponseMetadata"]["HTTPStatusCode"] == 503
    assert exc.value.response["Error"]["Code"] == "ServiceUnavailable"


def test_s3_injector_ignores_db_points_and_vice_versa():
    s3, db = S3FaultInjector(1), DbFaultInjector(1, "mid")
    s3("mid")
    s3("after_commit")
    db("download")
    assert (s3.triggered, db.triggered) == (0, 0)


def test_s3_invalid_kind(monkeypatch):
    monkeypatch.setenv("SIMULATE_S3_FAILURES", "1")
    monkeypatch.setenv("SIMULATE_S3_FAILURE_KIND", "incendie")
    with pytest.raises(ValueError, match="SIMULATE_S3_FAILURE_KIND"):
        S3FaultInjector.from_env()
