"""Тесты github_api: create_repo, whoami (через httpx.MockTransport)."""

from __future__ import annotations

import httpx
import pytest

from agent_coder.github_api import GitHubError, create_repo, whoami


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_whoami_ok():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/user"
        assert req.headers["authorization"].startswith("Bearer ")
        return httpx.Response(200, json={"login": "test-user"})

    async with _client(handler) as c:
        login = await whoami("tok", client=c)
    assert login == "test-user"


@pytest.mark.asyncio
async def test_whoami_401():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    async with _client(handler) as c:
        with pytest.raises(GitHubError) as ei:
            await whoami("bad", client=c)
    assert ei.value.status == 401
    assert "Bad credentials" in ei.value.message


@pytest.mark.asyncio
async def test_create_repo_private():
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["method"] = req.method
        captured["path"] = req.url.path
        captured["body"] = req.read().decode()
        return httpx.Response(
            201,
            json={
                "name": "my-proj",
                "ssh_url": "git@github.com:me/my-proj.git",
                "html_url": "https://github.com/me/my-proj",
                "private": True,
            },
        )

    async with _client(handler) as c:
        repo = await create_repo("my-proj", private=True, token="tok", client=c)

    assert captured["method"] == "POST"
    assert captured["path"] == "/user/repos"
    assert '"name":"my-proj"' in captured["body"].replace(" ", "")
    assert '"private":true' in captured["body"].replace(" ", "")
    assert '"auto_init":false' in captured["body"].replace(" ", "")
    assert repo["html_url"] == "https://github.com/me/my-proj"


@pytest.mark.asyncio
async def test_create_repo_public():
    def handler(req: httpx.Request) -> httpx.Response:
        assert '"private":false' in req.read().decode().replace(" ", "")
        return httpx.Response(201, json={"ssh_url": "x", "html_url": "y"})

    async with _client(handler) as c:
        await create_repo("p", private=False, token="tok", client=c)


@pytest.mark.asyncio
async def test_create_repo_with_description():
    def handler(req: httpx.Request) -> httpx.Response:
        body = req.read().decode()
        assert "description" in body
        assert "my desc" in body
        return httpx.Response(201, json={"ssh_url": "x", "html_url": "y"})

    async with _client(handler) as c:
        await create_repo(
            "p", private=False, token="tok", description="my desc", client=c
        )


@pytest.mark.asyncio
async def test_create_repo_422_name_taken():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "message": "Repository creation failed.",
                "errors": [{"message": "name already exists"}],
            },
        )

    async with _client(handler) as c:
        with pytest.raises(GitHubError) as ei:
            await create_repo("dup", private=False, token="tok", client=c)
    assert ei.value.status == 422
