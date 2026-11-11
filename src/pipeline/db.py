"""Accès à la base PostgreSQL : connexion, schéma et insertion.

Module d'I/O : rien ici ne doit être importé par processing.py.
"""

import os
import sys
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pandas as pd
import psycopg
from psycopg import pq

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "sql" / "schema.sql"
_CENTS = Decimal("0.01")

# Colonnes produites par processing.py (non modifié) -> colonnes en base.
_FLAG_COLUMN = "montant_eleve"
_TOTAL_COLUMN = "montant_total"

_INSERT_FILE = """
    INSERT INTO processed_files (filename, content_hash)
    VALUES (%s, %s)
    ON CONFLICT (content_hash) DO NOTHING
    RETURNING id
"""

_INSERT_TRANSACTION = """
    INSERT INTO transactions (
        file_id, datetime_transaction, iban_origine, pays_source, banque_source,
        iban_destinataire, pays_destinataire, montant, is_flagged
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

# (table, colonne clé en base, colonne clé dans le DataFrame de sommes)
_ALL_TABLES = (
    "transactions",
    "processed_files",
    "total_sent_by_iban",
    "total_sent_by_bank",
    "total_received_by_iban",
)

_TOTAL_TABLES = {
    "sent_by_iban": ("total_sent_by_iban", "iban", "iban_origine"),
    "sent_by_bank": ("total_sent_by_bank", "banque", "banque_source"),
    "received_by_iban": ("total_received_by_iban", "iban", "iban_destinataire"),
}


def get_connection(database_url: str | None = None) -> psycopg.Connection:
    """Ouvre une connexion à partir de DATABASE_URL (ou de l'URL fournie)."""
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL n'est pas définie (voir .env.example)")
    return psycopg.connect(url, autocommit=True)


def apply_schema(conn: psycopg.Connection, schema_path: Path = SCHEMA_PATH) -> None:
    """Exécute le script de schéma (idempotent)."""
    with conn.transaction():
        conn.execute(schema_path.read_text(encoding="utf-8"))


def reset_tables(conn: psycopg.Connection) -> None:
    """Vide toutes les tables (démos et tests uniquement)."""
    conn.execute(f"TRUNCATE {', '.join(_ALL_TABLES)} RESTART IDENTITY CASCADE")


def is_processed(conn: psycopg.Connection, content_hash: str) -> bool:
    """Lecture seule : ce contenu figure-t-il déjà dans processed_files ?

    Optimisation uniquement (éviter un chargement coûteux, OCR ou SLM) : la
    garantie anti-doublon reste l'INSERT ... ON CONFLICT de insert_file_results.
    """
    row = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM processed_files WHERE content_hash = %s)", (content_hash,)
    ).fetchone()
    return bool(row[0])


def _to_decimal(value: float) -> Decimal:
    # str() donne la représentation la plus courte du float (741.58 et non 741.579999...).
    return Decimal(str(value)).quantize(_CENTS)


def _upsert_totals(cur: psycopg.Cursor, kind: str, sums: pd.DataFrame) -> None:
    table, db_key, df_key = _TOTAL_TABLES[kind]
    # Cumul entre fichiers délégué à la base.
    query = f"""
        INSERT INTO {table} ({db_key}, total) VALUES (%s, %s)
        ON CONFLICT ({db_key}) DO UPDATE SET total = {table}.total + EXCLUDED.total
    """
    cur.executemany(
        query,
        [
            (key, _to_decimal(total))
            for key, total in zip(sums[df_key], sums[_TOTAL_COLUMN], strict=True)
        ],
    )


def insert_file_results(
    conn: psycopg.Connection,
    filename: str,
    content_hash: str,
    transactions_flaggees: pd.DataFrame,
    sommes_iban_origine: pd.DataFrame,
    sommes_banque: pd.DataFrame,
    sommes_iban_destinataire: pd.DataFrame,
    fault_hook: Callable[[str], None] | None = None,
) -> bool:
    """Insère les résultats d'un fichier dans une seule transaction SQL.

    Renvoie False si un fichier de même contenu a déjà été traité (rien n'est
    inséré), True sinon. En cas d'erreur, rollback complet puis l'exception
    est propagée.

    fault_hook (simulation de pannes, voir fault_injection.py) est appelé avec
    "mid" avant les totaux et "after_commit" juste après le commit.
    """
    # Sinon conn.transaction() ouvrirait un savepoint dans une transaction
    # externe, et le commit ne dépendrait plus de cette fonction.
    if conn.info.transaction_status != pq.TransactionStatus.IDLE:
        raise RuntimeError("insert_file_results exige une connexion sans transaction en cours")

    with conn.transaction(), conn.cursor() as cur:
        cur.execute(_INSERT_FILE, (filename, content_hash))
        row = cur.fetchone()
        if row is None:
            return False
        file_id = row[0]

        cur.executemany(
            _INSERT_TRANSACTION,
            [
                (
                    file_id,
                    t.datetime_transaction.to_pydatetime(),
                    t.iban_origine,
                    t.pays_source,
                    t.banque_source,
                    t.iban_destinataire,
                    t.pays_destinataire,
                    _to_decimal(t.montant),
                    bool(getattr(t, _FLAG_COLUMN)),
                )
                for t in transactions_flaggees.itertuples(index=False)
            ],
        )

        if fault_hook:
            fault_hook("mid")
        _upsert_totals(cur, "sent_by_iban", sommes_iban_origine)
        _upsert_totals(cur, "sent_by_bank", sommes_banque)
        _upsert_totals(cur, "received_by_iban", sommes_iban_destinataire)

    if fault_hook:
        fault_hook("after_commit")
    return True


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    command = args[0] if args else "init"
    with get_connection() as conn:
        if command == "init":
            apply_schema(conn)
            print(f"Schéma appliqué : {SCHEMA_PATH.name}")
        elif command == "reset":
            reset_tables(conn)
            print(f"Tables vidées : {', '.join(_ALL_TABLES)}")
        else:
            sys.exit(f"Commande inconnue : {command} (attendu : init ou reset)")


if __name__ == "__main__":
    main()
