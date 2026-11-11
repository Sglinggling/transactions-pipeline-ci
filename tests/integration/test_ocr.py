"""OCR réel (Tesseract) sur les images générées : valeurs attendues en dur."""

import pytest

from pipeline.extraction import extract_transaction
from pipeline.generate_images import IMAGE_TRANSACTIONS, render_notice
from pipeline.loading import TransactionValidationError, load_ocr_transaction
from pipeline.ocr import OcrError, image_to_text

pytestmark = pytest.mark.integration

EXPECTED_RECORDS = [
    {
        "datetime_transaction": "2025-01-18 09:15:00",
        "iban_origine": "FR7630004000010000000000101",
        "pays_source": "FR",
        "banque_source": "BNP Paribas",
        "iban_destinataire": "DE89370400440532013000",
        "pays_destinataire": "DE",
        "montant": "7350.50",
    },
    {
        "datetime_transaction": "2025-01-18 10:40:00",
        "iban_origine": "ES9121000418450200051332",
        "pays_source": "ES",
        "banque_source": "Santander",
        "iban_destinataire": "FR7630004000010000000000202",
        "pays_destinataire": "FR",
        "montant": "5000.00",
    },
    {
        "datetime_transaction": "2025-01-18 14:05:30",
        "iban_origine": "BE68539007547034",
        "pays_source": "BE",
        "banque_source": "ING Belgique",
        "iban_destinataire": "IT60X0542811101000000123456",
        "pays_destinataire": "IT",
        "montant": "1250.75",
    },
]


@pytest.mark.parametrize("index", [0, 1, 2], ids=["virement_001", "virement_002", "virement_003"])
def test_each_generated_image_gives_expected_transaction(index):
    text = image_to_text(render_notice(IMAGE_TRANSACTIONS[index]), f"virement_{index + 1:03d}.png")
    assert extract_transaction(text) == EXPECTED_RECORDS[index]


def test_typed_transaction_from_image():
    df = load_ocr_transaction(image_to_text(render_notice(IMAGE_TRANSACTIONS[1]), "v"), "v")
    assert df["montant"].tolist() == [5000.00]
    assert str(df["datetime_transaction"].iloc[0]) == "2025-01-18 10:40:00"


def test_garbage_bytes_are_unreadable():
    with pytest.raises(OcrError, match="incoming/casse.png : image illisible"):
        image_to_text(b"ceci n'est pas une image", "incoming/casse.png")


def test_image_with_missing_field():
    fields = {k: v for k, v in IMAGE_TRANSACTIONS[0].items() if k != "montant"}
    with pytest.raises(TransactionValidationError, match="manquant.* : Montant$"):
        load_ocr_transaction(image_to_text(render_notice(fields), "v"), "v")
