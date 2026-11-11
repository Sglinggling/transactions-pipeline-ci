import pytest
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    ReadTimeoutError,
)

from pipeline.storage import (
    PermanentStorageError,
    S3Settings,
    TemporaryStorageError,
    compare_listings,
    download_object,
    get_client,
    is_temporary,
    list_keys,
)


def test_compare_listings_identical():
    assert compare_listings({"a.csv": 10, "b.csv": 20}, {"a.csv": 10, "b.csv": 20}) == []


def test_compare_listings_extra_remote_objects_are_allowed():
    assert compare_listings({"a.csv": 10}, {"a.csv": 10, "ancien.csv": 5}) == []


def test_compare_listings_missing_and_different():
    local = {"b.csv": 20, "a.csv": 10, "c.csv": 30}
    remote = {"a.csv": 11, "c.csv": 30}
    assert compare_listings(local, remote) == [
        "a.csv : taille différente (local 10 o, bucket 11 o)",
        "b.csv : absent du bucket",
    ]


def test_settings_from_env(monkeypatch):
    monkeypatch.delenv("S3_CONNECT_TIMEOUT", raising=False)
    monkeypatch.delenv("S3_READ_TIMEOUT", raising=False)
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://localhost:9000")
    monkeypatch.setenv("S3_ACCESS_KEY", "k")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    monkeypatch.setenv("S3_BUCKET", "b")
    monkeypatch.setenv("S3_PREFIX", "p/")
    settings = S3Settings.from_env()
    assert settings == S3Settings("http://localhost:9000", "k", "s", "b", "p/")
    assert settings.location == "s3://b/p/"


def test_settings_from_env_missing(monkeypatch):
    for name in ["S3_ENDPOINT_URL", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET"]:
        monkeypatch.delenv(name, raising=False)
    expected = "S3_ENDPOINT_URL, S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET"
    with pytest.raises(RuntimeError, match=expected):
        S3Settings.from_env()


# --- Classification des erreurs ---


def client_error(code, status, operation="GetObject"):
    return ClientError(
        {"Error": {"Code": code, "Message": "msg"}, "ResponseMetadata": {"HTTPStatusCode": status}},
        operation,
    )


def error_id(exc):
    if isinstance(exc, ClientError):
        status = exc.response["ResponseMetadata"]["HTTPStatusCode"]
        return f"{exc.response['Error']['Code']}-{status}"
    return type(exc).__name__


@pytest.mark.parametrize(
    "exc",
    [
        EndpointConnectionError(endpoint_url="http://s3"),  # connexion refusée
        ConnectTimeoutError(endpoint_url="http://s3"),  # timeout de connexion
        ReadTimeoutError(endpoint_url="http://s3"),  # timeout de lecture
        ConnectionClosedError(endpoint_url="http://s3"),  # connexion coupée
        client_error("InternalError", 500),
        client_error("ServiceUnavailable", 503),
        client_error("SlowDown", 503),
        client_error("SlowDown", 400),  # SlowDown temporaire quel que soit le statut
    ],
    ids=error_id,
)
def test_temporary_errors(exc):
    assert is_temporary(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        client_error("NoSuchKey", 404),
        client_error("NoSuchBucket", 404),
        client_error("AccessDenied", 403),
        client_error("InvalidAccessKeyId", 403),
        client_error("InvalidRequest", 400),
        NoCredentialsError(),
        ValueError("pas une erreur S3"),
    ],
    ids=error_id,
)
def test_permanent_errors(exc):
    assert is_temporary(exc) is False


class FakeClient:
    """Client S3 dont get_object / get_paginator lèvent l'erreur donnée."""

    def __init__(self, error):
        self.error = error

    def get_object(self, **kwargs):
        raise self.error

    def get_paginator(self, name):
        raise self.error


def test_download_translates_temporary_error():
    with pytest.raises(TemporaryStorageError) as exc:
        download_object(FakeClient(EndpointConnectionError(endpoint_url="http://s3")), "b", "k.csv")
    assert str(exc.value).startswith(
        "téléchargement de s3://b/k.csv : EndpointConnectionError : Could not connect"
    )
    assert isinstance(exc.value.__cause__, EndpointConnectionError)


def test_download_translates_permanent_error():
    with pytest.raises(PermanentStorageError) as exc:
        download_object(FakeClient(client_error("NoSuchKey", 404)), "b", "k.csv")
    assert str(exc.value) == "téléchargement de s3://b/k.csv : NoSuchKey (HTTP 404) : msg"


def test_listing_translates_errors():
    with pytest.raises(TemporaryStorageError, match="listing de s3://b/p/ : SlowDown"):
        list_keys(FakeClient(client_error("SlowDown", 503, "ListObjectsV2")), "b", "p/")
    with pytest.raises(PermanentStorageError, match="listing de s3://b/p/ : NoSuchBucket"):
        list_keys(FakeClient(client_error("NoSuchBucket", 404, "ListObjectsV2")), "b", "p/")


def test_download_fault_hook_goes_through_classification():
    def hook(point):
        assert point == "download"
        raise ReadTimeoutError(endpoint_url="http://s3")

    with pytest.raises(TemporaryStorageError, match="ReadTimeoutError"):
        download_object(FakeClient(AssertionError("ne doit pas être appelé")), "b", "k", hook)


def test_client_has_no_internal_retries_and_short_timeouts():
    settings = S3Settings("http://localhost:9000", "k", "s", "b", "p/", 1.5, 3.0)
    config = get_client(settings).meta.config
    assert config.retries == {"total_max_attempts": 1, "mode": "standard"}
    assert (config.connect_timeout, config.read_timeout) == (1.5, 3.0)


def test_timeouts_from_env(monkeypatch):
    for name, value in [
        ("S3_ENDPOINT_URL", "http://x"),
        ("S3_ACCESS_KEY", "k"),
        ("S3_SECRET_KEY", "s"),
        ("S3_BUCKET", "b"),
        ("S3_CONNECT_TIMEOUT", "0.5"),
        ("S3_READ_TIMEOUT", "7"),
    ]:
        monkeypatch.setenv(name, value)
    settings = S3Settings.from_env()
    assert (settings.connect_timeout, settings.read_timeout) == (0.5, 7.0)

    monkeypatch.setenv("S3_READ_TIMEOUT", "long")
    with pytest.raises(RuntimeError, match="S3_READ_TIMEOUT"):
        S3Settings.from_env()
