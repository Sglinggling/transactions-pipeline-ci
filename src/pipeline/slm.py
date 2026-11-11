"""Appel du SLM local servi par Ollama (module d'I/O, HTTP).

Configuration : SLM_URL (défaut http://localhost:11434), SLM_MODEL, SLM_TIMEOUT
(secondes), SLM_SEED. Appels déterministes : température 0, seed fixe, sortie
JSON contrainte par un schéma (paramètre « format » d'Ollama).

Les erreurs sont classées : TemporarySlmError (service indisponible,
timeout, connexion coupée, 5xx), seule retentée par run.py ; PermanentSlmError
(modèle absent, requête refusée...) jamais retentée.
"""

import http.client
import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_URL = "http://localhost:11434"
# Plus petit modèle testé à 100 % de champs exacts (voir README, tableau comparatif).
DEFAULT_MODEL = "qwen3.5:0.8b"
DEFAULT_TIMEOUT = 120.0  # secondes : le premier appel charge le modèle en mémoire
DEFAULT_SEED = 42


class SlmError(Exception):
    """Erreur d'appel au SLM, déjà classée."""


class TemporarySlmError(SlmError):
    """Panne passagère du service : la seule erreur SLM retentée."""


class PermanentSlmError(SlmError):
    """Erreur définitive (modèle absent, requête invalide) : jamais retentée."""


@dataclass(frozen=True)
class SlmSettings:
    url: str = DEFAULT_URL
    model: str = DEFAULT_MODEL
    timeout: float = DEFAULT_TIMEOUT
    seed: int = DEFAULT_SEED

    @classmethod
    def from_env(cls) -> "SlmSettings":
        try:
            return cls(
                url=os.environ.get("SLM_URL") or DEFAULT_URL,
                model=os.environ.get("SLM_MODEL") or DEFAULT_MODEL,
                timeout=float(os.environ.get("SLM_TIMEOUT") or DEFAULT_TIMEOUT),
                seed=int(os.environ.get("SLM_SEED") or DEFAULT_SEED),
            )
        except ValueError as exc:
            raise RuntimeError(f"SLM_TIMEOUT / SLM_SEED invalide : {exc}") from exc


def build_request(
    settings: SlmSettings, messages: list[dict[str, str]], schema: dict[str, Any]
) -> dict[str, Any]:
    """Corps de la requête /api/chat (pur, testable sans service)."""
    return {
        "model": settings.model,
        "messages": messages,
        "format": schema,
        "stream": False,
        # Pas de phase de raisonnement (modèles « thinking ») : réponse directe,
        # plus rapide et seul le JSON contraint est produit.
        "think": False,
        "options": {"temperature": 0, "seed": settings.seed},
    }


def chat_json(settings: SlmSettings, messages: list[dict[str, str]], schema: dict[str, Any]) -> str:
    """Envoie la conversation au modèle et renvoie le contenu (JSON brut) de sa réponse."""
    body = json.dumps(build_request(settings, messages, schema)).encode("utf-8")
    request = urllib.request.Request(
        f"{settings.url.rstrip('/')}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        if exc.code >= 500 or exc.code == 429:
            raise TemporarySlmError(f"SLM indisponible (HTTP {exc.code}) : {detail}") from exc
        if exc.code == 404:
            raise PermanentSlmError(
                f"modèle {settings.model!r} introuvable (lancer make slm-pull) : {detail}"
            ) from exc
        raise PermanentSlmError(f"requête refusée par le SLM (HTTP {exc.code}) : {detail}") from exc
    except TimeoutError as exc:
        raise TemporarySlmError(f"SLM : délai de {settings.timeout:g} s dépassé") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError | socket.timeout):
            raise TemporarySlmError(f"SLM : délai de {settings.timeout:g} s dépassé") from exc
        raise TemporarySlmError(f"SLM injoignable ({settings.url}) : {exc.reason}") from exc
    except (ConnectionError, http.client.HTTPException) as exc:
        raise TemporarySlmError(f"SLM : connexion interrompue ({exc})") from exc
    except json.JSONDecodeError as exc:
        raise PermanentSlmError("réponse HTTP du SLM illisible (pas du JSON)") from exc

    try:
        return payload["message"]["content"]
    except (KeyError, TypeError) as exc:
        raise PermanentSlmError(f"réponse du SLM sans message : {str(payload)[:200]}") from exc
