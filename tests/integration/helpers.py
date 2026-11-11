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
