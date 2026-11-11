"""Accès S3 contre le vrai service (RustFS)."""

import subprocess
import sys

import pytest

from pipeline.storage import (
    StorageVerificationError,
    download_object,
    ensure_bucket,
    list_keys,
    upload_directory,
    verify_upload,
)

from .helpers import TEST_BUCKET, TEST_PREFIX, subprocess_env

pytestmark = pytest.mark.integration

ENSURE_BUCKET = "pipeline-tests-ensure"


@pytest.fixture
def fresh_bucket(s3_client):
    """Nom d'un bucket inexistant, supprimé après le test."""

    def drop():
        if ENSURE_BUCKET in {b["Name"] for b in s3_client.list_buckets()["Buckets"]}:
            s3_client.delete_bucket(Bucket=ENSURE_BUCKET)

    drop()
    yield ENSURE_BUCKET
    drop()


def verify_cli(source_dir):
    return subprocess.run(
        [sys.executable, "-m", "pipeline.storage", "verify", "--source-dir", str(source_dir)],
        capture_output=True,
        text=True,
        check=False,
        env=subprocess_env(),
    )


def test_ensure_bucket_is_idempotent(s3_client, fresh_bucket):
    assert ensure_bucket(s3_client, fresh_bucket) is True
    assert ensure_bucket(s3_client, fresh_bucket) is False


def test_upload_list_download_roundtrip(s3, incoming):
    uploaded = upload_directory(s3, TEST_BUCKET, TEST_PREFIX, incoming)

    expected_keys = [
        "incoming/transactions_001.csv",
        "incoming/transactions_002.csv",
        "incoming/transactions_003.csv",
    ]
    assert [key for key, _ in uploaded] == expected_keys
    assert list_keys(s3, TEST_BUCKET, TEST_PREFIX) == expected_keys
    for key in expected_keys:
        local = (incoming / key.removeprefix(TEST_PREFIX)).read_bytes()
        assert download_object(s3, TEST_BUCKET, key) == local


def test_list_keys_is_sorted_and_limited_to_prefix(s3):
    for key in ["incoming/b.csv", "incoming/a.csv", "autre/c.csv", "incoming/sous/d.csv"]:
        s3.put_object(Bucket=TEST_BUCKET, Key=key, Body=b"x")

    assert list_keys(s3, TEST_BUCKET, TEST_PREFIX) == [
        "incoming/a.csv",
        "incoming/b.csv",
        "incoming/sous/d.csv",
    ]


def test_verify_ok(s3_incoming, incoming):
    assert verify_upload(s3_incoming, TEST_BUCKET, TEST_PREFIX, incoming) == 3

    result = verify_cli(incoming)
    assert result.returncode == 0, result.stderr
    assert "Vérification OK : 3 fichier(s) présents dans s3://pipeline-tests/incoming/" in (
        result.stdout
    )


def test_verify_fails_when_object_missing(s3_incoming, incoming):
    s3_incoming.delete_object(Bucket=TEST_BUCKET, Key="incoming/transactions_002.csv")

    with pytest.raises(StorageVerificationError, match="transactions_002.csv : absent du bucket"):
        verify_upload(s3_incoming, TEST_BUCKET, TEST_PREFIX, incoming)

    result = verify_cli(incoming)
    assert result.returncode != 0
    assert (
        "ERREUR : 1 fichier(s) manquant(s) ou différent(s) dans s3://pipeline-tests/incoming/ : "
        "transactions_002.csv : absent du bucket"
    ) in result.stderr


def test_verify_fails_when_size_differs(s3_incoming, incoming):
    s3_incoming.put_object(Bucket=TEST_BUCKET, Key="incoming/transactions_001.csv", Body=b"tronque")
    size = (incoming / "transactions_001.csv").stat().st_size

    with pytest.raises(
        StorageVerificationError,
        match=rf"transactions_001.csv : taille différente \(local {size} o, bucket 7 o\)",
    ):
        verify_upload(s3_incoming, TEST_BUCKET, TEST_PREFIX, incoming)
