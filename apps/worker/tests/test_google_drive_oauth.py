"""Tests for Google Drive OAuth (MVP1-061), including credential refresh."""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from worker.google_drive.oauth import (
    DriveCredentials,
    GoogleDriveOAuth,
    OAuthError,
    TokenRefreshRequired,
)

CLIENT_ID = "client-id.apps.googleusercontent.com"
CLIENT_SECRET = "client-secret"
REDIRECT_URI = "http://localhost:3000/api/connections/google-drive/callback"


def oauth_with(handler) -> GoogleDriveOAuth:
    transport = httpx.MockTransport(handler)
    return GoogleDriveOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        http_client=httpx.AsyncClient(transport=transport),
    )


def test_authorization_url_requests_offline_access_and_readonly_scope():
    oauth = oauth_with(lambda request: httpx.Response(200))
    params = parse_qs(urlparse(oauth.authorization_url(state="csrf-token")).query)

    # Without both of these Google returns no refresh token on re-consent,
    # which silently breaks background sync after the access token expires.
    assert params["access_type"] == ["offline"]
    assert params["prompt"] == ["consent"]
    assert params["scope"] == ["https://www.googleapis.com/auth/drive.readonly"]
    assert params["state"] == ["csrf-token"]
    assert params["redirect_uri"] == [REDIRECT_URI]


def test_authorization_scope_is_read_only():
    """The product must never modify or delete a customer's source files."""
    oauth = oauth_with(lambda request: httpx.Response(200))
    assert "drive.readonly" in oauth.authorization_url(state="s")
    assert "auth/drive " not in oauth.authorization_url(state="s")


@pytest.mark.asyncio
async def test_code_exchange_returns_credentials():
    def handler(request: httpx.Request) -> httpx.Response:
        body = parse_qs(request.content.decode())
        assert body["grant_type"] == ["authorization_code"]
        assert body["code"] == ["auth-code"]
        return httpx.Response(
            200,
            json={
                "access_token": "access-1",
                "refresh_token": "refresh-1",
                "expires_in": 3600,
            },
        )

    credentials = await oauth_with(handler).exchange_code(code="auth-code")
    assert credentials.refresh_token == "refresh-1"
    assert credentials.access_token == "access-1"
    assert not credentials.is_expired


@pytest.mark.asyncio
async def test_code_exchange_without_refresh_token_is_an_error():
    """A missing refresh token would break sync an hour later, not now."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "access-1", "expires_in": 3600})

    with pytest.raises(OAuthError, match="refresh token"):
        await oauth_with(handler).exchange_code(code="auth-code")


@pytest.mark.asyncio
async def test_valid_token_is_not_refreshed():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"access_token": "new", "expires_in": 3600})

    fresh = DriveCredentials(
        refresh_token="refresh-1",
        access_token="still-good",
        expires_at_epoch=time.time() + 3600,
    )
    result = await oauth_with(handler).ensure_access_token(fresh)

    assert result.access_token == "still-good"
    assert calls == []


@pytest.mark.asyncio
async def test_expired_token_is_refreshed():
    def handler(request: httpx.Request) -> httpx.Response:
        body = parse_qs(request.content.decode())
        assert body["grant_type"] == ["refresh_token"]
        return httpx.Response(200, json={"access_token": "refreshed", "expires_in": 3600})

    expired = DriveCredentials(
        refresh_token="refresh-1", access_token="old", expires_at_epoch=time.time() - 10
    )
    result = await oauth_with(handler).ensure_access_token(expired)

    assert result.access_token == "refreshed"
    # Google omits refresh_token on refresh; the stored one must survive.
    assert result.refresh_token == "refresh-1"


@pytest.mark.asyncio
async def test_token_nearing_expiry_is_refreshed_early():
    """A token expiring in 30s would die mid-download without skew."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "refreshed", "expires_in": 3600})

    nearly_expired = DriveCredentials(
        refresh_token="refresh-1", access_token="old", expires_at_epoch=time.time() + 30
    )
    result = await oauth_with(handler).ensure_access_token(nearly_expired)
    assert result.access_token == "refreshed"


@pytest.mark.asyncio
async def test_revoked_grant_requires_user_reconsent():
    """Revocation is permanent: pause the connection, do not retry forever."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    expired = DriveCredentials(
        refresh_token="revoked", access_token="old", expires_at_epoch=time.time() - 10
    )
    with pytest.raises(TokenRefreshRequired, match="Reconnect"):
        await oauth_with(handler).ensure_access_token(expired)


@pytest.mark.asyncio
async def test_server_error_on_refresh_is_retryable_not_terminal():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "backend_error"})

    expired = DriveCredentials(
        refresh_token="refresh-1", access_token="old", expires_at_epoch=time.time() - 10
    )
    # OAuthError, not TokenRefreshRequired: a 503 should be retried.
    with pytest.raises(OAuthError):
        await oauth_with(handler).ensure_access_token(expired)


def test_credentials_round_trip_through_storage():
    original = DriveCredentials(
        refresh_token="refresh-1", access_token="access-1", expires_at_epoch=1_800_000_000.0
    )
    restored = DriveCredentials.from_storage(original.to_storage())
    assert restored == original


def test_stored_credentials_without_a_refresh_token_require_reconsent():
    with pytest.raises(TokenRefreshRequired):
        DriveCredentials.from_storage({"access_token": "orphan"})
