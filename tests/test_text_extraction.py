import json
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from pipeline.loading import (
    TransactionValidationError,
    decode_text,
    load_slm_transaction,
    load_transactions,
)
from pipeline.text_extraction import (
    RESPONSE_SCHEMA,
    SLM_FIELDS,
    SYSTEM_PROMPT,
    HallucinationError,
    TextExtractionError,
    amounts_in_text,
    build_messages,
    check_against_source,
    extract_from_response,
    normalize,
    normalize_amount,
    normalize_country,
    normalize_date,
    normalize_time,
    parse_response,
)

TEXT = (
    "Le 12 septembre 2026 à 14h30, le compte FR76 3000 4000 0100 0000 0000 101 de la "
    "banque BNP Paribas (France) a envoyé 6200 € vers le compte "
    "DE89 3704 0044 0532 0130 00 en Allemagne."
)

# Réponse simulée d'un modèle qui recopie fidèlement le texte.
ANSWER = {
    "date": "12 septembre 2026",
    "heure": "14h30",
    "iban_origine": "FR76 3000 4000 0100 0000 0000 101",
    "banque_origine": "BNP Paribas",
    "pays_origine": "France",
    "iban_destinataire": "DE89 3704 0044 0532 0130 00",
    "pays_destinataire": "Allemagne",
    "montant": "6200 €",
}

EXPECTED = {
    "datetime_transaction": "2026-09-12 14:30:00",
    "iban_origine": "FR7630004000010000000000101",
    "pays_source": "FR",
    "banque_source": "BNP Paribas",
    "iban_destinataire": "DE89370400440532013000",
    "pays_destinataire": "DE",
    "montant": "6200.00",
}


def answer(**overrides):
    return json.dumps({**ANSWER, **overrides}, ensure_ascii=False)


# --- Prompt et schéma ---


def test_messages_structure():
    messages = build_messages(f"  {TEXT}\n")

    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[0]["content"] == SYSTEM_PROMPT
    assert messages[-1]["content"] == TEXT  # texte source nettoyé des espaces de bord
    # L'exemple résolu respecte lui-même le schéma.
    assert set(json.loads(messages[2]["content"])) == set(SLM_FIELDS)


def test_prompt_lists_every_field_and_forbids_inventing():
    for field in SLM_FIELDS:
        assert f"- {field} :" in SYSTEM_PROMPT
    assert "N'invente jamais rien" in SYSTEM_PROMPT
    assert "mets null" in SYSTEM_PROMPT


def test_schema_constrains_exact_keys():
    assert RESPONSE_SCHEMA["required"] == list(SLM_FIELDS)
    assert RESPONSE_SCHEMA["additionalProperties"] is False
    assert all(p == {"type": ["string", "null"]} for p in RESPONSE_SCHEMA["properties"].values())


# --- Lecture de la réponse ---


def test_parse_response_nominal():
    assert parse_response(answer()) == ANSWER


def test_parse_response_collapses_spaces():
    parsed = parse_response(answer(banque_origine="  BNP   Paribas "))
    assert parsed["banque_origine"] == "BNP Paribas"


@pytest.mark.parametrize("raw", ["pas du json", '{"date": ', ""])
def test_non_json_response(raw):
    with pytest.raises(TextExtractionError, match="réponse du modèle non JSON"):
        parse_response(raw)


def test_json_but_not_an_object():
    with pytest.raises(TextExtractionError, match="pas un objet"):
        parse_response("[1, 2]")


def test_missing_and_null_fields():
    data = dict(ANSWER)
    del data["montant"]
    data["heure"] = None
    data["pays_origine"] = "   "
    with pytest.raises(TextExtractionError) as exc:
        parse_response(json.dumps(data))
    assert str(exc.value) == "champ(s) manquant(s) dans la réponse : heure, pays_origine, montant"


# --- Normalisation ---


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("12 septembre 2026", date(2026, 9, 12)),
        ("le 12 septembre 2026", date(2026, 9, 12)),
        ("1er novembre 2026", date(2026, 11, 1)),
        ("3 août 2026", date(2026, 8, 3)),
        ("03/10/2026", date(2026, 10, 3)),
        ("2026-10-15", date(2026, 10, 15)),
    ],
)
def test_normalize_date(value, expected):
    assert normalize_date(value) == expected


@pytest.mark.parametrize("value", ["demain", "12 brumaire 2026", "31/02/2026", "2026-13-01"])
def test_invalid_date(value):
    with pytest.raises(TextExtractionError, match="date"):
        normalize_date(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("14h30", "14:30:00"),
        ("à 14h30", "14:30:00"),
        ("8h15", "08:15:00"),
        ("14 h 30", "14:30:00"),
        ("9h", "09:00:00"),
        ("09:05", "09:05:00"),
        ("16:45:20", "16:45:20"),
    ],
)
def test_normalize_time(value, expected):
    assert normalize_time(value) == expected


@pytest.mark.parametrize("value", ["midi", "25h00", "14h75"])
def test_invalid_time(value):
    with pytest.raises(TextExtractionError, match="heure"):
        normalize_time(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("6200 €", Decimal("6200.00")),
        ("1 875,40 euros", Decimal("1875.40")),
        ("5000.00 EUR", Decimal("5000.00")),
        ("7 300,50 €", Decimal("7300.50")),
        ("980", Decimal("980.00")),
    ],
)
def test_normalize_amount(value, expected):
    assert normalize_amount(value) == expected


@pytest.mark.parametrize("value", ["beaucoup", "1.234,56 €", "12,345,00", "-10 €"])
def test_invalid_amount(value):
    with pytest.raises(TextExtractionError, match="montant illisible"):
        normalize_amount(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("France", "FR"),
        ("en Allemagne", "DE"),
        ("(Belgique)", "BE"),
        ("fr", "FR"),
        ("Pays-Bas", "NL"),
    ],
)
def test_normalize_country(value, expected):
    assert normalize_country(value) == expected


def test_unknown_country():
    with pytest.raises(TextExtractionError, match="pays non reconnu : 'Atlantide'"):
        normalize_country("Atlantide")


def test_normalize_to_csv_columns():
    assert normalize(parse_response(answer())) == EXPECTED


# --- Garde-fou anti-hallucination ---


def test_amounts_in_text():
    text = "1 875,40 euros, 6200 €, 5000.00 EUR, le 03/10/2026"
    assert {Decimal("1875.40"), Decimal("6200"), Decimal("5000.00")} <= amounts_in_text(text)


def test_guard_accepts_faithful_extraction():
    check_against_source(EXPECTED, TEXT)  # ne lève rien


def test_invented_iban_is_rejected():
    record = {**EXPECTED, "iban_destinataire": "DE89370400440532013999"}
    with pytest.raises(HallucinationError) as exc:
        check_against_source(record, TEXT)
    assert str(exc.value) == (
        "IBAN destinataire 'DE89370400440532013999' absent du texte source (valeur inventée)"
    )


def test_invented_amount_is_rejected():
    with pytest.raises(HallucinationError, match="montant 6300.00 absent du texte source"):
        check_against_source({**EXPECTED, "montant": "6300.00"}, TEXT)


def test_iban_spaces_ignored_by_guard():
    compact_text = TEXT.replace("FR76 3000 4000 0100 0000 0000 101", "FR7630004000010000000000101")
    check_against_source(EXPECTED, compact_text)


def test_extract_from_response_end_to_end():
    assert extract_from_response(answer(), TEXT) == EXPECTED


def test_extract_rejects_hallucinated_answer():
    with pytest.raises(HallucinationError, match="IBAN émetteur"):
        extract_from_response(answer(iban_origine="FR7699999999999999999999999"), TEXT)


def test_text_without_transaction_all_null():
    raw = json.dumps(dict.fromkeys(SLM_FIELDS))
    with pytest.raises(TextExtractionError, match="champ\\(s\\) manquant\\(s\\)"):
        extract_from_response(raw, "Bonjour, la réunion est reportée à jeudi.")


# --- Pureté ---


def test_purity():
    raw, text = answer(), str(TEXT)

    first = extract_from_response(raw, text)
    first["montant"] = "modifié"
    second = extract_from_response(raw, text)

    assert second == EXPECTED
    assert text == TEXT
    assert build_messages(TEXT) == build_messages(TEXT)
    assert build_messages(TEXT) is not build_messages(TEXT)


# --- Chargement : validation commune, même schéma que les CSV ---


def test_slm_transaction_same_schema_as_csv():
    csv = (
        "datetime_transaction,iban_origine,pays_source,banque_source,"
        "iban_destinataire,pays_destinataire,montant\n"
        "2026-09-12 14:30:00,FR7630004000010000000000101,FR,BNP Paribas,"
        "DE89370400440532013000,DE,6200.00\n"
    )
    from_csv = load_transactions(csv.encode(), "t.csv")
    from_slm = load_slm_transaction(answer(), TEXT, "incoming/message_001.txt")
    pd.testing.assert_frame_equal(from_slm, from_csv)


def test_slm_errors_become_validation_errors():
    with pytest.raises(TransactionValidationError) as exc:
        load_slm_transaction(answer(montant="6300 €"), TEXT, "incoming/message_001.txt")
    assert str(exc.value) == (
        "incoming/message_001.txt, fichier : montant 6300.00 absent du texte source "
        "(valeur inventée)"
    )


def test_decode_text():
    assert decode_text(b"Le 12 septembre", "m.txt") == "Le 12 septembre"
    with pytest.raises(TransactionValidationError, match="m.txt, fichier : texte non encodé"):
        decode_text(b"\xff\xfe\xfa", "m.txt")
