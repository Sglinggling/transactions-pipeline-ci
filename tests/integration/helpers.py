import os

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
