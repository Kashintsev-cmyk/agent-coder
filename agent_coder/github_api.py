"""Тонкая обёртка над GitHub API (только необходимое для бота).

Используется:
- /ghnew   → create_repo()
- /status  → whoami() (проверка токена)
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_API = "https://api.github.com"
_TIMEOUT = 20.0


class GitHubError(RuntimeError):
    """Ошибка GitHub API. Содержит status_code и message."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"GitHub {status}: {message}")
        self.status = status
        self.message = message


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def whoami(
    token: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Вернуть login пользователя. Бросает GitHubError при ошибке."""
    owns = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        r = await client.get(f"{_API}/user", headers=_headers(token))
        if r.status_code != 200:
            raise GitHubError(r.status_code, r.text[:200])
        return r.json().get("login", "")
    finally:
        if owns:
            await client.aclose()


async def create_repo(
    name: str,
    *,
    private: bool,
    token: str,
    description: str = "",
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Создать репозиторий. Возвращает ответ API.

    - name: короткое имя (без owner)
    - private: True — приватный
    - description: пусто = без описания

    Бросает GitHubError при ошибке.
    """
    owns = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        body: dict[str, Any] = {
            "name": name,
            "private": private,
            # Без auto_init — мы запушим свой initial commit сами
            "auto_init": False,
        }
        if description:
            body["description"] = description

        r = await client.post(
            f"{_API}/user/repos",
            headers=_headers(token),
            json=body,
        )
        if r.status_code not in (200, 201):
            # 422 = имя занято, 401 = плохой токен, 403 = нет scope
            try:
                msg = r.json().get("message", r.text[:200])
            except Exception:
                msg = r.text[:200]
            raise GitHubError(r.status_code, msg)
        return r.json()
    finally:
        if owns:
            await client.aclose()
