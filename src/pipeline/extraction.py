"""Extraction (fonctions pures) : texte OCR d'un avis de virement -> transaction.

Le texte contient des lignes « libellé : valeur ». On normalise (espaces,
casse, séparateur décimal) puis on renvoie les 7 champs du format CSV sous
forme de chaînes ; la validation des valeurs (datetime, montant > 0...) est
faite ensuite par loading.validate_transactions, comme pour les CSV.

Aucune I/O, aucun état global ; aucune correction ad hoc des caractères mal
reconnus : un texte inexploitable lève ExtractionError.
"""

import re

# Format d'un avis : colonne CSV -> libellé imprimé (sans accents : modèle
# Tesseract "eng"). Source unique, utilisée aussi par generate_images.py.
NOTICE_LABELS = {
    "datetime_transaction": "Date et heure",
    "iban_origine": "IBAN origine",
    "pays_source": "Pays source",
    "banque_source": "Banque source",
    "iban_destinataire": "IBAN destinataire",
    "pays_destinataire": "Pays destinataire",
    "montant": "Montant",
}

_WHITESPACE = re.compile(r"\s+")
_CURRENCY = re.compile(r"(EUR|€)$", re.IGNORECASE)


class ExtractionError(ValueError):
    """Texte OCR inexploitable : champ manquant, vide ou en double."""


def _collapse(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _normalize_label(label: str) -> str:
    return _collapse(label).lower()


_FIELD_BY_LABEL = {_normalize_label(label): field for field, label in NOTICE_LABELS.items()}


def _normalize_value(field: str, value: str) -> str:
    value = _collapse(value)
    if field.startswith("iban_"):
        return value.replace(" ", "").upper()  # IBAN imprimé par groupes de 4
    if field.startswith("pays_"):
        return value.upper()
    if field == "montant":
        # « 7 350,50 EUR » -> « 7350.50 » ; la validité numérique est contrôlée ensuite.
        amount = _CURRENCY.sub("", value).replace(" ", "")
        return amount.replace(",", ".")
    return value  # date et banque : espaces normalisés, casse conservée


def extract_transaction(text: str) -> dict[str, str]:
    """Renvoie {colonne: valeur normalisée} pour les 7 colonnes du format CSV.

    Les lignes sans « : » (titre, lignes vides) et les libellés inconnus sont
    ignorés. Lève ExtractionError si un champ manque, est vide ou apparaît
    deux fois.
    """
    found: dict[str, str] = {}
    for line in text.splitlines():
        label, sep, value = line.partition(":")  # 1er « : » : l'heure en contient d'autres
        if not sep:
            continue
        field = _FIELD_BY_LABEL.get(_normalize_label(label))
        if field is None:
            continue
        if field in found:
            raise ExtractionError(f"champ '{NOTICE_LABELS[field]}' présent plusieurs fois")
        normalized = _normalize_value(field, value)
        if not normalized:
            raise ExtractionError(f"champ '{NOTICE_LABELS[field]}' vide ou illisible")
        found[field] = normalized

    missing = [NOTICE_LABELS[f] for f in NOTICE_LABELS if f not in found]
    if missing:
        raise ExtractionError(f"champ(s) manquant(s) ou illisible(s) : {', '.join(missing)}")
    return {field: found[field] for field in NOTICE_LABELS}
