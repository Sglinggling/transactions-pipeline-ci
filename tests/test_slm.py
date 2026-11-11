import io
import json
import re
import urllib.error

import pytest

from pipeline import slm
from pipeline.retry import RetryPolicy
from pipeline.run import slm_retry_policy
from pipeline.slm import (
    PermanentSlmError,
    SlmSettings,
    TemporarySlmError,
    build_request,
    chat_json,
)

SETTINGS = SlmSettings(url="http://slm:11434", model="qwen3.5:0.8b", timeout=5, seed=42)
MESSAGES = [{"role": "user", "content": "bonjour"}]
SCHEMA = {"type": "object"}


def test_request_is_deterministic_and_constrained():
    assert build_request(SETTINGS, MESSAGES, SCHEMA) == {
        "model": "qwen3.5:0.8b",
        "messages": MESSAGES,
        "format": SCHEMA,
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "seed": 42},
    }


def test_settings_from_env(monkeypatch):
    for name in ["SLM_URL", "SLM_MODEL", "SLM_TIMEOUT", "SLM_SEED"]:
        monkeypatch.delenv(name, raising=False)
    assert SlmSettings.from_env() == SlmSettings(
        "http://localhost:11434", "qwen3.5:0.8b", 120.0, 42
    )
    monkeypatch.setenv("SLM_MODEL", "autre:1b")
    monkeypatch.setenv("SLM_TIMEOUT", "30")
    assert SlmSettings.from_env().model == "autre:1b"
    assert SlmSettings.from_env().timeout == 30.0
    monkeypatch.setenv("SLM_SEED", "graine")
    with pytest.raises(RuntimeError, match="SLM_SEED"):
        SlmSettings.from_env()


def test_slm_policy(monkeypatch):
    from pipeline.slm import TemporarySlmError as Temporary

    monkeypatch.delenv("SLM_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("SLM_RETRY_DELAY", raising=False)
    assert slm_retry_policy() == RetryPolicy(4, 2.0, (Temporary,), 2.0)
    monkeypatch.setenv("SLM_MAX_ATTEMPTS", "2")
    assert slm_retry_policy().max_attempts == 2


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def fake_urlopen(result):
    def urlopen(request, timeout):
        urlopen.request, urlopen.timeout = request, timeout
        if isinstance(result, BaseException):
            raise result
        return FakeResponse(json.dumps(result).encode())

    return urlopen


def http_error(code, body=b"detail"):
    return urllib.error.HTTPError("http://slm", code, "erreur", {}, io.BytesIO(body))


def test_chat_json_returns_message_content(monkeypatch):
    urlopen = fake_urlopen({"message": {"role": "assistant", "content": '{"date": null}'}})
    monkeypatch.setattr(slm.urllib.request, "urlopen", urlopen)

    assert chat_json(SETTINGS, MESSAGES, SCHEMA) == '{"date": null}'
    assert urlopen.request.full_url == "http://slm:11434/api/chat"
    assert urlopen.timeout == 5
    assert json.loads(urlopen.request.data)["options"] == {"temperature": 0, "seed": 42}


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (http_error(503), "SLM indisponible (HTTP 503)"),
        (http_error(500), "SLM indisponible (HTTP 500)"),
        (urllib.error.URLError(ConnectionRefusedError(61, "refused")), "SLM injoignable"),
        (urllib.error.URLError(TimeoutError("timed out")), "délai de 5 s dépassé"),
        (TimeoutError("timed out"), "délai de 5 s dépassé"),
        (ConnectionResetError("reset"), "connexion interrompue"),
    ],
)
def test_temporary_errors(monkeypatch, error, message):
    monkeypatch.setattr(slm.urllib.request, "urlopen", fake_urlopen(error))
    with pytest.raises(TemporarySlmError, match=re.escape(message)):
        chat_json(SETTINGS, MESSAGES, SCHEMA)


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (http_error(404, b'{"error":"model not found"}'), "introuvable \\(lancer make slm-pull\\)"),
        (http_error(400), "requête refusée par le SLM \\(HTTP 400\\)"),
    ],
)
def test_permanent_errors(monkeypatch, error, message):
    monkeypatch.setattr(slm.urllib.request, "urlopen", fake_urlopen(error))
    with pytest.raises(PermanentSlmError, match=message):
        chat_json(SETTINGS, MESSAGES, SCHEMA)


def test_response_without_message_is_permanent(monkeypatch):
    monkeypatch.setattr(slm.urllib.request, "urlopen", fake_urlopen({"error": "oups"}))
    with pytest.raises(PermanentSlmError, match="sans message"):
        chat_json(SETTINGS, MESSAGES, SCHEMA)
