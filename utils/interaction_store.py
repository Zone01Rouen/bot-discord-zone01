"""
Persistance des interactions de relance (CR / grouping / audit / milestone).

Permet au bot de retrouver le `context`, le `coach_discord_id` et le `type`
associés à un message DM après coup (clic sur le bouton « Répondre » ou
réaction ✅), y compris **après un redémarrage** du bot.

Stockage : fichier JSON `data/pending_interactions.json` au format
    { "<message_id>": { "context": {...}, "coach_discord_id": "...|null", "type": "..." } }

Le module est tolérant au fichier absent ou corrompu (repart d'un dict vide),
et thread/async-safe via un verrou simple : les opérations sont courtes et
synchrones (lecture/écriture JSON), protégées par un threading.RLock.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading

from utils.logger import logger

_STORE_PATH = os.path.join("data", "pending_interactions.json")
_lock = threading.RLock()


def _load() -> dict:
    """Charge le store depuis le disque. Tolérant fichier absent/corrompu."""
    if not os.path.exists(_STORE_PATH):
        return {}
    try:
        with open(_STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning(
                "pending_interactions.json n'est pas un objet JSON — réinitialisé",
                category="cr_relance",
            )
            return {}
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(
            f"Lecture de pending_interactions.json impossible ({e}) — réinitialisé",
            category="cr_relance",
        )
        return {}


def _save(data: dict) -> None:
    """Écrit le store de façon atomique (tmp file + os.replace)."""
    os.makedirs(os.path.dirname(_STORE_PATH), exist_ok=True)
    dir_name = os.path.dirname(_STORE_PATH) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".pending_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, _STORE_PATH)
    except Exception:
        # Nettoyage du fichier temporaire en cas d'échec
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def get(message_id) -> dict | None:
    """Retourne les données associées à un message_id, ou None."""
    key = str(message_id)
    with _lock:
        return _load().get(key)


def put(message_id, data: dict) -> None:
    """Associe `data` (context / coach_discord_id / type) à un message_id."""
    key = str(message_id)
    with _lock:
        store = _load()
        store[key] = data
        _save(store)


def delete(message_id) -> None:
    """Supprime l'entrée d'un message_id (idempotent)."""
    key = str(message_id)
    with _lock:
        store = _load()
        if key in store:
            del store[key]
            _save(store)


def all() -> dict:
    """Retourne une copie de tout le store."""
    with _lock:
        return dict(_load())
