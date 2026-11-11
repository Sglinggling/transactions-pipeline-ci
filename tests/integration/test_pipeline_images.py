"""Flux mixte de bout en bout : CSV + images -> S3 -> pipeline réelle -> base."""

from decimal import Decimal

import pytest

from pipeline.generate_images import IMAGE_TRANSACTIONS, render_notice

from .helpers import (
    EXPECTED_MIXED_FILES,
    EXPECTED_MIXED_FLAGGED,
    EXPECTED_MIXED_TOTALS,
    EXPECTED_MIXED_TRANSACTIONS,
    TEST_BUCKET,
    count_duplicate_transactions,
    fetch,
    row_counts,
    run_pipeline,
    totals,
)

pytestmark = pytest.mark.integration


def processed_keys(conn):
    return [r[0] for r in fetch(conn, "SELECT filename FROM processed_files ORDER BY id")]


def put(s3, key, body):
    s3.put_object(Bucket=TEST_BUCKET, Key=key, Body=body)


def test_mixed_run(conn, s3_mixed):
    result = run_pipeline()

    assert result.returncode == 0, result.stderr
    assert "TRAITÉ        incoming/virement_001.png : 1 transaction(s), 1 flag(s)" in result.stderr
    assert processed_keys(conn) == EXPECTED_MIXED_FILES
    assert row_counts(conn)["transactions"] == EXPECTED_MIXED_TRANSACTIONS
    assert count_duplicate_transactions(conn) == 0
    assert totals(conn) == EXPECTED_MIXED_TOTALS


def test_mixed_flags(conn, s3_mixed):
    assert run_pipeline().returncode == 0

    flags = dict(
        fetch(
            conn,
            "SELECT montant, is_flagged FROM transactions WHERE montant IN (%s, %s, %s, %s)",
            (Decimal("12500.00"), Decimal("5000.01"), Decimal("7350.50"), Decimal("1250.75")),
        )
    )
    assert flags == {
        Decimal("12500.00"): True,
        Decimal("5000.01"): True,
        Decimal("7350.50"): True,  # image
        Decimal("1250.75"): False,  # image
    }
    # 5000.00 pile existe deux fois (un CSV, une image) : jamais flaggé.
    assert fetch(
        conn, "SELECT count(*), bool_or(is_flagged) FROM transactions WHERE montant = 5000.00"
    ) == [(2, False)]
    assert fetch(conn, "SELECT count(*) FROM transactions WHERE is_flagged")[0][0] == (
        EXPECTED_MIXED_FLAGGED
    )


def test_totals_cumulated_across_csv_and_image(conn, s3_mixed):
    assert run_pipeline().returncode == 0

    # FR..0101 / BNP Paribas émettent dans les CSV (22969.24 / 37144.15) et
    # dans virement_001.png (7350.50).
    assert fetch(
        conn,
        "SELECT total FROM total_sent_by_iban WHERE iban = 'FR7630004000010000000000101'",
    ) == [(Decimal("30319.74"),)]
    assert fetch(conn, "SELECT total FROM total_sent_by_bank WHERE banque = 'BNP Paribas'") == [
        (Decimal("44494.65"),)
    ]


def test_second_run_inserts_nothing(conn, s3_mixed):
    assert run_pipeline().returncode == 0
    counts_before = row_counts(conn)

    result = run_pipeline()

    assert result.returncode == 0, result.stderr
    assert result.stderr.count("DÉJÀ TRAITÉ   incoming/virement_") == 3
    assert row_counts(conn) == counts_before
    assert totals(conn) == EXPECTED_MIXED_TOTALS


def test_same_image_under_another_key_is_ignored(conn, s3_mixed):
    assert run_pipeline().returncode == 0
    put(s3_mixed, "incoming/zz_copie.png", render_notice(IMAGE_TRANSACTIONS[0]))

    result = run_pipeline()

    assert result.returncode == 0, result.stderr
    assert "DÉJÀ TRAITÉ   incoming/zz_copie.png : ignoré" in result.stderr
    assert totals(conn) == EXPECTED_MIXED_TOTALS


def test_unreadable_image_stops_cleanly(conn, s3_mixed):
    put(s3_mixed, "incoming/virement_004.png", b"\x89PNG tronque")

    result = run_pipeline()

    assert result.returncode != 0
    assert (
        "ERREUR        incoming/virement_004.png : incoming/virement_004.png : image illisible"
    ) in result.stderr
    assert "Traceback" not in result.stderr
    assert processed_keys(conn) == EXPECTED_MIXED_FILES  # tous les objets précédents
    assert "incoming/virement_004.png" not in processed_keys(conn)


def test_image_with_missing_field_stops_cleanly(conn, s3_mixed):
    fields = {k: v for k, v in IMAGE_TRANSACTIONS[2].items() if k != "montant"}
    # Trié entre virement_001.png et virement_002.png.
    put(s3_mixed, "incoming/virement_001b.png", render_notice(fields))

    result = run_pipeline()

    assert result.returncode != 0
    assert (
        "ERREUR        incoming/virement_001b.png : incoming/virement_001b.png, fichier : "
        "champ(s) manquant(s) ou illisible(s) : Montant"
    ) in result.stderr
    assert "2 non examiné(s)" in result.stderr
    assert processed_keys(conn) == EXPECTED_MIXED_FILES[:4]
    assert row_counts(conn)["transactions"] == 22 + 1


def test_s3_retry_applies_to_images(conn, s3, incoming_mixed):
    # Seules les images dans le bucket : les pannes S3 tombent sur une image.
    for path in sorted(incoming_mixed.glob("*.png")):
        put(s3, f"incoming/{path.name}", path.read_bytes())

    result = run_pipeline(SIMULATE_S3_FAILURES=2, S3_RETRY_DELAY=0)

    assert result.returncode == 0, result.stderr
    assert "Tentative 2/4 échouée (incoming/virement_001.png)" in result.stderr
    assert processed_keys(conn) == EXPECTED_MIXED_FILES[3:]
    assert row_counts(conn)["transactions"] == 3
