"""Chargement et validation des transactions (CSV, texte OCR).

Valide des transactions déjà en mémoire, quelle que soit leur provenance :
contenu CSV (bytes) ou texte OCR d'une image. Toutes passent par les mêmes
règles (validate_transactions) et produisent le même schéma. Aucune I/O ici.
"""

import io

import numpy as np
import pandas as pd

from pipeline.extraction import ExtractionError, extract_transaction

EXPECTED_COLUMNS = [
    "datetime_transaction",
    "iban_origine",
    "pays_source",
    "banque_source",
    "iban_destinataire",
    "pays_destinataire",
    "montant",
]

# Ligne 1 = en-tête, donc la ligne de données d'index i est la ligne i + 2 du fichier.
_HEADER_OFFSET = 2


class TransactionValidationError(ValueError):
    """Les données (CSV ou image) ne respectent pas le format attendu."""

    def __init__(self, name: str, line: int | None, reason: str) -> None:
        self.name = name
        self.line = line
        self.reason = reason
        location = f"ligne {line}" if line is not None else "fichier"
        super().__init__(f"{name}, {location} : {reason}")


def _first_position(mask: pd.Series) -> int:
    return int(mask.to_numpy().nonzero()[0][0])


def validate_transactions(
    raw: pd.DataFrame, name: str, line_offset: int | None = _HEADER_OFFSET
) -> pd.DataFrame:
    """Règles communes à toutes les sources (CSV, OCR) : colonnes présentes,
    aucune valeur manquante, datetime parsable, montant numérique et > 0.

    raw contient des chaînes brutes. Renvoie un nouveau DataFrame typé
    (datetime64, float64), colonnes dans l'ordre EXPECTED_COLUMNS. La ligne
    citée dans les erreurs vaut position + line_offset (None : pas de ligne,
    cas d'une image).
    """

    def fail(position: int | None, reason: str) -> TransactionValidationError:
        line = None if position is None or line_offset is None else position + line_offset
        return TransactionValidationError(name, line, reason)

    missing = [c for c in EXPECTED_COLUMNS if c not in raw.columns]
    if missing:
        header_line = 1 if line_offset is not None else None
        raise TransactionValidationError(
            name, header_line, f"colonne(s) manquante(s) : {', '.join(missing)}"
        )

    df = raw[EXPECTED_COLUMNS].copy()

    for column in EXPECTED_COLUMNS:
        is_missing = df[column].isna() | (df[column].str.strip() == "")
        if is_missing.any():
            raise fail(_first_position(is_missing), f"valeur manquante dans '{column}'")

    dates = pd.to_datetime(df["datetime_transaction"], format="ISO8601", errors="coerce")
    if dates.isna().any():
        position = _first_position(dates.isna())
        value = df["datetime_transaction"].iloc[position]
        raise fail(position, f"datetime_transaction non parsable : '{value}'")

    amounts = pd.to_numeric(df["montant"], errors="coerce")
    not_numeric = ~np.isfinite(amounts)  # NaN, "inf", "nan"...
    if not_numeric.any():
        position = _first_position(not_numeric)
        raise fail(position, f"montant non numérique : '{df['montant'].iloc[position]}'")

    if (amounts <= 0).any():
        position = _first_position(amounts <= 0)
        value = df["montant"].iloc[position]
        raise fail(position, f"montant doit être strictement positif : '{value}'")

    df["datetime_transaction"] = dates
    df["montant"] = amounts.astype("float64")
    return df


def load_transactions(content: bytes, name: str) -> pd.DataFrame:
    """Parse un contenu CSV de transactions, le valide et renvoie un DataFrame typé.

    name (nom de l'objet ou du fichier) sert uniquement aux messages d'erreur.
    Lève TransactionValidationError au premier problème rencontré.
    """
    try:
        raw = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False, na_values=[""])
    except pd.errors.EmptyDataError as exc:
        raise TransactionValidationError(name, None, "fichier vide") from exc
    return validate_transactions(raw, name)


def load_ocr_transaction(text: str, name: str) -> pd.DataFrame:
    """Texte OCR d'un avis de virement -> DataFrame typé d'une ligne, même schéma
    que load_transactions. Lève TransactionValidationError (champ manquant ou
    illisible, valeur invalide)."""
    try:
        record = extract_transaction(text)
    except ExtractionError as exc:
        raise TransactionValidationError(name, None, str(exc)) from exc
    return validate_transactions(
        pd.DataFrame([record], columns=EXPECTED_COLUMNS, dtype=str), name, line_offset=None
    )
