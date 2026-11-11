"""Extraction par SLM (fonctions pures) : prompt, lecture de la réponse JSON,
normalisation vers les 7 champs du format CSV, garde-fou anti-hallucination.

Répartition du travail : le modèle ne fait que **repérer et recopier** les
informations telles qu'elles sont écrites dans le texte (il comprend qui
envoie et qui reçoit, quelle que soit la formulation). Toutes les
conversions (date en toutes lettres, « 14h30 », « 1 875,40 euros », nom de
pays -> code ISO) sont faites ici, de façon déterministe et testable.

Aucune I/O, aucun état global.
"""

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

# Champs demandés au modèle, valeurs recopiées du texte (null si absentes).
SLM_FIELDS = {
    "date": "date du virement, telle qu'écrite",
    "heure": "heure du virement, telle qu'écrite",
    "iban_origine": "IBAN du compte qui envoie l'argent (émetteur, débité)",
    "banque_origine": "nom de la banque du compte émetteur",
    "pays_origine": "pays du compte émetteur, tel qu'écrit",
    "iban_destinataire": "IBAN du compte qui reçoit l'argent (destinataire, crédité)",
    "pays_destinataire": "pays du compte destinataire, tel qu'écrit",
    "montant": "montant du virement avec son unité, tel qu'écrit",
}

# Sortie contrainte (Ollama « format ») : un objet avec exactement ces clés.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {field: {"type": ["string", "null"]} for field in SLM_FIELDS},
    "required": list(SLM_FIELDS),
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "Tu extrais un virement bancaire d'un texte en français.\n"
    "Réponds uniquement avec un objet JSON contenant ces champs :\n"
    + "\n".join(f"- {field} : {description}" for field, description in SLM_FIELDS.items())
    + "\n\nRègles :\n"
    "- Recopie chaque valeur exactement comme elle apparaît dans le texte, sans la "
    "reformuler, la traduire ni la convertir.\n"
    "- Recopie les IBAN en entier, chiffre par chiffre.\n"
    "- Si une information est absente du texte, mets null. N'invente jamais rien.\n"
    "- Si le texte ne décrit aucun virement, mets null partout."
)

# Exemple résolu (absent des données de test) pour guider un petit modèle.
_EXAMPLE_TEXT = (
    "Le compte NL91ABNA0417164300 (Pays-Bas), tenu par ABN AMRO, a reçu 980,00 € "
    "le 2 mars 2026 à 10h20, envoyés depuis le compte PT50000201231234567890154 "
    "de la banque Millennium BCP au Portugal."
)
_EXAMPLE_ANSWER = {
    "date": "2 mars 2026",
    "heure": "10h20",
    "iban_origine": "PT50000201231234567890154",
    "banque_origine": "Millennium BCP",
    "pays_origine": "Portugal",
    "iban_destinataire": "NL91ABNA0417164300",
    "pays_destinataire": "Pays-Bas",
    "montant": "980,00 €",
}

CSV_COLUMNS = [
    "datetime_transaction",
    "iban_origine",
    "pays_source",
    "banque_source",
    "iban_destinataire",
    "pays_destinataire",
    "montant",
]

MONTHS = {
    "janvier": 1,
    "février": 2,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "août": 8,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "décembre": 12,
    "decembre": 12,
}

COUNTRIES = {
    "france": "FR",
    "allemagne": "DE",
    "espagne": "ES",
    "italie": "IT",
    "belgique": "BE",
    "pays-bas": "NL",
    "portugal": "PT",
    "luxembourg": "LU",
    "suisse": "CH",
    "autriche": "AT",
    "irlande": "IE",
    "royaume-uni": "GB",
}

_SPACES = re.compile(r"[\s  ]+")
_LEADING_WORDS = re.compile(r"^(le|la|l'|en|au|aux|à|a|vers|depuis)\s+", re.IGNORECASE)
_CURRENCY = re.compile(r"(€|eur|euros?)$", re.IGNORECASE)
_ISO_DATE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_FR_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_WORD_DATE = re.compile(r"^(\d{1,2})(?:er)?\s+([a-zéû]+)\s+(\d{4})$")
_TIME = re.compile(r"^(\d{1,2})\s*[h:]\s*(\d{2})?(?::(\d{2}))?$")
_AMOUNT = re.compile(r"^\d+(\.\d{1,2})?$")
# Nombres du texte : « 1 875,40 », « 6200 », « 5000.00 »...
_NUMBER_IN_TEXT = re.compile(r"\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")


class TextExtractionError(ValueError):
    """Réponse du modèle inexploitable : non JSON, champ manquant, valeur illisible."""


class HallucinationError(TextExtractionError):
    """Valeur extraite absente du texte source : le modèle l'a inventée."""


def build_messages(text: str) -> list[dict[str, str]]:
    """Messages de chat pour le modèle : consignes, un exemple résolu, le texte."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _EXAMPLE_TEXT},
        {"role": "assistant", "content": json.dumps(_EXAMPLE_ANSWER, ensure_ascii=False)},
        {"role": "user", "content": text.strip()},
    ]


def parse_response(raw: str) -> dict[str, str | None]:
    """Réponse brute du modèle -> {champ SLM: valeur}. Lève TextExtractionError."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TextExtractionError(f"réponse du modèle non JSON : {raw[:80]!r}") from exc
    if not isinstance(data, dict):
        raise TextExtractionError(f"réponse du modèle inattendue (pas un objet) : {raw[:80]!r}")
    fields: dict[str, str | None] = {}
    for field in SLM_FIELDS:
        value = data.get(field)
        fields[field] = None if value is None else _SPACES.sub(" ", str(value)).strip() or None
    missing = [field for field, value in fields.items() if value is None]
    if missing:
        raise TextExtractionError(f"champ(s) manquant(s) dans la réponse : {', '.join(missing)}")
    return fields


def _strip_leading_words(value: str) -> str:
    while True:
        stripped = _LEADING_WORDS.sub("", value)
        if stripped == value:
            return value
        value = stripped


def normalize_date(value: str) -> date:
    text = _strip_leading_words(value.lower().strip(" ,."))
    if match := _ISO_DATE.match(text):
        year, month, day = (int(g) for g in match.groups())
    elif match := _FR_DATE.match(text):
        day, month, year = (int(g) for g in match.groups())
    elif (match := _WORD_DATE.match(text)) and match.group(2) in MONTHS:
        day, month, year = int(match.group(1)), MONTHS[match.group(2)], int(match.group(3))
    else:
        raise TextExtractionError(f"date illisible : {value!r}")
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise TextExtractionError(f"date invalide : {value!r}") from exc


def normalize_time(value: str) -> str:
    text = _strip_leading_words(value.lower().strip(" ,."))
    match = _TIME.match(text)
    if not match:
        raise TextExtractionError(f"heure illisible : {value!r}")
    hour, minute, second = (int(g) if g else 0 for g in match.groups())
    if hour > 23 or minute > 59 or second > 59:
        raise TextExtractionError(f"heure invalide : {value!r}")
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def normalize_amount(value: str) -> Decimal:
    text = _CURRENCY.sub("", value.strip().lower()).strip()
    text = _SPACES.sub("", text).replace(",", ".")
    if not _AMOUNT.match(text):
        raise TextExtractionError(f"montant illisible : {value!r}")
    return Decimal(text).quantize(Decimal("0.01"))


def normalize_country(value: str) -> str:
    text = _strip_leading_words(value.strip(" ()").lower())
    if re.fullmatch(r"[a-z]{2}", text):
        return text.upper()
    if text in COUNTRIES:
        return COUNTRIES[text]
    raise TextExtractionError(f"pays non reconnu : {value!r}")


def normalize_iban(value: str) -> str:
    return _SPACES.sub("", value).upper()


def normalize(fields: dict[str, str | None]) -> dict[str, str]:
    """Champs SLM -> les 7 colonnes du CSV (chaînes), avant validation commune."""
    when = f"{normalize_date(fields['date']).isoformat()} {normalize_time(fields['heure'])}"
    return {
        "datetime_transaction": when,
        "iban_origine": normalize_iban(fields["iban_origine"]),
        "pays_source": normalize_country(fields["pays_origine"]),
        "banque_source": fields["banque_origine"],
        "iban_destinataire": normalize_iban(fields["iban_destinataire"]),
        "pays_destinataire": normalize_country(fields["pays_destinataire"]),
        "montant": str(normalize_amount(fields["montant"])),
    }


def amounts_in_text(text: str) -> set[Decimal]:
    """Tous les nombres écrits dans le texte (« 1 875,40 » -> 1875.40)."""
    numbers = set()
    for match in _NUMBER_IN_TEXT.findall(text):
        try:
            numbers.add(Decimal(_SPACES.sub("", match).replace(",", ".")))
        except InvalidOperation:  # pragma: no cover - la regex ne produit que des nombres
            continue
    return numbers


def check_against_source(record: dict[str, str], text: str) -> None:
    """Garde-fou : chaque IBAN doit figurer tel quel dans le texte (espaces
    ignorés) et le montant doit correspondre à un nombre du texte."""
    compact = _SPACES.sub("", text).upper()
    for column, label in (("iban_origine", "émetteur"), ("iban_destinataire", "destinataire")):
        if record[column] not in compact:
            raise HallucinationError(
                f"IBAN {label} {record[column]!r} absent du texte source (valeur inventée)"
            )
    if Decimal(record["montant"]) not in amounts_in_text(text):
        raise HallucinationError(
            f"montant {record['montant']} absent du texte source (valeur inventée)"
        )


def extract_from_response(raw: str, text: str) -> dict[str, str]:
    """Réponse brute du modèle + texte source -> 7 colonnes vérifiées."""
    record = normalize(parse_response(raw))
    check_against_source(record, text)
    return record
