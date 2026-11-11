"""Traitements des transactions : fonctions pures uniquement.

Aucune I/O, aucun état global, aucune mutation du DataFrame reçu :
chaque fonction renvoie un nouvel objet.
"""

import pandas as pd

TOTAL_COLUMN = "montant_total"
HIGH_AMOUNT_COLUMN = "montant_eleve"
DEFAULT_HIGH_AMOUNT_THRESHOLD = 5000


def _sum_by(df: pd.DataFrame, key: str) -> pd.DataFrame:
    # Arrondi à 2 décimales pour neutraliser les erreurs de flottants (0.1 + 0.2).
    return (
        df.groupby(key, as_index=False, sort=True)["montant"]
        .sum()
        .rename(columns={"montant": TOTAL_COLUMN})
        .round({TOTAL_COLUMN: 2})
    )


def sum_by_iban_origine(df: pd.DataFrame) -> pd.DataFrame:
    """Montant total émis par IBAN d'origine, trié par IBAN."""
    return _sum_by(df, "iban_origine")


def sum_by_banque_source(df: pd.DataFrame) -> pd.DataFrame:
    """Montant total émis par banque source, trié par banque."""
    return _sum_by(df, "banque_source")


def sum_by_iban_destinataire(df: pd.DataFrame) -> pd.DataFrame:
    """Montant total reçu par IBAN destinataire, trié par IBAN."""
    return _sum_by(df, "iban_destinataire")


def flag_high_amount(
    df: pd.DataFrame, threshold: float = DEFAULT_HIGH_AMOUNT_THRESHOLD
) -> pd.DataFrame:
    """Copie de df avec une colonne booléenne : montant strictement supérieur au seuil."""
    return df.assign(**{HIGH_AMOUNT_COLUMN: df["montant"] > threshold})
