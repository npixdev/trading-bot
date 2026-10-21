"""Envoi de messages. Une alerte qui échoue ne doit JAMAIS faire planter le bot.

Les messages sont en HTML Telegram (gras, italique) : tout texte variable doit passer par `escape()`.
"""

from __future__ import annotations

import html
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import requests
from dotenv import load_dotenv

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
MAX_LENGTH = 4000  # limite Telegram : 4096 caractères par message
MAX_CAPTION = 1000  # limite Telegram : 1024 caractères pour la légende d'une image


def escape(text: object) -> str:
    """Neutralise <, > et & : un message d'erreur de l'exchange ne doit pas casser la mise en forme."""
    return html.escape(str(text), quote=False)


class Notifier(Protocol):
    def send(self, text: str) -> bool: ...
    def send_photo(self, png: bytes, caption: str = "") -> bool: ...


class NullNotifier:
    """Alertes non configurées : on se contente du journal."""

    def send(self, text: str) -> bool:
        log.debug("Alerte (non envoyée, Telegram non configuré) : %s", text)
        return False

    def send_photo(self, png: bytes, caption: str = "") -> bool:
        return False


class TelegramNotifier:
    def __init__(
        self,
        token: str,
        chat_id: str,
        post: Callable[..., Any] = requests.post,
        get: Callable[..., Any] = requests.get,
        timeout: float = 10.0,
    ) -> None:
        self.token, self.chat_id, self.post, self.get, self.timeout = token, chat_id, post, get, timeout

    def __repr__(self) -> str:  # le token donne le contrôle du bot Telegram : jamais dans un log
        return f"TelegramNotifier(chat_id={self.chat_id}, token=***)"

    def _url(self, method: str) -> str:
        return f"{TELEGRAM_API}/bot{self.token}/{method}"

    def _call(self, method: str, **kwargs: Any) -> bool:
        try:
            response = self.post(self._url(method), timeout=self.timeout, **kwargs)
            if response.status_code != 200:
                log.warning("Telegram a refusé %s (HTTP %s) : %s", method, response.status_code, response.text[:200])
                return False
            return True
        except requests.RequestException as exc:
            # Pas de détail de l'exception : l'URL qu'elle contient inclut le token.
            log.warning("Telegram injoignable pour %s (%s)", method, type(exc).__name__)
            return False

    def send(self, text: str) -> bool:
        return self._call(
            "sendMessage",
            json={"chat_id": self.chat_id, "text": text[:MAX_LENGTH], "parse_mode": "HTML",
                  "disable_web_page_preview": True},
        )

    def send_photo(self, png: bytes, caption: str = "") -> bool:
        return self._call(
            "sendPhoto",
            data={"chat_id": self.chat_id, "caption": caption[:MAX_CAPTION], "parse_mode": "HTML"},
            files={"photo": ("graphique.png", png, "image/png")},
        )

    def get_updates(self, offset: int | None = None) -> list[dict[str, Any]]:
        """Messages reçus par le bot (lecture immédiate, sans attente)."""
        params: dict[str, Any] = {"timeout": 0, "allowed_updates": '["message"]'}
        if offset is not None:
            params["offset"] = offset
        try:
            response = self.get(self._url("getUpdates"), params=params, timeout=self.timeout)
            return response.json().get("result", []) if response.status_code == 200 else []
        except (requests.RequestException, ValueError) as exc:
            log.warning("Lecture des commandes Telegram impossible (%s)", type(exc).__name__)
            return []

    def find_chat_ids(self) -> list[tuple[str, str]]:
        """(chat_id, nom) des conversations qui ont écrit au bot : aide à configurer TELEGRAM_CHAT_ID."""
        found: dict[str, str] = {}
        for update in self.get_updates():
            chat = (update.get("message") or update.get("channel_post") or {}).get("chat") or {}
            if "id" in chat:
                name = chat.get("username") or chat.get("title") or chat.get("first_name")
                if name or str(chat["id"]) not in found:
                    found[str(chat["id"])] = name or "?"
        return list(found.items())


def notifier_from_env(env_file: Path = Path(".env")) -> Notifier:
    load_dotenv(env_file, override=False)
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if token and chat_id:
        return TelegramNotifier(token, chat_id)
    return NullNotifier()
