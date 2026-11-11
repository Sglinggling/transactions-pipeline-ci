"""Orchestrateur : traite les CSV du bucket S3 et les insère en base.

Pour chaque objet .csv, .png ou .txt du préfixe, par ordre de clé :
téléchargement en mémoire -> hash SHA-256 du contenu -> chargement
(.csv : parsing CSV ; .png : OCR -> extraction ; .txt : SLM -> extraction ->
garde-fou) -> validation commune -> traitements purs -> insert_file_results.
Aucun fichier local n'est lu.
Retries (backoff exponentiel) : listing et téléchargements S3 sur
TemporaryStorageError (S3_*), appel au SLM sur TemporarySlmError (SLM_*),
insertion (connexion neuve + insert_file_results) sur
psycopg.OperationalError (DB_*).
Le premier objet en erreur arrête la pipeline (code de sortie 1) ; les
objets traités avant lui restent en base, rien n'est écrit pour lui.
"""

import hashlib
import logging
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd
import psycopg
from botocore.client import BaseClient

from pipeline.db import get_connection, insert_file_results, is_processed
from pipeline.fault_injection import DbFaultInjector, S3FaultInjector
from pipeline.loading import (
    TransactionValidationError,
    decode_text,
    load_ocr_transaction,
    load_slm_transaction,
    load_transactions,
)
from pipeline.ocr import OcrError, image_to_text
from pipeline.processing import (
    HIGH_AMOUNT_COLUMN,
    flag_high_amount,
    sum_by_banque_source,
    sum_by_iban_destinataire,
    sum_by_iban_origine,
)
from pipeline.retry import RetryExhaustedError, RetryPolicy, retry_call
from pipeline.slm import SlmError, SlmSettings, TemporarySlmError, chat_json
from pipeline.storage import (
    SOURCE_EXTENSIONS,
    S3Settings,
    StorageError,
    TemporaryStorageError,
    download_object,
    get_client,
    list_keys,
)
from pipeline.text_extraction import RESPONSE_SCHEMA, build_messages

DEFAULT_DB_MAX_ATTEMPTS = 4
DEFAULT_DB_RETRY_DELAY = 1.0  # secondes, doublé à chaque échec
DEFAULT_S3_MAX_ATTEMPTS = 4
DEFAULT_S3_RETRY_DELAY = 1.0  # secondes, doublé à chaque échec
DEFAULT_SLM_MAX_ATTEMPTS = 4
DEFAULT_SLM_RETRY_DELAY = 2.0  # secondes, doublé à chaque échec (redémarrage d'Ollama)

# Erreurs qui arrêtent proprement la pipeline (ligne ERREUR sans traceback, code 1).
_STOPPING_ERRORS = (
    TransactionValidationError,
    OcrError,
    RetryExhaustedError,
    psycopg.Error,
    StorageError,
    SlmError,
)

logger = logging.getLogger("pipeline")


@dataclass
class RunSummary:
    processed: int = 0
    skipped: int = 0
    failed_file: str | None = None
    not_examined: int = 0

    @property
    def ok(self) -> bool:
        return self.failed_file is None


def db_retry_policy() -> RetryPolicy:
    """Politique de retry de l'insertion : DB_MAX_ATTEMPTS, DB_RETRY_DELAY, backoff x2."""
    return RetryPolicy.from_env(
        "DB",
        retry_on=(psycopg.OperationalError,),
        default_max_attempts=DEFAULT_DB_MAX_ATTEMPTS,
        default_delay=DEFAULT_DB_RETRY_DELAY,
    )


def s3_retry_policy() -> RetryPolicy:
    """Politique de retry du listing et des téléchargements : S3_MAX_ATTEMPTS,
    S3_RETRY_DELAY, backoff x2. Seule TemporaryStorageError est retentée."""
    return RetryPolicy.from_env(
        "S3",
        retry_on=(TemporaryStorageError,),
        default_max_attempts=DEFAULT_S3_MAX_ATTEMPTS,
        default_delay=DEFAULT_S3_RETRY_DELAY,
    )


def slm_retry_policy() -> RetryPolicy:
    """Politique de retry de l'appel au SLM : SLM_MAX_ATTEMPTS, SLM_RETRY_DELAY,
    backoff x2. Seule TemporarySlmError est retentée."""
    return RetryPolicy.from_env(
        "SLM",
        retry_on=(TemporarySlmError,),
        default_max_attempts=DEFAULT_SLM_MAX_ATTEMPTS,
        default_delay=DEFAULT_SLM_RETRY_DELAY,
    )


EXTENSIONS_ENV = "PIPELINE_EXTENSIONS"


def pipeline_extensions() -> tuple[str, ...]:
    """Types d'objets traités, lus dans PIPELINE_EXTENSIONS (défaut : tous)."""
    raw = os.environ.get(EXTENSIONS_ENV) or ",".join(SOURCE_EXTENSIONS)
    extensions = tuple(dict.fromkeys(e.strip().lower() for e in raw.split(",") if e.strip()))
    unknown = [e for e in extensions if e not in SOURCE_EXTENSIONS]
    if not extensions or unknown:
        raise ValueError(
            f"{EXTENSIONS_ENV}={raw!r} invalide : valeurs possibles {', '.join(SOURCE_EXTENSIONS)}"
        )
    return extensions


@dataclass(frozen=True)
class RunOptions:
    db_policy: RetryPolicy
    s3_policy: RetryPolicy
    sleep: Callable[[float], None] = time.sleep
    db_fault_hook: DbFaultInjector | None = None
    s3_fault_hook: S3FaultInjector | None = None
    slm_policy: RetryPolicy = field(default_factory=slm_retry_policy)
    slm_settings: SlmSettings = field(default_factory=SlmSettings.from_env)
    extensions: tuple[str, ...] = SOURCE_EXTENSIONS


def load_object(content: bytes, key: str, options: RunOptions) -> pd.DataFrame:
    """Aiguillage selon l'extension ; les trois chemins renvoient le même schéma."""
    if key.endswith(".png"):
        return load_ocr_transaction(image_to_text(content, key), key)
    if key.endswith(".txt"):
        text = decode_text(content, key)
        raw_response = retry_call(
            lambda: chat_json(options.slm_settings, build_messages(text), RESPONSE_SCHEMA),
            options.slm_policy,
            label=key,
            sleep=options.sleep,
        )
        return load_slm_transaction(raw_response, text, key)
    return load_transactions(content, key)


def process_object(client: BaseClient, bucket: str, key: str, options: RunOptions) -> bool:
    """Traite un objet, logge TRAITÉ ou DÉJÀ TRAITÉ et renvoie True si inséré.

    Lève une des _STOPPING_ERRORS en cas d'échec.
    """
    content = retry_call(
        lambda: download_object(client, bucket, key, fault_hook=options.s3_fault_hook),
        options.s3_policy,
        label=key,
        sleep=options.sleep,
    )
    content_hash = hashlib.sha256(content).hexdigest()

    def already_processed() -> bool:
        with get_connection() as conn:
            return is_processed(conn, content_hash)

    # Optimisation : un contenu connu n'est ni chargé, ni passé à l'OCR ou au SLM.
    # La garantie anti-doublon reste l'INSERT ... ON CONFLICT de l'insertion.
    if retry_call(already_processed, options.db_policy, label=key, sleep=options.sleep):
        logger.info("DÉJÀ TRAITÉ   %s : ignoré", key)
        return False

    df = load_object(content, key, options)
    flagged = flag_high_amount(df)
    sums = (sum_by_iban_origine(df), sum_by_banque_source(df), sum_by_iban_destinataire(df))

    def insert_attempt() -> bool:
        # Chaque tentative repart d'une connexion neuve.
        with get_connection() as conn:
            return insert_file_results(
                conn, key, content_hash, flagged, *sums, fault_hook=options.db_fault_hook
            )

    inserted = retry_call(insert_attempt, options.db_policy, label=key, sleep=options.sleep)
    if inserted:
        n_flags = int(flagged[HIGH_AMOUNT_COLUMN].sum())
        logger.info("TRAITÉ        %s : %d transaction(s), %d flag(s)", key, len(df), n_flags)
    else:
        logger.info("DÉJÀ TRAITÉ   %s : ignoré", key)
    return inserted


def run_pipeline(client: BaseClient, settings: S3Settings, options: RunOptions) -> RunSummary:
    summary = RunSummary()
    try:
        keys = retry_call(
            lambda: list_keys(client, settings.bucket, settings.prefix),
            options.s3_policy,
            label=f"listing {settings.location}",
            sleep=options.sleep,
        )
    except (StorageError, RetryExhaustedError) as exc:
        logger.error("ERREUR        listing impossible : %s", exc)
        summary.failed_file = settings.location
        return summary
    keys = [k for k in keys if k.endswith(options.extensions)]
    if not keys:
        logger.warning(
            "Aucun objet à traiter (%s) dans %s", ", ".join(options.extensions), settings.location
        )

    for idx, key in enumerate(keys):
        try:
            inserted = process_object(client, settings.bucket, key, options)
        except _STOPPING_ERRORS as exc:
            logger.error("ERREUR        %s : %s", key, str(exc).strip())
            summary.failed_file = key
            summary.not_examined = len(keys) - idx - 1
            break
        if inserted:
            summary.processed += 1
        else:
            summary.skipped += 1

    logger.info(
        "Résumé : %d traité(s), %d déjà traité(s), %d en erreur, %d non examiné(s)",
        summary.processed,
        summary.skipped,
        0 if summary.ok else 1,
        summary.not_examined,
    )
    if not summary.ok:
        logger.error(
            "Pipeline interrompue sur %s : corriger ou retirer cet objet puis relancer",
            summary.failed_file,
        )
    return summary


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S"
    )
    try:
        settings = S3Settings.from_env()
        options = RunOptions(
            db_policy=db_retry_policy(),
            s3_policy=s3_retry_policy(),
            db_fault_hook=DbFaultInjector.from_env(),
            s3_fault_hook=S3FaultInjector.from_env(),
            slm_policy=slm_retry_policy(),
            slm_settings=SlmSettings.from_env(),
            extensions=pipeline_extensions(),
        )
    except (RuntimeError, ValueError) as exc:
        logger.error("ERREUR        configuration invalide : %s", exc)
        return 1

    logger.info("Source : %s (types : %s)", settings.location, ", ".join(options.extensions))
    if db_hook := options.db_fault_hook:
        logger.warning("Simulation : %d panne(s) DB au point '%s'", db_hook.failures, db_hook.point)
    if s3_hook := options.s3_fault_hook:
        logger.warning("Simulation : %d panne(s) S3 de type '%s'", s3_hook.failures, s3_hook.kind)
    try:
        summary = run_pipeline(get_client(settings), settings, options)
    except RuntimeError as exc:  # DATABASE_URL absente
        logger.error("ERREUR        %s", exc)
        return 1
    return 0 if summary.ok else 1


if __name__ == "__main__":
    sys.exit(main())
