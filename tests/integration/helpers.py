import os
import subprocess
import sys
from decimal import Decimal

# Bucket et préfixe dédiés aux tests, vidés par fixture avant chaque test.
TEST_BUCKET = "pipeline-tests"
TEST_PREFIX = "incoming/"

TABLES = (
    "transactions",
    "processed_files",
    "total_sent_by_iban",
    "total_sent_by_bank",
    "total_received_by_iban",
)


def fetch(conn, query, params=None):
    return conn.execute(query, params).fetchall()


def totals(conn):
    return {
        "sent_by_iban": fetch(conn, "SELECT iban, total FROM total_sent_by_iban ORDER BY iban"),
        "sent_by_bank": fetch(conn, "SELECT banque, total FROM total_sent_by_bank ORDER BY banque"),
        "received_by_iban": fetch(
            conn, "SELECT iban, total FROM total_received_by_iban ORDER BY iban"
        ),
    }


def row_counts(conn):
    return {t: fetch(conn, f"SELECT count(*) FROM {t}")[0][0] for t in TABLES}


# Valeurs attendues pour write_csv_files(n_files=3, seed=42), calculées une
# fois indépendamment du code testé (module csv + Decimal) et figées ici.
EXPECTED_TRANSACTIONS = 22
# processed_files.filename contient la clé S3.
EXPECTED_FILES = [
    "incoming/transactions_001.csv",
    "incoming/transactions_002.csv",
    "incoming/transactions_003.csv",
]
EXPECTED_FLAGGED = 2

EXPECTED_TOTALS = {
    "sent_by_iban": [
        ("BE68539007547034", Decimal("4791.05")),
        ("DE89370400440532013000", Decimal("19969.02")),
        ("ES9121000418450200051332", Decimal("4484.58")),
        ("FR7630003000010000000000303", Decimal("3358.92")),
        ("FR7630004000010000000000101", Decimal("22969.24")),
        ("FR7630004000010000000000202", Decimal("14174.91")),
        ("IT60X0542811101000000123456", Decimal("3670.26")),
    ],
    "sent_by_bank": [
        ("BNP Paribas", Decimal("37144.15")),
        ("Deutsche Bank", Decimal("19969.02")),
        ("ING Belgique", Decimal("4791.05")),
        ("Santander", Decimal("4484.58")),
        ("Societe Generale", Decimal("3358.92")),
        ("UniCredit", Decimal("3670.26")),
    ],
    "received_by_iban": [
        ("BE68539007547034", Decimal("20403.75")),
        ("DE89370400440532013000", Decimal("3670.26")),
        ("ES9121000418450200051332", Decimal("15839.48")),
        ("FR7630003000010000000000303", Decimal("3099.44")),
        ("FR7630004000010000000000101", Decimal("14381.33")),
        ("FR7630004000010000000000202", Decimal("2861.67")),
        ("IT60X0542811101000000123456", Decimal("13162.05")),
    ],
}


# Flux mixte : 3 CSV + 3 images (generate_images.IMAGE_TRANSACTIONS), valeurs
# calculées une fois indépendamment du code testé (csv + Decimal, virements
# des images recopiés à la main) et figées ici.
EXPECTED_MIXED_TRANSACTIONS = 25
EXPECTED_MIXED_FLAGGED = 3  # 12500.00 et 5000.01 (CSV), 7350.50 (image)
EXPECTED_MIXED_FILES = EXPECTED_FILES + [
    "incoming/virement_001.png",
    "incoming/virement_002.png",
    "incoming/virement_003.png",
]
EXPECTED_MIXED_TOTALS = {
    "sent_by_iban": [
        ("BE68539007547034", Decimal("6041.80")),
        ("DE89370400440532013000", Decimal("19969.02")),
        ("ES9121000418450200051332", Decimal("9484.58")),
        ("FR7630003000010000000000303", Decimal("3358.92")),
        ("FR7630004000010000000000101", Decimal("30319.74")),
        ("FR7630004000010000000000202", Decimal("14174.91")),
        ("IT60X0542811101000000123456", Decimal("3670.26")),
    ],
    "sent_by_bank": [
        ("BNP Paribas", Decimal("44494.65")),
        ("Deutsche Bank", Decimal("19969.02")),
        ("ING Belgique", Decimal("6041.80")),
        ("Santander", Decimal("9484.58")),
        ("Societe Generale", Decimal("3358.92")),
        ("UniCredit", Decimal("3670.26")),
    ],
    "received_by_iban": [
        ("BE68539007547034", Decimal("20403.75")),
        ("DE89370400440532013000", Decimal("11020.76")),
        ("ES9121000418450200051332", Decimal("15839.48")),
        ("FR7630003000010000000000303", Decimal("3099.44")),
        ("FR7630004000010000000000101", Decimal("14381.33")),
        ("FR7630004000010000000000202", Decimal("7861.67")),
        ("IT60X0542811101000000123456", Decimal("14412.80")),
    ],
}


# Flux complet : 3 CSV + 3 images + 4 textes (generate_texts.TEXTS), valeurs
# calculées une fois indépendamment du code testé (csv + Decimal, virements
# des images et des textes recopiés à la main) et figées ici.
EXPECTED_ALL_TRANSACTIONS = 29
EXPECTED_ALL_FLAGGED = 5  # CSV : 12500.00, 5000.01 ; image : 7350.50 ; textes : 6200.00, 7300.50
EXPECTED_ALL_FILES = [  # ordre des clés : message_* < transactions_* < virement_*
    "incoming/message_001.txt",
    "incoming/message_002.txt",
    "incoming/message_003.txt",
    "incoming/message_004.txt",
    *EXPECTED_MIXED_FILES,
]
EXPECTED_ALL_TOTALS = {
    "sent_by_iban": [
        ("BE68539007547034", Decimal("11041.80")),
        ("DE89370400440532013000", Decimal("27269.52")),
        ("ES9121000418450200051332", Decimal("11359.98")),
        ("FR7630003000010000000000303", Decimal("3358.92")),
        ("FR7630004000010000000000101", Decimal("36519.74")),
        ("FR7630004000010000000000202", Decimal("14174.91")),
        ("IT60X0542811101000000123456", Decimal("3670.26")),
    ],
    "sent_by_bank": [
        ("BNP Paribas", Decimal("50694.65")),
        ("Deutsche Bank", Decimal("27269.52")),
        ("ING Belgique", Decimal("11041.80")),
        ("Santander", Decimal("11359.98")),
        ("Societe Generale", Decimal("3358.92")),
        ("UniCredit", Decimal("3670.26")),
    ],
    "received_by_iban": [
        ("BE68539007547034", Decimal("20403.75")),
        ("DE89370400440532013000", Decimal("17220.76")),
        ("ES9121000418450200051332", Decimal("15839.48")),
        ("FR7630003000010000000000303", Decimal("10399.94")),
        ("FR7630004000010000000000101", Decimal("14381.33")),
        ("FR7630004000010000000000202", Decimal("12861.67")),
        ("IT60X0542811101000000123456", Decimal("16288.20")),
    ],
}


def subprocess_env(**overrides):
    """Environnement d'un sous-processus : bucket de test, sans simulation héritée."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("SIMULATE_", "DB_MAX_ATTEMPTS", "DB_RETRY_", "PIPELINE_EXTENSIONS"))
    }
    env.update(S3_BUCKET=TEST_BUCKET, S3_PREFIX=TEST_PREFIX)
    env.update({k: str(v) for k, v in overrides.items()})
    return env


def run_pipeline(**env_overrides):
    """Lance la vraie commande, comme `make run`, sur le bucket de test.

    Les variables de simulation / retry héritées sont ignorées ; seules
    celles passées en argument s'appliquent.
    """
    return subprocess.run(
        [sys.executable, "-m", "pipeline.run"],
        capture_output=True,
        text=True,
        check=False,
        env=subprocess_env(**env_overrides),
    )


def count_duplicate_transactions(conn):
    """Nombre de transactions présentes plus d'une fois (hors id / file_id)."""
    return fetch(
        conn,
        """
        SELECT count(*) FROM (
            SELECT 1 FROM transactions
            GROUP BY datetime_transaction, iban_origine, iban_destinataire, montant
            HAVING count(*) > 1
        ) d
        """,
    )[0][0]
