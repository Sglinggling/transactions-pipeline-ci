"""Accès au stockage objet compatible S3 (module d'I/O).

Configuration par variables d'environnement : S3_ENDPOINT_URL, S3_ACCESS_KEY,
S3_SECRET_KEY, S3_BUCKET, S3_PREFIX, et S3_CONNECT_TIMEOUT / S3_READ_TIMEOUT.

Les retries internes de botocore sont désactivés (une seule tentative par
appel) : seul pipeline.retry retente, pour que chaque échec soit loggé. Les
erreurs du listing et du téléchargement sont classées en
TemporaryStorageError (seule retentée) ou PermanentStorageError.
"""

import argparse
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    HTTPClientError,
    IncompleteReadError,
)
from botocore.exceptions import ConnectionError as BotoConnectionError

DEFAULT_SOURCE_DIR = Path("data/incoming")
# Types d'objets traités par la pipeline : CSV, images d'avis de virement,
# textes libres (SLM).
SOURCE_EXTENSIONS = (".csv", ".png", ".txt")
_ALREADY_EXISTS = {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}
DEFAULT_CONNECT_TIMEOUT = 2.0  # secondes
DEFAULT_READ_TIMEOUT = 5.0  # secondes

# Erreurs réseau botocore considérées comme temporaires : connexion refusée
# ou en timeout (ConnectionError), timeout de lecture / connexion coupée
# (HTTPClientError), corps de réponse tronqué (IncompleteReadError).
_TEMPORARY_BOTO_ERRORS = (BotoConnectionError, HTTPClientError, IncompleteReadError)
# Codes S3 temporaires même si le statut HTTP n'est pas 5xx.
_TEMPORARY_CODES = {"SlowDown", "ServiceUnavailable", "InternalError", "RequestTimeout"}


class StorageVerificationError(Exception):
    """Les objets du bucket ne correspondent pas aux fichiers locaux."""


class StorageError(Exception):
    """Erreur d'accès au stockage S3, déjà classée."""


class TemporaryStorageError(StorageError):
    """Panne passagère (réseau, 5xx, SlowDown) : la seule erreur S3 retentée."""


class PermanentStorageError(StorageError):
    """Erreur définitive (NoSuchKey, NoSuchBucket, AccessDenied, 4xx...) : jamais retentée."""


def is_temporary(exc: BaseException) -> bool:
    """True si l'erreur botocore est passagère et mérite une nouvelle tentative."""
    if isinstance(exc, ClientError):
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        return status >= 500 or exc.response.get("Error", {}).get("Code") in _TEMPORARY_CODES
    return isinstance(exc, _TEMPORARY_BOTO_ERRORS)


def _describe(exc: BaseException) -> str:
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", "?")
        return f"{error.get('Code', '?')} (HTTP {status}) : {error.get('Message', '')}".rstrip(" :")
    return f"{type(exc).__name__} : {exc}"


@contextmanager
def classify_errors(operation: str) -> Iterator[None]:
    """Traduit les erreurs botocore en TemporaryStorageError / PermanentStorageError."""
    try:
        yield
    except (BotoCoreError, ClientError) as exc:
        error_class = TemporaryStorageError if is_temporary(exc) else PermanentStorageError
        raise error_class(f"{operation} : {_describe(exc)}") from exc


@dataclass(frozen=True)
class S3Settings:
    endpoint_url: str
    access_key: str
    secret_key: str
    bucket: str
    prefix: str
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    read_timeout: float = DEFAULT_READ_TIMEOUT

    @classmethod
    def from_env(cls) -> "S3Settings":
        names = ["S3_ENDPOINT_URL", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET"]
        missing = [n for n in names if not os.environ.get(n)]
        if missing:
            raise RuntimeError(
                f"variable(s) non définie(s) : {', '.join(missing)} (voir .env.example)"
            )
        return cls(
            endpoint_url=os.environ["S3_ENDPOINT_URL"],
            access_key=os.environ["S3_ACCESS_KEY"],
            secret_key=os.environ["S3_SECRET_KEY"],
            bucket=os.environ["S3_BUCKET"],
            prefix=os.environ.get("S3_PREFIX", ""),
            connect_timeout=_env_float("S3_CONNECT_TIMEOUT", DEFAULT_CONNECT_TIMEOUT),
            read_timeout=_env_float("S3_READ_TIMEOUT", DEFAULT_READ_TIMEOUT),
        )

    @property
    def location(self) -> str:
        return f"s3://{self.bucket}/{self.prefix}"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} doit être un nombre de secondes, pas '{raw}'") from exc


def get_client(settings: S3Settings) -> BaseClient:
    return boto3.client(
        "s3",
        endpoint_url=settings.endpoint_url,
        aws_access_key_id=settings.access_key,
        aws_secret_access_key=settings.secret_key,
        region_name="us-east-1",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            # Une seule tentative par appel : les retries sont gérés par pipeline.retry.
            retries={"total_max_attempts": 1, "mode": "standard"},
            connect_timeout=settings.connect_timeout,
            read_timeout=settings.read_timeout,
        ),
    )


def ensure_bucket(client: BaseClient, bucket: str) -> bool:
    """Crée le bucket s'il n'existe pas ; renvoie True s'il vient d'être créé."""
    # HeadBucket d'abord : en us-east-1 (et sur RustFS), CreateBucket sur un
    # bucket déjà possédé répond 200 sans erreur, on ne saurait pas s'il existait.
    try:
        client.head_bucket(Bucket=bucket)
        return False
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in {"404", "NoSuchBucket", "NotFound"}:
            raise
    try:
        client.create_bucket(Bucket=bucket)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in _ALREADY_EXISTS:
            return False
        raise
    return True


def _local_source_files(source_dir: Path) -> list[Path]:
    files = [p for p in source_dir.iterdir() if p.suffix in SOURCE_EXTENSIONS]
    return sorted(files, key=lambda p: p.name)


def upload_directory(
    client: BaseClient, bucket: str, prefix: str, source_dir: Path
) -> list[tuple[str, int]]:
    """Envoie les CSV, PNG et TXT de source_dir sous le préfixe ; renvoie (clé, taille) envoyés."""
    uploaded = []
    for path in _local_source_files(source_dir):
        content = path.read_bytes()
        key = f"{prefix}{path.name}"
        client.put_object(Bucket=bucket, Key=key, Body=content)
        uploaded.append((key, len(content)))
    return uploaded


def list_objects(client: BaseClient, bucket: str, prefix: str) -> dict[str, int]:
    """Objets du préfixe : {clé: taille en octets}. Lève TemporaryStorageError ou
    PermanentStorageError."""
    objects: dict[str, int] = {}
    with classify_errors(f"listing de s3://{bucket}/{prefix}"):
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                objects[obj["Key"]] = obj["Size"]
    return objects


def list_keys(client: BaseClient, bucket: str, prefix: str) -> list[str]:
    """Clés du préfixe, triées."""
    return sorted(list_objects(client, bucket, prefix))


def download_object(
    client: BaseClient,
    bucket: str,
    key: str,
    fault_hook: Callable[[str], None] | None = None,
) -> bytes:
    """Télécharge un objet en mémoire (point unique de téléchargement, retenté par run.py).

    Lève TemporaryStorageError ou PermanentStorageError. fault_hook (simulation
    de pannes, voir fault_injection.py) est appelé avec "download" avant l'appel S3.
    """
    with classify_errors(f"téléchargement de s3://{bucket}/{key}"):
        if fault_hook:
            fault_hook("download")
        return client.get_object(Bucket=bucket, Key=key)["Body"].read()


def compare_listings(local: dict[str, int], remote: dict[str, int]) -> list[str]:
    """Écarts entre fichiers locaux et objets distants ({nom: taille}), triés par nom."""
    problems = []
    for name, size in sorted(local.items()):
        if name not in remote:
            problems.append(f"{name} : absent du bucket")
        elif remote[name] != size:
            problems.append(f"{name} : taille différente (local {size} o, bucket {remote[name]} o)")
    return problems


def verify_upload(client: BaseClient, bucket: str, prefix: str, source_dir: Path) -> int:
    """Vérifie que chaque CSV / PNG / TXT local est présent dans le bucket avec la même taille.

    Renvoie le nombre de fichiers vérifiés ; lève StorageVerificationError sinon.
    """
    local = {p.name: p.stat().st_size for p in _local_source_files(source_dir)}
    if not local:
        raise StorageVerificationError(f"aucun fichier CSV, PNG ou TXT local dans {source_dir}")
    remote = {
        key.removeprefix(prefix): size for key, size in list_objects(client, bucket, prefix).items()
    }
    problems = compare_listings(local, remote)
    if problems:
        raise StorageVerificationError(
            f"{len(problems)} fichier(s) manquant(s) ou différent(s) dans s3://{bucket}/{prefix} : "
            + " ; ".join(problems)
        )
    return len(local)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Opérations sur le stockage S3.")
    parser.add_argument("command", choices=["init", "upload", "verify"])
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    args = parser.parse_args(argv)

    try:
        settings = S3Settings.from_env()
    except RuntimeError as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 1
    client = get_client(settings)

    if args.command == "init":
        created = ensure_bucket(client, settings.bucket)
        print(f"Bucket {settings.bucket} : {'créé' if created else 'déjà présent'}")
    elif args.command == "upload":
        uploaded = upload_directory(client, settings.bucket, settings.prefix, args.source_dir)
        for key, size in uploaded:
            print(f"Envoyé : s3://{settings.bucket}/{key} ({size} octets)")
        print(f"{len(uploaded)} fichier(s) envoyé(s) depuis {args.source_dir}")
    else:
        try:
            count = verify_upload(client, settings.bucket, settings.prefix, args.source_dir)
        except (StorageVerificationError, StorageError) as exc:
            print(f"ERREUR : {exc}", file=sys.stderr)
            return 1
        print(
            f"Vérification OK : {count} fichier(s) présents dans {settings.location}, "
            "tailles identiques"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
