"""Retry de la récupération S3 : pannes simulées (sous-processus) et erreurs réelles."""

import dataclasses
import logging

import pytest
from botocore.exceptions import EndpointConnectionError

from pipeline.retry import RetryPolicy
from pipeline.run import RunOptions, db_retry_policy, run_pipeline, s3_retry_policy
from pipeline.storage import S3Settings, TemporaryStorageError

from .helpers import (
    EXPECTED_FILES,
    EXPECTED_TOTALS,
    EXPECTED_TRANSACTIONS,
    TABLES,
    TEST_BUCKET,
    TEST_PREFIX,
    count_duplicate_transactions,
    fetch,
    row_counts,
    totals,
)
from .helpers import run_pipeline as run_pipeline_cli

pytestmark = pytest.mark.integration

NO_WAIT = {"S3_RETRY_DELAY": 0, "DB_RETRY_DELAY": 0}
FIRST = "incoming/transactions_001.csv"  # premier objet téléchargé : il encaisse les pannes


def processed_keys(conn):
    return [r[0] for r in fetch(conn, "SELECT filename FROM processed_files ORDER BY id")]


def assert_complete_and_exact(conn):
    assert processed_keys(conn) == EXPECTED_FILES
    assert row_counts(conn)["transactions"] == EXPECTED_TRANSACTIONS
    assert count_duplicate_transactions(conn) == 0
    assert totals(conn) == EXPECTED_TOTALS


# --- Pannes simulées, pipeline réelle en sous-processus ---


@pytest.mark.parametrize("kind", ["connection", "timeout", "unavailable"])
def test_two_s3_failures_then_success(conn, s3_incoming, kind):
    result = run_pipeline_cli(SIMULATE_S3_FAILURES=2, SIMULATE_S3_FAILURE_KIND=kind, **NO_WAIT)

    assert result.returncode == 0, result.stderr
    assert f"Tentative 1/4 échouée ({FIRST}) : téléchargement de s3://pipeline-tests/" in (
        result.stderr
    )
    assert f"Tentative 2/4 échouée ({FIRST})" in result.stderr
    assert "Tentative 3/4" not in result.stderr
    assert_complete_and_exact(conn)


def test_s3_retries_exhausted_then_clean_rerun(conn, s3_incoming):
    result = run_pipeline_cli(SIMULATE_S3_FAILURES=10, S3_MAX_ATTEMPTS=3, **NO_WAIT)

    assert result.returncode != 0
    assert f"Tentative 3/3 échouée ({FIRST})" in result.stderr
    assert (
        f"ERREUR        {FIRST} : échec après 3 tentative(s) : "
        f"téléchargement de s3://pipeline-tests/{FIRST} : EndpointConnectionError"
    ) in result.stderr
    assert "2 non examiné(s)" in result.stderr
    assert "Traceback" not in result.stderr
    assert row_counts(conn) == dict.fromkeys(TABLES, 0)

    rerun = run_pipeline_cli()

    assert rerun.returncode == 0, rerun.stderr
    assert_complete_and_exact(conn)


@pytest.mark.parametrize("db_point", ["mid", "after_commit"])
def test_s3_and_db_failures_in_same_run(conn, s3_incoming, db_point):
    result = run_pipeline_cli(
        SIMULATE_S3_FAILURES=2,
        SIMULATE_DB_FAILURES=2,
        SIMULATE_DB_FAILURE_POINT=db_point,
        **NO_WAIT,
    )

    assert result.returncode == 0, result.stderr
    assert "panne simulée 2/2 (point " in result.stderr  # pannes DB
    assert f"téléchargement de s3://pipeline-tests/{FIRST}" in result.stderr  # pannes S3
    assert_complete_and_exact(conn)


def test_s3_backoff_delays_are_logged(conn, s3_incoming):
    result = run_pipeline_cli(SIMULATE_S3_FAILURES=3, S3_RETRY_DELAY=0.1)

    assert result.returncode == 0, result.stderr
    for attempt, delay in [(1, "0.1"), (2, "0.2"), (3, "0.4")]:
        line = next(
            line for line in result.stderr.splitlines() if f"Tentative {attempt}/4 échouée" in line
        )
        assert f"({FIRST})" in line
        assert f"panne simulée {attempt}/3" in line
        assert line.endswith(f"nouvelle tentative dans {delay} s")


# --- Erreurs réelles, pipeline appelée en processus avec un client instrumenté ---


class ClientProxy:
    """Délègue au vrai client S3 ; les sous-classes perturbent certains appels."""

    def __init__(self, client):
        self._client = client

    def __getattr__(self, name):
        return getattr(self._client, name)


class DeleteBeforeDownload(ClientProxy):
    """Supprime l'objet juste avant son téléchargement (après le listing)."""

    def __init__(self, client, key):
        super().__init__(client)
        self.key = key
        self.downloads = []

    def get_object(self, **kwargs):
        self.downloads.append(kwargs["Key"])
        if kwargs["Key"] == self.key:
            self._client.delete_object(Bucket=kwargs["Bucket"], Key=self.key)
        return self._client.get_object(**kwargs)


class FlakyListing(ClientProxy):
    """Le premier listing échoue (connexion refusée), les suivants passent."""

    def __init__(self, client):
        super().__init__(client)
        self.calls = 0

    def get_paginator(self, name):
        self.calls += 1
        if self.calls == 1:
            raise EndpointConnectionError(endpoint_url="http://s3-coupe")
        return self._client.get_paginator(name)


@pytest.fixture
def test_settings():
    return dataclasses.replace(S3Settings.from_env(), bucket=TEST_BUCKET, prefix=TEST_PREFIX)


def options(sleeps):
    return RunOptions(
        db_policy=db_retry_policy(),
        s3_policy=s3_retry_policy(),
        sleep=sleeps.append,
    )


def test_object_deleted_between_listing_and_download_is_not_retried(
    conn, s3_incoming, test_settings, caplog, monkeypatch
):
    caplog.set_level(logging.INFO)
    monkeypatch.delenv("S3_MAX_ATTEMPTS", raising=False)
    deleted = "incoming/transactions_002.csv"
    client, sleeps = DeleteBeforeDownload(s3_incoming, deleted), []

    summary = run_pipeline(client, test_settings, options(sleeps))

    assert summary.failed_file == deleted
    assert summary.not_examined == 1
    assert client.downloads.count(deleted) == 1  # une seule tentative
    assert sleeps == []
    assert not [r for r in caplog.records if r.message.startswith("Tentative")]
    assert (
        f"ERREUR        {deleted} : téléchargement de s3://pipeline-tests/{deleted} : "
        "NoSuchKey (HTTP 404) : The specified key does not exist."
    ) in caplog.messages
    # L'objet précédent reste en base, rien pour l'objet supprimé ni le suivant.
    assert processed_keys(conn) == ["incoming/transactions_001.csv"]
    assert row_counts(conn)["transactions"] == 5


def test_listing_is_retried(conn, s3_incoming, test_settings, caplog):
    caplog.set_level(logging.INFO)
    client, sleeps = FlakyListing(s3_incoming), []
    opts = dataclasses.replace(
        options(sleeps), s3_policy=RetryPolicy(4, 1.0, (TemporaryStorageError,))
    )

    summary = run_pipeline(client, test_settings, opts)

    assert summary.ok
    assert client.calls == 2
    assert sleeps == [1.0]
    assert any(
        m.startswith("Tentative 1/4 échouée (listing s3://pipeline-tests/incoming/) : listing de")
        for m in caplog.messages
    )
    assert_complete_and_exact(conn)


def test_known_content_check_goes_through_db_retry(
    conn, s3_incoming, test_settings, caplog, monkeypatch
):
    import psycopg

    import pipeline.run as run_module

    caplog.set_level(logging.INFO)
    sleeps = []
    assert run_pipeline(s3_incoming, test_settings, options(sleeps)).ok

    calls = {"n": 0}
    real_is_processed = run_module.is_processed

    def flaky_is_processed(connection, content_hash):
        calls["n"] += 1
        if calls["n"] == 1:
            raise psycopg.OperationalError("base indisponible (simulée)")
        return real_is_processed(connection, content_hash)

    monkeypatch.setattr(run_module, "is_processed", flaky_is_processed)
    caplog.clear()

    summary = run_pipeline(s3_incoming, test_settings, options(sleeps))

    assert summary.ok
    assert summary.skipped == 3
    assert any(
        m.startswith(f"Tentative 1/4 échouée ({FIRST}) : base indisponible (simulée)")
        for m in caplog.messages
    )
