import psycopg

from pipeline.retry import RetryPolicy
from pipeline.run import db_retry_policy, s3_retry_policy
from pipeline.storage import TemporaryStorageError


def test_s3_policy_defaults(monkeypatch):
    monkeypatch.delenv("S3_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("S3_RETRY_DELAY", raising=False)
    assert s3_retry_policy() == RetryPolicy(4, 1.0, (TemporaryStorageError,), 2.0)


def test_s3_policy_from_env(monkeypatch):
    monkeypatch.setenv("S3_MAX_ATTEMPTS", "6")
    monkeypatch.setenv("S3_RETRY_DELAY", "0.5")
    assert s3_retry_policy() == RetryPolicy(6, 0.5, (TemporaryStorageError,), 2.0)


def test_s3_and_db_policies_are_independent(monkeypatch):
    monkeypatch.setenv("S3_MAX_ATTEMPTS", "6")
    monkeypatch.delenv("DB_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("DB_RETRY_DELAY", raising=False)
    assert db_retry_policy() == RetryPolicy(4, 1.0, (psycopg.OperationalError,), 2.0)


def test_pipeline_extensions(monkeypatch):
    import pytest

    from pipeline.run import pipeline_extensions

    monkeypatch.delenv("PIPELINE_EXTENSIONS", raising=False)
    assert pipeline_extensions() == (".csv", ".png", ".txt")
    monkeypatch.setenv("PIPELINE_EXTENSIONS", " .CSV , .txt,.csv ")
    assert pipeline_extensions() == (".csv", ".txt")
    for invalid in [".csv,.pdf", " , "]:
        monkeypatch.setenv("PIPELINE_EXTENSIONS", invalid)
        with pytest.raises(ValueError, match="PIPELINE_EXTENSIONS"):
            pipeline_extensions()
