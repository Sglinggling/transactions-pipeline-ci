import psycopg
import pytest

from pipeline.db import apply_schema, get_connection, reset_tables


@pytest.fixture(scope="session")
def db_conn():
    try:
        conn = get_connection()
    except psycopg.OperationalError as exc:
        pytest.fail(f"Base injoignable, lancer `make db-up db-init` : {exc}")
    apply_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def conn(db_conn):
    """Connexion sur une base vidée avant chaque test."""
    reset_tables(db_conn)
    return db_conn
