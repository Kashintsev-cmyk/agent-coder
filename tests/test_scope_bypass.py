"""M2 (2026-11-07): `..`-bypass в in_scope закрыт.

Регресс на finding `8f0cd848`/M2. До фикса `in_scope("bot/../.env", ["bot/"])`
возвращал True из-за `lstrip("./")` + prefix-match. Теперь PurePosixPath-нормализация
и явный reject `..` в parts.
"""

from __future__ import annotations

import pytest

from agent_coder.tools._scope import in_scope


@pytest.mark.parametrize(
    "rel,scopes,want",
    [
        # baseline
        ("bot/x.py", ["bot/"], True),
        ("bot/sub/x.py", ["bot/"], True),
        ("bot", ["bot/"], True),
        ("bot2/x.py", ["bot/"], False),
        # bypass — все False
        ("bot/../.env", ["bot/"], False),
        ("../bot/x", ["bot/"], False),
        ("a/../../etc", ["bot/"], False),
        ("/etc/passwd", ["bot/"], False),
        # нормализация `./` и `.`
        ("./bot/x", ["bot/"], True),
        ("bot/./x", ["bot/"], True),
        # без ограничений
        ("bot/../.env", None, True),
        ("bot/../.env", [], True),
        # битый scope игнорируется
        ("bot/x.py", [".."], False),
        # scope "." = весь workspace = allow-all (B9.2h: system diagnostics)
        ("anything/x", ["."], True),
        ("/etc/hosts", ["."], True),
    ],
)
def test_in_scope_bypass_closed(rel: str, scopes, want: bool) -> None:
    assert in_scope(rel, scopes) is want
