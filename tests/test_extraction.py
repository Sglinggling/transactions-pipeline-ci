import pandas as pd
import pytest

from pipeline.extraction import NOTICE_LABELS, ExtractionError, extract_transaction
from pipeline.loading import (
    TransactionValidationError,
    load_ocr_transaction,
    load_transactions,
)

# Texte tel que rendu par Tesseract pour un avis (titre, lignes vides, IBAN groupés).
NOMINAL = """AVIS DE VIREMENT

Date et heure : 2025-01-18 09:15:00

IBAN origine : FR76 3000 4000 0100 0000 0000 101
Pays source : FR

Banque source : BNP Paribas

IBAN destinataire : DE89 3704 0044 0532 0130 00
Pays destinataire : DE

Montant : 7350,50 EUR
"""

EXPECTED = {
    "datetime_transaction": "2025-01-18 09:15:00",
    "iban_origine": "FR7630004000010000000000101",
    "pays_source": "FR",
    "banque_source": "BNP Paribas",
    "iban_destinataire": "DE89370400440532013000",
    "pays_destinataire": "DE",
    "montant": "7350.50",
}


def replace_line(text, label, new_line):
    lines = [new_line if line.startswith(label) else line for line in text.splitlines()]
    return "\n".join(lines)


def drop_line(text, label):
    return "\n".join(line for line in text.splitlines() if not line.startswith(label))


def test_nominal():
    assert extract_transaction(NOMINAL) == EXPECTED


def test_columns_in_csv_order():
    assert list(extract_transaction(NOMINAL)) == list(NOTICE_LABELS)


def test_parasitic_spaces_and_case():
    text = """   AVIS  DE  VIREMENT
   date  ET heure   :   2025-01-18    09:15:00
iban ORIGINE:fr76 3000 4000   0100 0000 0000 101
  Pays source :  fr
Banque   source :   BNP    Paribas
IBAN destinataire : de89 3704 0044 0532 0130 00
pays destinataire : de
MONTANT  :  7350,50   eur  """
    assert extract_transaction(text) == EXPECTED


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("7350,50 EUR", "7350.50"),
        ("7 350,50 EUR", "7350.50"),
        ("7350.50 EUR", "7350.50"),
        ("7350,50 €", "7350.50"),
        ("7350,50", "7350.50"),
        ("12 500,00 eur", "12500.00"),
    ],
)
def test_decimal_separator_and_currency(printed, expected):
    text = replace_line(NOMINAL, "Montant", f"Montant : {printed}")
    assert extract_transaction(text)["montant"] == expected


def test_missing_field():
    with pytest.raises(ExtractionError) as exc:
        extract_transaction(drop_line(NOMINAL, "Montant"))
    assert str(exc.value) == "champ(s) manquant(s) ou illisible(s) : Montant"


def test_several_missing_fields_listed_in_order():
    text = drop_line(drop_line(NOMINAL, "Pays source"), "IBAN origine")
    with pytest.raises(ExtractionError, match="IBAN origine, Pays source$"):
        extract_transaction(text)


def test_unreadable_label_counts_as_missing():
    # Libellé mal reconnu : pas de correction ad hoc, le champ est manquant.
    text = replace_line(NOMINAL, "Banque source", "Banqve sovrce : BNP Paribas")
    with pytest.raises(ExtractionError, match="Banque source"):
        extract_transaction(text)


def test_empty_value():
    text = replace_line(NOMINAL, "Montant", "Montant :   EUR")
    with pytest.raises(ExtractionError, match="champ 'Montant' vide ou illisible"):
        extract_transaction(text)


def test_duplicate_field():
    with pytest.raises(ExtractionError, match="champ 'Montant' présent plusieurs fois"):
        extract_transaction(NOMINAL + "Montant : 10,00 EUR\n")


def test_empty_text():
    with pytest.raises(ExtractionError, match="Date et heure, IBAN origine"):
        extract_transaction("")


def test_purity():
    text = str(NOMINAL)
    labels_before = dict(NOTICE_LABELS)

    first = extract_transaction(text)
    first["montant"] = "modifié"
    second = extract_transaction(text)

    assert text == NOMINAL
    assert second == EXPECTED  # la mutation du premier résultat n'a aucun effet
    assert first is not second
    assert NOTICE_LABELS == labels_before


# --- Validation commune (mêmes règles et même schéma que les CSV) ---


def test_ocr_transaction_typed_like_csv():
    df = load_ocr_transaction(NOMINAL, "incoming/virement_001.png")

    assert df.to_dict("records") == [
        {
            "datetime_transaction": pd.Timestamp("2025-01-18 09:15:00"),
            "iban_origine": "FR7630004000010000000000101",
            "pays_source": "FR",
            "banque_source": "BNP Paribas",
            "iban_destinataire": "DE89370400440532013000",
            "pays_destinataire": "DE",
            "montant": 7350.50,
        }
    ]


def test_same_schema_as_csv_loading():
    csv = (
        "datetime_transaction,iban_origine,pays_source,banque_source,"
        "iban_destinataire,pays_destinataire,montant\n"
        "2025-01-18 09:15:00,FR7630004000010000000000101,FR,BNP Paribas,"
        "DE89370400440532013000,DE,7350.50\n"
    )
    from_csv = load_transactions(csv.encode(), "t.csv")
    from_ocr = load_ocr_transaction(NOMINAL, "v.png")

    assert list(from_ocr.columns) == list(from_csv.columns)
    assert from_ocr.dtypes.to_dict() == from_csv.dtypes.to_dict()
    pd.testing.assert_frame_equal(from_ocr, from_csv)


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("Montant : douze EUR", "montant non numérique : 'douze'"),
        ("Montant : -10,00 EUR", "montant doit être strictement positif : '-10.00'"),
        ("Montant : 0,00 EUR", "montant doit être strictement positif : '0.00'"),
    ],
)
def test_invalid_amount(line, message):
    with pytest.raises(TransactionValidationError) as exc:
        load_ocr_transaction(replace_line(NOMINAL, "Montant", line), "v.png")
    assert str(exc.value) == f"v.png, fichier : {message}"


def test_invalid_datetime():
    text = replace_line(NOMINAL, "Date et heure", "Date et heure : 2025-13-45 99:00:00")
    with pytest.raises(TransactionValidationError, match="datetime_transaction non parsable"):
        load_ocr_transaction(text, "v.png")


def test_extraction_error_becomes_validation_error():
    with pytest.raises(TransactionValidationError) as exc:
        load_ocr_transaction(drop_line(NOMINAL, "Montant"), "incoming/virement_004.png")
    assert str(exc.value) == (
        "incoming/virement_004.png, fichier : champ(s) manquant(s) ou illisible(s) : Montant"
    )
