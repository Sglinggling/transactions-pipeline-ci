import logging

import pytest

from pipeline.retry import RetryExhaustedError, RetryPolicy, retry_call


class TemporaryError(Exception):
    pass


class SubTemporaryError(TemporaryError):
    pass


class FakeSleep:
    """Enregistre les délais demandés sans attendre."""

    def __init__(self):
        self.delays = []

    def __call__(self, seconds):
        self.delays.append(seconds)


class Flaky:
    """Échoue `failures` fois avec `error`, puis renvoie "ok"."""

    def __init__(self, failures, error=TemporaryError):
        self.failures = failures
        self.error = error
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error(f"panne {self.calls}")
        return "ok"


def policy(max_attempts=4, initial_delay=1.0, backoff=2.0):
    return RetryPolicy(max_attempts, initial_delay, (TemporaryError,), backoff)


def test_success_first_try_does_not_sleep():
    sleep, func = FakeSleep(), Flaky(0)
    assert retry_call(func, policy(), sleep=sleep) == "ok"
    assert func.calls == 1
    assert sleep.delays == []


def test_success_after_two_failures():
    sleep, func = FakeSleep(), Flaky(2)
    assert retry_call(func, policy(), sleep=sleep) == "ok"
    assert func.calls == 3
    assert sleep.delays == [1.0, 2.0]


def test_exponential_delays():
    sleep = FakeSleep()
    retry_call(Flaky(3), policy(initial_delay=0.5, backoff=3.0), sleep=sleep)
    assert sleep.delays == [0.5, 1.5, 4.5]


def test_exhaustion_raises_and_chains_last_error():
    sleep, func = FakeSleep(), Flaky(10)

    with pytest.raises(RetryExhaustedError) as exc:
        retry_call(func, policy(max_attempts=3), sleep=sleep)

    assert func.calls == 3
    assert sleep.delays == [1.0, 2.0]  # pas d'attente après la dernière tentative
    assert exc.value.attempts == 3
    assert isinstance(exc.value.__cause__, TemporaryError)
    assert exc.value.__cause__ is exc.value.last_error
    assert str(exc.value.__cause__) == "panne 3"
    assert str(exc.value) == "échec après 3 tentative(s) : panne 3"


def test_non_temporary_error_is_not_retried():
    sleep, func = FakeSleep(), Flaky(1, error=ValueError)

    with pytest.raises(ValueError, match="panne 1"):
        retry_call(func, policy(), sleep=sleep)

    assert func.calls == 1
    assert sleep.delays == []


def test_subclass_of_temporary_error_is_retried():
    func = Flaky(1, error=SubTemporaryError)
    assert retry_call(func, policy(), sleep=FakeSleep()) == "ok"
    assert func.calls == 2


def test_single_attempt_policy_never_sleeps():
    sleep = FakeSleep()
    with pytest.raises(RetryExhaustedError):
        retry_call(Flaky(1), policy(max_attempts=1), sleep=sleep)
    assert sleep.delays == []


def test_warning_logs(caplog):
    caplog.set_level(logging.WARNING, logger="pipeline.retry")

    with pytest.raises(RetryExhaustedError):
        retry_call(Flaky(10), policy(max_attempts=3), label="f.csv", sleep=FakeSleep())

    assert [r.levelname for r in caplog.records] == ["WARNING"] * 3
    assert caplog.messages == [
        "Tentative 1/3 échouée (f.csv) : panne 1 — nouvelle tentative dans 1.0 s",
        "Tentative 2/3 échouée (f.csv) : panne 2 — nouvelle tentative dans 2.0 s",
        "Tentative 3/3 échouée (f.csv) : panne 3 — abandon",
    ]


def test_no_log_on_success(caplog):
    caplog.set_level(logging.WARNING, logger="pipeline.retry")
    retry_call(Flaky(0), policy(), sleep=FakeSleep())
    assert caplog.records == []


@pytest.mark.parametrize(
    "kwargs",
    [{"max_attempts": 0}, {"initial_delay": -1.0}, {"backoff": 0.5}],
)
def test_invalid_policy(kwargs):
    with pytest.raises(ValueError):
        policy(**kwargs)


def test_policy_from_env_defaults(monkeypatch):
    monkeypatch.delenv("X_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("X_RETRY_DELAY", raising=False)
    p = RetryPolicy.from_env("X", (TemporaryError,), default_max_attempts=4, default_delay=1.0)
    assert p == RetryPolicy(4, 1.0, (TemporaryError,), 2.0)


def test_policy_from_env_overrides(monkeypatch):
    monkeypatch.setenv("X_MAX_ATTEMPTS", "6")
    monkeypatch.setenv("X_RETRY_DELAY", "0.25")
    p = RetryPolicy.from_env("X", (TemporaryError,), default_max_attempts=4, default_delay=1.0)
    assert (p.max_attempts, p.initial_delay, p.backoff) == (6, 0.25, 2.0)


def test_policy_from_env_invalid(monkeypatch):
    monkeypatch.setenv("X_MAX_ATTEMPTS", "beaucoup")
    with pytest.raises(ValueError, match="X_MAX_ATTEMPTS"):
        RetryPolicy.from_env("X", (TemporaryError,), default_max_attempts=4, default_delay=1.0)
