"""Flux complet de bout en bout : CSV + images + textes -> S3 -> pipeline réelle -> base."""

from decimal import Decimal

import pytest

from pipeline.generate_images import IMAGE_TRANSACTIONS, render_notice
from pipeline.generate_texts import TEXTS

from .helpers import (
    EXPECTED_ALL_FILES,
    EXPECTED_ALL_FLAGGED,
    EXPECTED_ALL_TOTALS,
    EXPECTED_ALL_TRANSACTIONS,
    TABLES,
    TEST_BUCKET,
    count_duplicate_transactions,
    fetch,
    row_counts,
    run_pipeline,
    totals,
)
from .test_slm_extraction import NO_TRANSACTION

pytestmark = pytest.mark.integration


def processed_keys(conn):
    return [r[0] for r in fetch(conn, "SELECT filename FROM processed_files ORDER BY id")]


def put(s3, key, body):
    s3.put_object(Bucket=TEST_BUCKET, Key=key, Body=body)


@pytest.fixture
def no_ocr_no_slm(tmp_path):
    """Environnement où l'OCR et le SLM échouent à coup sûr : dossier de modèles
    Tesseract vide, endpoint SLM fermé, une seule tentative."""
    empty_tessdata = tmp_path / "tessdata-vide"
    empty_tessdata.mkdir()
    return {
        "TESSDATA_PREFIX": empty_tessdata,
        "SLM_URL": "http://127.0.0.1:9",
        "SLM_MAX_ATTEMPTS": 1,
    }


def test_full_run_three_sources_then_rerun(conn, s3_all, no_ocr_no_slm):
    # Les deux passages dans un seul test : chaque passage coûte 4 appels au
    # modèle (plusieurs secondes chacun), on ne refait pas le premier deux fois.
    result = run_pipeline()

    assert result.returncode == 0, result.stderr
    assert "TRAITÉ        incoming/message_001.txt : 1 transaction(s), 1 flag(s)" in result.stderr
    assert processed_keys(conn) == EXPECTED_ALL_FILES
    assert row_counts(conn)["transactions"] == EXPECTED_ALL_TRANSACTIONS
    assert count_duplicate_transactions(conn) == 0
    assert totals(conn) == EXPECTED_ALL_TOTALS

    # Flags, dont les sources texte (6200.00, 7300.50) et les 5000.00 pile
    # (un CSV, une image, un texte) jamais flaggés.
    assert fetch(conn, "SELECT count(*) FROM transactions WHERE is_flagged")[0][0] == (
        EXPECTED_ALL_FLAGGED
    )
    assert fetch(
        conn,
        "SELECT montant, is_flagged FROM transactions WHERE montant IN (6200.00, 7300.50) "
        "ORDER BY montant",
    ) == [(Decimal("6200.00"), True), (Decimal("7300.50"), True)]
    assert fetch(
        conn, "SELECT count(*), bool_or(is_flagged) FROM transactions WHERE montant = 5000.00"
    ) == [(3, False)]

    # FR..0101 / BNP Paribas émettent dans les trois sources :
    # CSV 22969.24 + image 7350.50 + texte 6200.00 = 36519.74.
    assert fetch(
        conn, "SELECT total FROM total_sent_by_iban WHERE iban = 'FR7630004000010000000000101'"
    ) == [(Decimal("36519.74"),)]
    assert fetch(conn, "SELECT total FROM total_sent_by_bank WHERE banque = 'BNP Paribas'") == [
        (Decimal("50694.65"),)
    ]

    # Second passage, OCR et SLM rendus inutilisables : il réussit quand même,
    # car chaque objet est reconnu par son hash avant tout chargement.
    counts_before = row_counts(conn)
    rerun = run_pipeline(**no_ocr_no_slm)

    assert rerun.returncode == 0, rerun.stderr
    assert rerun.stderr.count("DÉJÀ TRAITÉ   incoming/message_") == 4
    assert rerun.stderr.count("DÉJÀ TRAITÉ   incoming/") == len(EXPECTED_ALL_FILES)
    assert "ERREUR" not in rerun.stderr
    assert row_counts(conn) == counts_before
    assert totals(conn) == EXPECTED_ALL_TOTALS


@pytest.mark.parametrize(
    ("key", "body", "error"),
    [
        ("incoming/virement_001.png", None, "échec de l'OCR"),
        ("incoming/message_001.txt", (TEXTS[0] + "\n").encode(), "SLM injoignable"),
    ],
    ids=["image", "texte"],
)
def test_no_ocr_no_slm_environment_really_breaks_them(conn, s3, no_ocr_no_slm, key, body, error):
    # Contre-épreuve du test précédent : sur un objet inconnu, cet environnement
    # fait bien échouer l'OCR et le SLM.
    if body is None:
        body = render_notice(IMAGE_TRANSACTIONS[0])
    put(s3, key, body)

    result = run_pipeline(**no_ocr_no_slm)

    assert result.returncode != 0
    assert f"ERREUR        {key} : " in result.stderr
    assert error in result.stderr
    assert row_counts(conn) == dict.fromkeys(TABLES, 0)


def test_csv_only_extensions(conn, s3_all, no_ocr_no_slm):
    # PIPELINE_EXTENSIONS=.csv : images et textes ignorés (ni OCR ni SLM).
    result = run_pipeline(PIPELINE_EXTENSIONS=".csv", **no_ocr_no_slm)

    assert result.returncode == 0, result.stderr
    assert "(types : .csv)" in result.stderr
    assert processed_keys(conn) == [k for k in EXPECTED_ALL_FILES if k.endswith(".csv")]
    assert row_counts(conn)["transactions"] == 22


def test_invalid_extensions_setting(conn, s3):
    result = run_pipeline(PIPELINE_EXTENSIONS=".csv,.pdf")

    assert result.returncode != 0
    assert "ERREUR        configuration invalide : PIPELINE_EXTENSIONS='.csv,.pdf' invalide" in (
        result.stderr
    )


def test_text_without_transaction_stops_cleanly(conn, s3):
    put(s3, "incoming/message_vide.txt", NO_TRANSACTION.encode("utf-8"))

    result = run_pipeline()

    assert result.returncode != 0
    assert "ERREUR        incoming/message_vide.txt : incoming/message_vide.txt, fichier : " in (
        result.stderr
    )
    assert "Traceback" not in result.stderr
    assert row_counts(conn) == dict.fromkeys(TABLES, 0)


def test_s3_retry_applies_to_texts(conn, s3):
    put(s3, "incoming/message_001.txt", (TEXTS[0] + "\n").encode("utf-8"))

    result = run_pipeline(SIMULATE_S3_FAILURES=2, S3_RETRY_DELAY=0)

    assert result.returncode == 0, result.stderr
    assert "Tentative 2/4 échouée (incoming/message_001.txt)" in result.stderr
    assert processed_keys(conn) == ["incoming/message_001.txt"]
    assert fetch(conn, "SELECT montant, is_flagged FROM transactions") == [
        (Decimal("6200.00"), True)
    ]
