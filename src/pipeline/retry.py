"""Retry générique avec backoff exponentiel.

Indépendant de la base : réutilisable pour toute opération sujette à des
pannes temporaires (base, S3...).
"""

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger("pipeline.retry")


class RetryExhaustedError(Exception):
    """Toutes les tentatives ont échoué ; la dernière erreur est chaînée (__cause__)."""

    def __init__(self, attempts: int, last_error: BaseException) -> None:
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(f"échec après {attempts} tentative(s) : {last_error}")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    initial_delay: float
    retry_on: tuple[type[BaseException], ...]
    backoff: float = 2.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts doit être >= 1")
        if self.initial_delay < 0:
            raise ValueError("initial_delay doit être >= 0")
        if self.backoff < 1:
            raise ValueError("backoff doit être >= 1")

    @classmethod
    def from_env(
        cls,
        prefix: str,
        retry_on: tuple[type[BaseException], ...],
        default_max_attempts: int,
        default_delay: float,
        backoff: float = 2.0,
    ) -> "RetryPolicy":
        """Lit <prefix>_MAX_ATTEMPTS et <prefix>_RETRY_DELAY (secondes)."""
        attempts_var, delay_var = f"{prefix}_MAX_ATTEMPTS", f"{prefix}_RETRY_DELAY"
        try:
            max_attempts = int(os.environ.get(attempts_var, default_max_attempts))
            delay = float(os.environ.get(delay_var, default_delay))
        except ValueError as exc:
            raise ValueError(f"{attempts_var} / {delay_var} invalide : {exc}") from exc
        return cls(max_attempts, delay, retry_on, backoff)


def retry_call[T](
    func: Callable[[], T],
    policy: RetryPolicy,
    *,
    label: str = "opération",
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Appelle func jusqu'à succès ou épuisement des tentatives.

    Seules les exceptions de policy.retry_on sont retentées ; les autres
    remontent immédiatement.
    """
    delay = policy.initial_delay
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return func()
        except policy.retry_on as exc:
            if attempt == policy.max_attempts:
                logger.warning(
                    "Tentative %d/%d échouée (%s) : %s — abandon",
                    attempt,
                    policy.max_attempts,
                    label,
                    exc,
                )
                raise RetryExhaustedError(attempt, exc) from exc
            logger.warning(
                "Tentative %d/%d échouée (%s) : %s — nouvelle tentative dans %.1f s",
                attempt,
                policy.max_attempts,
                label,
                exc,
                delay,
            )
            sleep(delay)
            delay *= policy.backoff
    raise AssertionError("inatteignable")  # pragma: no cover
