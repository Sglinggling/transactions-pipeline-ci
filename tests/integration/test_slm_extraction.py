"""Extraction par le vrai SLM (Ollama) sur les textes générés : valeurs en dur."""

import pytest

from pipeline.generate_texts import TEXTS
from pipeline.loading import TransactionValidationError, load_slm_transaction
from pipeline.slm import SlmSettings, chat_json
from pipeline.text_extraction import RESPONSE_SCHEMA, build_messages, extract_from_response

pytestmark = pytest.mark.integration

EXPECTED_RECORDS = [
    {
        "datetime_transaction": "2026-09-12 14:30:00",
        "iban_origine": "FR7630004000010000000000101",
        "pays_source": "FR",
        "banque_source": "BNP Paribas",
        "iban_destinataire": "DE89370400440532013000",
        "pays_destinataire": "DE",
        "montant": "6200.00",
    },
    {
        "datetime_transaction": "2026-10-03 09:05:00",
        "iban_origine": "ES9121000418450200051332",
        "pays_source": "ES",
        "banque_source": "Santander",
        "iban_destinataire": "IT60X0542811101000000123456",
        "pays_destinataire": "IT",
        "montant": "1875.40",
    },
    {
        "datetime_transaction": "2026-10-15 16:45:00",
        "iban_origine": "BE68539007547034",
        "pays_source": "BE",
        "banque_source": "ING Belgique",
        "iban_destinataire": "FR7630004000010000000000202",
        "pays_destinataire": "FR",
        "montant": "5000.00",
    },
    {
        "datetime_transaction": "2026-11-01 08:15:00",
        "iban_origine": "DE89370400440532013000",
        "pays_source": "DE",
        "banque_source": "Deutsche Bank",
        "iban_destinataire": "FR7630003000010000000000303",
        "pays_destinataire": "FR",
        "montant": "7300.50",
    },
]

NO_TRANSACTION = (
    "Bonjour, la réunion de suivi du projet est reportée à jeudi prochain, "
    "même heure, salle B. Merci de confirmer votre présence."
)


@pytest.fixture(scope="module")
def settings():
    return SlmSettings.from_env()


def ask(settings, text):
    return chat_json(settings, build_messages(text), RESPONSE_SCHEMA)


@pytest.mark.parametrize("index", range(4), ids=[f"message_00{i}" for i in range(1, 5)])
def test_each_generated_text_gives_expected_transaction(settings, index):
    raw = ask(settings, TEXTS[index])
    assert extract_from_response(raw, TEXTS[index]) == EXPECTED_RECORDS[index]


def test_text_without_transaction_is_rejected(settings):
    with pytest.raises(TransactionValidationError, match="message_vide.txt, fichier : "):
        load_slm_transaction(ask(settings, NO_TRANSACTION), NO_TRANSACTION, "message_vide.txt")
