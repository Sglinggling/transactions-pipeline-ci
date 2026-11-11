import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from pipeline.processing import (
    flag_high_amount,
    sum_by_banque_source,
    sum_by_iban_destinataire,
    sum_by_iban_origine,
)

FR_BNP_1 = "FR7630004000010000000000101"
FR_BNP_2 = "FR7630004000010000000000202"
DE_DB = "DE89370400440532013000"
ES_SAN = "ES9121000418450200051332"


@pytest.fixture
def transactions():
    return pd.DataFrame(
        {
            "datetime_transaction": pd.to_datetime(
                [
                    "2025-01-15 08:00:00",
                    "2025-01-15 09:00:00",
                    "2025-01-15 10:00:00",
                    "2025-01-15 11:00:00",
                    "2025-01-15 12:00:00",
                ]
            ),
            "iban_origine": [FR_BNP_1, FR_BNP_2, DE_DB, FR_BNP_1, DE_DB],
            "pays_source": ["FR", "FR", "DE", "FR", "DE"],
            "banque_source": [
                "BNP Paribas",
                "BNP Paribas",
                "Deutsche Bank",
                "BNP Paribas",
                "Deutsche Bank",
            ],
            "iban_destinataire": [ES_SAN, ES_SAN, FR_BNP_1, DE_DB, FR_BNP_1],
            "pays_destinataire": ["ES", "ES", "FR", "DE", "FR"],
            "montant": [12500.00, 5000.00, 5000.01, 0.10, 0.20],
        }
    )


def test_sum_by_iban_origine(transactions):
    expected = pd.DataFrame(
        {"iban_origine": [DE_DB, FR_BNP_1, FR_BNP_2], "montant_total": [5000.21, 12500.10, 5000.00]}
    )
    assert_frame_equal(sum_by_iban_origine(transactions), expected)


def test_sum_by_banque_source(transactions):
    expected = pd.DataFrame(
        {"banque_source": ["BNP Paribas", "Deutsche Bank"], "montant_total": [17500.10, 5000.21]}
    )
    assert_frame_equal(sum_by_banque_source(transactions), expected)


def test_sum_by_iban_destinataire(transactions):
    expected = pd.DataFrame(
        {"iban_destinataire": [DE_DB, ES_SAN, FR_BNP_1], "montant_total": [0.10, 17500.00, 5000.21]}
    )
    assert_frame_equal(sum_by_iban_destinataire(transactions), expected)


def test_flag_high_amount_default_threshold(transactions):
    result = flag_high_amount(transactions)
    assert result["montant_eleve"].tolist() == [True, False, True, False, False]


def test_flag_high_amount_boundaries():
    df = pd.DataFrame({"montant": [5000.00, 5000.01]})
    assert flag_high_amount(df)["montant_eleve"].tolist() == [False, True]


def test_flag_high_amount_custom_threshold(transactions):
    result = flag_high_amount(transactions, threshold=0.15)
    assert result["montant_eleve"].tolist() == [True, True, True, False, True]


def test_flag_high_amount_keeps_original_columns(transactions):
    result = flag_high_amount(transactions)
    assert list(result.columns) == [*transactions.columns, "montant_eleve"]
    assert result["montant_eleve"].dtype == bool


@pytest.mark.parametrize(
    "func",
    [sum_by_iban_origine, sum_by_banque_source, sum_by_iban_destinataire, flag_high_amount],
)
def test_purity(func, transactions):
    before = transactions.copy(deep=True)

    first = func(transactions)
    second = func(transactions)

    assert_frame_equal(transactions, before)  # entrée non mutée
    assert_frame_equal(first, second)  # même entrée -> même sortie
    assert first is not transactions  # nouvel objet renvoyé
