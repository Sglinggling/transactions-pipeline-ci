import psycopg
import pytest
from botocore.exceptions import BotoCoreError, ClientError

from pipeline.db import apply_schema, get_connection, reset_tables
from pipeline.generate import write_csv_files
from pipeline.generate_images import write_images
from pipeline.generate_texts import write_texts
from pipeline.storage import S3Settings, ensure_bucket, get_client, upload_directory

from .helpers import TEST_BUCKET, TEST_PREFIX


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


@pytest.fixture(scope="session")
def s3_client():
    try:
        client = get_client(S3Settings.from_env())
    except RuntimeError as exc:
        pytest.fail(f"{exc} : lancer les tests via `make test-integration`")
    try:
        ensure_bucket(client, TEST_BUCKET)
    except (BotoCoreError, ClientError) as exc:
        pytest.fail(f"S3 injoignable, lancer `make s3-up` : {exc}")
    return client


def empty_bucket(client, bucket):
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            client.delete_object(Bucket=bucket, Key=obj["Key"])


@pytest.fixture
def s3(s3_client):
    """Client S3 sur le bucket de test, vidé avant et après chaque test."""
    empty_bucket(s3_client, TEST_BUCKET)
    yield s3_client
    empty_bucket(s3_client, TEST_BUCKET)


@pytest.fixture
def incoming(tmp_path):
    """Dossier local (zone de dépôt) contenant les 3 CSV du générateur (seed 42)."""
    source = tmp_path / "incoming"
    write_csv_files(source, n_files=3, seed=42)
    return source


@pytest.fixture
def s3_incoming(s3, incoming):
    """Les 3 CSV du générateur déposés dans le bucket de test, sous TEST_PREFIX."""
    upload_directory(s3, TEST_BUCKET, TEST_PREFIX, incoming)
    return s3


@pytest.fixture
def incoming_mixed(incoming):
    """Zone de dépôt locale : les 3 CSV et les 3 images d'avis de virement."""
    write_images(incoming)
    return incoming


@pytest.fixture
def s3_mixed(s3, incoming_mixed):
    """Les 3 CSV et les 3 images déposés dans le bucket de test, sous TEST_PREFIX."""
    upload_directory(s3, TEST_BUCKET, TEST_PREFIX, incoming_mixed)
    return s3


@pytest.fixture
def incoming_all(incoming_mixed):
    """Zone de dépôt locale : 3 CSV, 3 images et 4 textes."""
    write_texts(incoming_mixed)
    return incoming_mixed


@pytest.fixture
def s3_all(s3, incoming_all):
    """Les trois sources déposées dans le bucket de test, sous TEST_PREFIX."""
    upload_directory(s3, TEST_BUCKET, TEST_PREFIX, incoming_all)
    return s3
