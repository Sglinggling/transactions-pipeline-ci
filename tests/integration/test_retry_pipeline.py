"""Retry de l'insertion avec pannes simulées (pipeline réelle depuis S3, en sous-processus)."""

import pytest

from .helpers import (
    EXPECTED_FILES,
    EXPECTED_TOTALS,
    EXPECTED_TRANSACTIONS,
    TABLES,
    count_duplicate_transactions,
    fetch,
    row_counts,
    run_pipeline,
    totals,
)

pytestmark = pytest.mark.integration

NO_WAIT = {"DB_RETRY_DELAY": 0}
FIRST = "incoming/transactions_001.csv"  # premier objet traité : il encaisse les pannes


def processed_filenames(conn):
    return [r[0] for r in fetch(conn, "SELECT filename FROM processed_files ORDER BY id")]


def test_two_mid_failures_then_success(conn, s3_incoming):
    result = run_pipeline(SIMULATE_DB_FAILURES=2, **NO_WAIT)

    assert result.returncode == 0, result.stderr
    assert f"Tentative 1/4 échouée ({FIRST}) : panne simulée 1/2" in result.stderr
    assert f"Tentative 2/4 échouée ({FIRST}) : panne simulée 2/2" in result.stderr
    assert "Tentative 3/4" not in result.stderr
    assert row_counts(conn)["transactions"] == EXPECTED_TRANSACTIONS
    assert processed_filenames(conn) == EXPECTED_FILES
    assert count_duplicate_transactions(conn) == 0
    assert totals(conn) == EXPECTED_TOTALS


def test_failure_after_commit_is_detected_as_already_processed(conn, s3_incoming):
    result = run_pipeline(
        SIMULATE_DB_FAILURES=1, SIMULATE_DB_FAILURE_POINT="after_commit", **NO_WAIT
    )

    assert result.returncode == 0, result.stderr
    assert "Tentative 1/4 échouée (incoming/transactions_001.csv)" in result.stderr
    # La 1re tentative a bien écrit : la 2e voit le hash et n'insère rien.
    assert "DÉJÀ TRAITÉ   incoming/transactions_001.csv : ignoré" in result.stderr
    assert row_counts(conn)["transactions"] == EXPECTED_TRANSACTIONS
    assert processed_filenames(conn) == EXPECTED_FILES
    assert count_duplicate_transactions(conn) == 0
    assert totals(conn) == EXPECTED_TOTALS


def test_exhausted_retries_stop_pipeline_then_clean_rerun(conn, s3_incoming):
    result = run_pipeline(SIMULATE_DB_FAILURES=10, DB_MAX_ATTEMPTS=3, **NO_WAIT)

    assert result.returncode != 0
    assert "Tentative 3/3 échouée (incoming/transactions_001.csv)" in result.stderr
    assert (
        f"ERREUR        {FIRST} : échec après 3 tentative(s) : panne simulée 3/10" in result.stderr
    )
    assert "2 non examiné(s)" in result.stderr
    assert row_counts(conn) == dict.fromkeys(TABLES, 0)

    rerun = run_pipeline()

    assert rerun.returncode == 0, rerun.stderr
    assert processed_filenames(conn) == EXPECTED_FILES
    assert row_counts(conn)["transactions"] == EXPECTED_TRANSACTIONS
    assert totals(conn) == EXPECTED_TOTALS


def test_backoff_delays_are_logged(conn, s3_incoming):
    # Délai initial minuscule pour vérifier le doublement sans ralentir le test.
    result = run_pipeline(SIMULATE_DB_FAILURES=3, DB_RETRY_DELAY=0.1)

    assert result.returncode == 0, result.stderr
    for attempt, delay in [(1, "0.1"), (2, "0.2"), (3, "0.4")]:
        assert (
            f"Tentative {attempt}/4 échouée (incoming/transactions_001.csv) : "
            f"panne simulée {attempt}/3 (point mid) — nouvelle tentative dans {delay} s"
        ) in result.stderr
    assert "Tentative 4/4" not in result.stderr
