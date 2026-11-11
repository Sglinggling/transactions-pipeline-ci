from datetime import datetime
from decimal import Decimal

import pandas as pd
import psycopg
import pytest

from pipeline.db import apply_schema, insert_file_results
from pipeline.processing import (
    flag_high_amount,
    sum_by_banque_source,
    sum_by_iban_destinataire,
    sum_by_iban_origine,
)

from .helpers import TABLES, fetch, row_counts, totals

pytestmark = pytest.mark.integration

FR_BNP_1 = "FR7630004000010000000000101"
FR_BNP_2 = "FR7630004000010000000000202"
DE_DB = "DE89370400440532013000"
ES_SAN = "ES9121000418450200051332"
IT_UNI = "IT60X0542811101000000123456"

COLUMNS = [
    "datetime_transaction",
    "iban_origine",
    "pays_source",
    "banque_source",
    "iban_destinataire",
    "pays_destinataire",
    "montant",
]

FILE_A = pd.DataFrame(
    [
        ("2025-01-15 08:00:00", FR_BNP_1, "FR", "BNP Paribas", ES_SAN, "ES", 12500.00),
        ("2025-01-15 09:00:00", FR_BNP_2, "FR", "BNP Paribas", FR_BNP_1, "FR", 5000.00),
        ("2025-01-15 10:00:00", DE_DB, "DE", "Deutsche Bank", FR_BNP_1, "FR", 5000.01),
    ],
    columns=COLUMNS,
).assign(datetime_transaction=lambda d: pd.to_datetime(d["datetime_transaction"]))

FILE_B = pd.DataFrame(
    [
        ("2025-01-16 08:00:00", FR_BNP_1, "FR", "BNP Paribas", DE_DB, "DE", 0.10),
        ("2025-01-16 09:00:00", IT_UNI, "IT", "UniCredit", FR_BNP_1, "FR", 0.20),
    ],
    columns=COLUMNS,
).assign(datetime_transaction=lambda d: pd.to_datetime(d["datetime_transaction"]))

HASH_A = "a" * 64
HASH_B = "b" * 64


def insert(conn, df, filename, content_hash, **overrides):
    """Calcule les résultats via processing.py puis insère ; overrides remplace une entrée."""
    args = {
        "transactions_flaggees": flag_high_amount(df),
        "sommes_iban_origine": sum_by_iban_origine(df),
        "sommes_banque": sum_by_banque_source(df),
        "sommes_iban_destinataire": sum_by_iban_destinataire(df),
    }
    args.update(overrides)
    return insert_file_results(conn, filename, content_hash, **args)


def test_schema_is_idempotent(conn):
    apply_schema(conn)
    apply_schema(conn)


def test_insert_one_file(conn):
    assert insert(conn, FILE_A, "transactions_001.csv", HASH_A) is True

    assert fetch(conn, "SELECT id, filename, content_hash FROM processed_files") == [
        (1, "transactions_001.csv", HASH_A)
    ]
    assert fetch(
        conn,
        """
        SELECT file_id, datetime_transaction, iban_origine, pays_source, banque_source,
               iban_destinataire, pays_destinataire, montant, is_flagged
        FROM transactions ORDER BY datetime_transaction
        """,
    ) == [
        (1, datetime(2025, 1, 15, 8), FR_BNP_1, "FR", "BNP Paribas", ES_SAN, "ES",
         Decimal("12500.00"), True),
        (1, datetime(2025, 1, 15, 9), FR_BNP_2, "FR", "BNP Paribas", FR_BNP_1, "FR",
         Decimal("5000.00"), False),
        (1, datetime(2025, 1, 15, 10), DE_DB, "DE", "Deutsche Bank", FR_BNP_1, "FR",
         Decimal("5000.01"), True),
    ]  # fmt: skip
    assert totals(conn) == {
        "sent_by_iban": [
            (DE_DB, Decimal("5000.01")),
            (FR_BNP_1, Decimal("12500.00")),
            (FR_BNP_2, Decimal("5000.00")),
        ],
        "sent_by_bank": [
            ("BNP Paribas", Decimal("17500.00")),
            ("Deutsche Bank", Decimal("5000.01")),
        ],
        "received_by_iban": [
            (ES_SAN, Decimal("12500.00")),
            (FR_BNP_1, Decimal("10000.01")),
        ],
    }


def test_totals_are_cumulated_across_files(conn):
    assert insert(conn, FILE_A, "transactions_001.csv", HASH_A) is True
    assert insert(conn, FILE_B, "transactions_002.csv", HASH_B) is True

    assert row_counts(conn)["transactions"] == 5
    assert totals(conn) == {
        "sent_by_iban": [
            (DE_DB, Decimal("5000.01")),
            (FR_BNP_1, Decimal("12500.10")),
            (FR_BNP_2, Decimal("5000.00")),
            (IT_UNI, Decimal("0.20")),
        ],
        "sent_by_bank": [
            ("BNP Paribas", Decimal("17500.10")),
            ("Deutsche Bank", Decimal("5000.01")),
            ("UniCredit", Decimal("0.20")),
        ],
        "received_by_iban": [
            (DE_DB, Decimal("0.10")),
            (ES_SAN, Decimal("12500.00")),
            (FR_BNP_1, Decimal("10000.21")),
        ],
    }


def test_same_file_twice_is_ignored(conn):
    assert insert(conn, FILE_A, "transactions_001.csv", HASH_A) is True
    counts_before, totals_before = row_counts(conn), totals(conn)

    # Même contenu sous un autre nom : c'est le hash qui fait foi.
    assert insert(conn, FILE_A, "copie.csv", HASH_A) is False

    assert row_counts(conn) == counts_before
    assert counts_before["processed_files"] == 1
    assert counts_before["transactions"] == 3
    assert totals(conn) == totals_before


@pytest.mark.parametrize(
    "overrides",
    [
        # Échec pendant l'insertion des transactions (CHECK montant > 0).
        {"transactions_flaggees": flag_high_amount(FILE_A.assign(montant=[1.0, 2.0, -3.0]))},
        # Échec à la toute dernière étape (dépassement de NUMERIC(14,2)).
        {
            "sommes_iban_destinataire": sum_by_iban_destinataire(FILE_A).assign(
                montant_total=[1.0, 1e13]
            )
        },
    ],
    ids=["transaction_invalide", "total_en_depassement"],
)
def test_error_mid_insert_rolls_back_everything(conn, overrides):
    with pytest.raises(psycopg.Error):
        insert(conn, FILE_A, "transactions_001.csv", HASH_A, **overrides)

    assert row_counts(conn) == dict.fromkeys(TABLES, 0)

    assert insert(conn, FILE_A, "transactions_001.csv", HASH_A) is True
    assert row_counts(conn)["transactions"] == 3


def test_refuses_connection_with_open_transaction(conn):
    with conn.transaction(), pytest.raises(RuntimeError):
        insert(conn, FILE_A, "transactions_001.csv", HASH_A)
