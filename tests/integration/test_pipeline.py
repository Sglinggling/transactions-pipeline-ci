"""Tests de bout en bout : générateur -> bucket S3 -> pipeline réelle (sous-processus) -> base."""

import shutil
from decimal import Decimal

import pytest

from pipeline.loading import EXPECTED_COLUMNS

from .helpers import (
    EXPECTED_FILES,
    EXPECTED_FLAGGED,
    EXPECTED_TOTALS,
    EXPECTED_TRANSACTIONS,
    TABLES,
    TEST_BUCKET,
    count_duplicate_transactions,
    fetch,
    row_counts,
    run_pipeline,
    totals,
)

pytestmark = pytest.mark.integration

INVALID_CSV = (
    ",".join(EXPECTED_COLUMNS) + "\n"
    "2025-01-18 08:00:00,BE68539007547034,BE,ING Belgique,"
    "DE89370400440532013000,DE,-10.00\n"
).encode("utf-8")


def processed_keys(conn):
    return [r[0] for r in fetch(conn, "SELECT filename FROM processed_files ORDER BY id")]


def put(s3, key, body):
    s3.put_object(Bucket=TEST_BUCKET, Key=key, Body=body)


def test_full_run(conn, s3_incoming):
    result = run_pipeline()

    assert result.returncode == 0, result.stderr
    assert "Source : s3://pipeline-tests/incoming/" in result.stderr
    assert row_counts(conn)["transactions"] == EXPECTED_TRANSACTIONS
    assert processed_keys(conn) == EXPECTED_FILES
    assert totals(conn) == EXPECTED_TOTALS


def test_flags(conn, s3_incoming):
    assert run_pipeline().returncode == 0

    flags = dict(
        fetch(
            conn,
            "SELECT montant, is_flagged FROM transactions WHERE montant IN (%s, %s, %s)",
            (Decimal("12500.00"), Decimal("5000.00"), Decimal("5000.01")),
        )
    )
    assert flags == {Decimal("12500.00"): True, Decimal("5000.00"): False, Decimal("5000.01"): True}
    assert fetch(conn, "SELECT count(*) FROM transactions WHERE is_flagged")[0][0] == (
        EXPECTED_FLAGGED
    )


def test_does_not_depend_on_local_files(conn, s3_incoming, incoming):
    shutil.rmtree(incoming)  # la zone de dépôt locale disparaît après l'upload
    assert not incoming.exists()

    result = run_pipeline()

    assert result.returncode == 0, result.stderr
    assert processed_keys(conn) == EXPECTED_FILES
    assert row_counts(conn)["transactions"] == EXPECTED_TRANSACTIONS
    assert totals(conn) == EXPECTED_TOTALS


def test_logs_one_line_per_object(conn, s3_incoming):
    first = run_pipeline()
    second = run_pipeline()

    assert first.stderr.count("TRAITÉ        incoming/transactions_") == 3
    assert (
        "TRAITÉ        incoming/transactions_001.csv : 5 transaction(s), 1 flag(s)" in first.stderr
    )
    assert second.stderr.count("DÉJÀ TRAITÉ   incoming/transactions_") == 3
    assert "Résumé : 0 traité(s), 3 déjà traité(s), 0 en erreur" in second.stderr


def test_second_run_inserts_nothing(conn, s3_incoming):
    assert run_pipeline().returncode == 0
    counts_before, totals_before = row_counts(conn), totals(conn)

    assert run_pipeline().returncode == 0

    assert row_counts(conn) == counts_before
    assert totals(conn) == totals_before == EXPECTED_TOTALS


def test_same_content_under_another_key_is_ignored(conn, s3_incoming, incoming):
    assert run_pipeline().returncode == 0
    counts_before = row_counts(conn)
    put(s3_incoming, "incoming/zz_copie_001.csv", (incoming / "transactions_001.csv").read_bytes())

    result = run_pipeline()

    assert result.returncode == 0, result.stderr
    assert "DÉJÀ TRAITÉ   incoming/zz_copie_001.csv : ignoré" in result.stderr
    assert row_counts(conn) == counts_before
    assert count_duplicate_transactions(conn) == 0
    assert totals(conn) == EXPECTED_TOTALS
    assert "incoming/zz_copie_001.csv" not in processed_keys(conn)


def test_invalid_csv_added_after_run(conn, s3_incoming):
    assert run_pipeline().returncode == 0
    counts_before, totals_before = row_counts(conn), totals(conn)
    put(s3_incoming, "incoming/transactions_004.csv", INVALID_CSV)

    result = run_pipeline()

    assert result.returncode != 0
    assert (
        "ERREUR        incoming/transactions_004.csv : incoming/transactions_004.csv, ligne 2 : "
        "montant doit être strictement positif : '-10.00'"
    ) in result.stderr
    assert row_counts(conn) == counts_before
    assert totals(conn) == totals_before
    assert "incoming/transactions_004.csv" not in processed_keys(conn)


def test_invalid_csv_stops_pipeline_keeping_previous_objects(conn, s3_incoming):
    # Trié entre transactions_002.csv et transactions_003.csv.
    put(s3_incoming, "incoming/transactions_002a.csv", INVALID_CSV)

    result = run_pipeline()

    assert result.returncode != 0
    assert "Pipeline interrompue sur incoming/transactions_002a.csv" in result.stderr
    assert "1 non examiné(s)" in result.stderr
    assert processed_keys(conn) == EXPECTED_FILES[:2]
    # 5 + 10 lignes des deux premiers objets, rien du fautif ni du suivant.
    assert row_counts(conn)["transactions"] == 15


def test_only_supported_objects_under_prefix_are_processed(conn, s3_incoming, incoming):
    content = (incoming / "transactions_001.csv").read_bytes()
    put(s3_incoming, "incoming/lisez-moi.md", b"type non pris en charge")
    put(s3_incoming, "autre-prefixe/transactions_009.csv", content + b"\n")

    result = run_pipeline()

    assert result.returncode == 0, result.stderr
    assert processed_keys(conn) == EXPECTED_FILES


def test_empty_prefix(conn, s3):
    result = run_pipeline()

    assert result.returncode == 0
    assert (
        "Aucun objet à traiter (.csv, .png, .txt) dans s3://pipeline-tests/incoming/"
        in result.stderr
    )
    assert row_counts(conn) == dict.fromkeys(TABLES, 0)


def test_missing_bucket(conn, s3):
    result = run_pipeline(S3_BUCKET="bucket-inexistant")

    assert result.returncode != 0
    assert (
        "ERREUR        listing impossible : listing de s3://bucket-inexistant/incoming/ : "
        "NoSuchBucket (HTTP 404)"
    ) in result.stderr
    assert "Tentative" not in result.stderr  # erreur définitive : jamais retentée
    assert "Traceback" not in result.stderr
    assert row_counts(conn) == dict.fromkeys(TABLES, 0)
