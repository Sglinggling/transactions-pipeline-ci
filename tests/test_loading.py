import pandas as pd
import pytest

from pipeline.loading import EXPECTED_COLUMNS, TransactionValidationError, load_transactions

HEADER = ",".join(EXPECTED_COLUMNS)
ROW_1 = (
    "2025-01-15 08:09:01,DE89370400440532013000,DE,Deutsche Bank,"
    "IT60X0542811101000000123456,IT,741.58"
)
ROW_2 = (
    "2025-01-15 08:25:14,FR7630004000010000000000101,FR,BNP Paribas,"
    "ES9121000418450200051332,ES,12500.00"
)


def load(content, name="transactions.csv"):
    return load_transactions(content.encode("utf-8"), name)


def test_loads_valid_csv():
    df = load(f"{HEADER}\n{ROW_1}\n{ROW_2}\n")

    assert list(df.columns) == EXPECTED_COLUMNS
    assert len(df) == 2
    assert df["montant"].tolist() == [741.58, 12500.00]
    assert df["datetime_transaction"].iloc[0] == pd.Timestamp("2025-01-15 08:09:01")
    assert df["banque_source"].tolist() == ["Deutsche Bank", "BNP Paribas"]


def test_missing_column():
    header = HEADER.replace(",banque_source", "")
    row = ROW_1.replace(",Deutsche Bank", "")
    content, name = f"{header}\n{row}\n", "sans_banque.csv"

    with pytest.raises(TransactionValidationError) as exc:
        load(content, name)

    assert exc.value.line == 1
    assert str(exc.value) == "sans_banque.csv, ligne 1 : colonne(s) manquante(s) : banque_source"


def test_non_numeric_amount():
    row = ROW_2.replace("12500.00", "douze")
    content, name = f"{HEADER}\n{ROW_1}\n{row}\n", "montant_texte.csv"

    with pytest.raises(TransactionValidationError) as exc:
        load(content, name)

    assert exc.value.line == 3
    assert str(exc.value) == "montant_texte.csv, ligne 3 : montant non numérique : 'douze'"


def test_negative_amount():
    row = ROW_1.replace("741.58", "-741.58")
    content, name = f"{HEADER}\n{row}\n{ROW_2}\n", "negatif.csv"

    with pytest.raises(TransactionValidationError) as exc:
        load(content, name)

    assert exc.value.line == 2
    assert str(exc.value) == (
        "negatif.csv, ligne 2 : montant doit être strictement positif : '-741.58'"
    )


def test_zero_amount():
    row = ROW_1.replace("741.58", "0.00")
    with pytest.raises(TransactionValidationError, match="strictement positif"):
        load(f"{HEADER}\n{row}\n")


def test_missing_value():
    row = ROW_2.replace("BNP Paribas", "")
    with pytest.raises(TransactionValidationError) as exc:
        load(f"{HEADER}\n{ROW_1}\n{row}\n")

    assert exc.value.line == 3
    assert "valeur manquante dans 'banque_source'" in str(exc.value)


def test_unparsable_datetime():
    row = ROW_1.replace("2025-01-15 08:09:01", "pas-une-date")
    with pytest.raises(TransactionValidationError) as exc:
        load(f"{HEADER}\n{row}\n")

    assert exc.value.line == 2
    assert "datetime_transaction non parsable : 'pas-une-date'" in str(exc.value)


def test_empty_file():
    with pytest.raises(TransactionValidationError, match="fichier vide"):
        load("")


def test_error_message_uses_object_key():
    row = ROW_1.replace("741.58", "abc")
    with pytest.raises(TransactionValidationError) as exc:
        load(f"{HEADER}\n{row}\n", name="incoming/transactions_001.csv")
    assert str(exc.value) == (
        "incoming/transactions_001.csv, ligne 2 : montant non numérique : 'abc'"
    )
