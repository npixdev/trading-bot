"""Envoi de messages. Une alerte qui échoue ne doit JAMAIS faire planter le bot."""

from __future__ import annotations

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


class Notifier(Protocol):
    def send(self, text: str) -> bool: ...


class NullNotifier:
    """Alertes non configurées : on se contente du journal."""

    def send(self, text: str) -> bool:
        log.debug("Alerte (non envoyée, Telegram non configuré) : %s", text)
        return False


class TelegramNotifier:
    def __init__(
        self,
        token: str,
        chat_id: str,
        post: Callable[..., Any] = requests.post,
        timeout: float = 10.0,
    ) -> None:
        self.token, self.chat_id, self.post, self.timeout = token, chat_id, post, timeout

    def __repr__(self) -> str:  # le token donne le contrôle du bot Telegram : jamais dans un log
        return f"TelegramNotifier(chat_id={self.chat_id}, token=***)"

    def send(self, text: str) -> bool:
        try:
            response = self.post(
                f"{TELEGRAM_API}/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text[:MAX_LENGTH], "disable_web_page_preview": True},
                timeout=self.timeout,
            )
            if response.status_code != 200:
                log.warning("Telegram a refusé l'alerte (HTTP %s) : %s", response.status_code, response.text[:200])
                return False
            return True
        except requests.RequestException as exc:
            # Pas de détail de l'exception : l'URL qu'elle contient inclut le token.
            log.warning("Alerte Telegram non envoyée (%s)", type(exc).__name__)
            return False

    def find_chat_ids(self, get: Callable[..., Any] = requests.get) -> list[tuple[str, str]]:
        """(chat_id, nom) des conversations qui ont écrit au bot : aide à configurer TELEGRAM_CHAT_ID."""
        response = get(f"{TELEGRAM_API}/bot{self.token}/getUpdates", timeout=self.timeout)
        found: dict[str, str] = {}
        for update in response.json().get("result", []):
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
