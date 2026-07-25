"""Google Drive OAuth support (spec section 11.2, MVP1-061).

This authorizes *Drive access for media ingestion*. It is deliberately separate
from any "Sign in with Google" login option (spec section 10.1) — the two use
different scopes and must not share credentials.

The worker never sees a user's Google password. It handles only the
authorization code exchange and subsequent refresh-token grants. Refresh
tokens are long-lived credentials: they are stored encrypted by the
connection repository and must never be logged or returned to a browser
(spec section 9.2).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from worker.logging import get_logger

logger = get_logger(__name__)

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

# Read-only: the product must never modify or delete a customer's source files
# (spec section 3, non-goals).
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"

# Refresh a little before actual expiry so a long download does not fail
# halfway through on a token that expired mid-flight.
EXPIRY_SKEW_SECONDS = 120


class OAuthError(RuntimeError):
    """The authorization or refresh grant was rejected."""


class TokenRefreshRequired(RuntimeError):
    """The stored credentials cannot be refreshed and need user re-consent.

    The connection should be marked as requiring attention rather than retried
    (spec section 11.5, "Authentication expiry").
    """


@dataclass(frozen=True)
class DriveCredentials:
    refresh_token: str
    access_token: str | None = None
    expires_at_epoch: float | None = None

    @property
    def is_expired(self) -> bool:
        if not self.access_token or self.expires_at_epoch is None:
            return True
        return time.time() >= (self.expires_at_epoch - EXPIRY_SKEW_SECONDS)

    def to_storage(self) -> dict[str, object]:
        """Shape handed to the connection repository for encrypted storage."""
        return {
            "refresh_token": self.refresh_token,
            "access_token": self.access_token,
            "expires_at_epoch": self.expires_at_epoch,
        }

    @classmethod
    def from_storage(cls, stored: dict[str, object]) -> DriveCredentials:
        refresh_token = stored.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise TokenRefreshRequired("No refresh token stored for this connection.")
        expires_at = stored.get("expires_at_epoch")
        access_token = stored.get("access_token")
        return cls(
            refresh_token=refresh_token,
            access_token=access_token if isinstance(access_token, str) else None,
            expires_at_epoch=float(expires_at) if isinstance(expires_at, (int | float)) else None,
        )


class GoogleDriveOAuth:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._http = http_client or httpx.AsyncClient(timeout=30.0)

    def authorization_url(self, *, state: str) -> str:
        """Build the consent URL the user is redirected to.

        ``access_type=offline`` with ``prompt=consent`` is what makes Google
        return a refresh token; without both, re-authorizing an existing grant
        returns only a short-lived access token and background sync breaks
        after an hour.

        ``state`` must be an unguessable, single-use value that the callback
        endpoint verifies, otherwise the callback is open to CSRF.
        """
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": DRIVE_SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
        return f"{AUTHORIZATION_ENDPOINT}?{urlencode(params)}"

    async def exchange_code(self, *, code: str) -> DriveCredentials:
        """Trade the one-time authorization code for tokens."""
        response = await self._http.post(
            TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "redirect_uri": self._redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        payload = _parse_token_response(response)

        refresh_token = payload.get("refresh_token")
        if not refresh_token:
            raise OAuthError(
                "Google did not return a refresh token. The user likely has an "
                "existing grant; re-request consent with prompt=consent."
            )

        logger.info("google_drive.authorized")
        return DriveCredentials(
            refresh_token=str(refresh_token),
            access_token=str(payload["access_token"]),
            expires_at_epoch=time.time() + float(payload.get("expires_in", 3600)),
        )

    async def ensure_access_token(self, credentials: DriveCredentials) -> DriveCredentials:
        """Return credentials with a valid access token, refreshing if needed."""
        if not credentials.is_expired:
            return credentials

        response = await self._http.post(
            TOKEN_ENDPOINT,
            data={
                "refresh_token": credentials.refresh_token,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "refresh_token",
            },
        )

        if response.status_code in (400, 401):
            # A revoked or expired grant is permanent: the user must reconnect.
            raise TokenRefreshRequired(
                "Google Drive access was revoked or expired. Reconnect the folder."
            )

        payload = _parse_token_response(response)
        logger.info("google_drive.token_refreshed")
        return DriveCredentials(
            # Google usually omits refresh_token on refresh; keep the stored one.
            refresh_token=str(payload.get("refresh_token") or credentials.refresh_token),
            access_token=str(payload["access_token"]),
            expires_at_epoch=time.time() + float(payload.get("expires_in", 3600)),
        )

    async def aclose(self) -> None:
        await self._http.aclose()


def _parse_token_response(response: httpx.Response) -> dict:
    if response.status_code >= 400:
        # Google returns the reason in the body; the body never contains the
        # client secret, but it can echo the code, so only the error field is
        # surfaced.
        try:
            detail = response.json().get("error", "unknown_error")
        except ValueError:
            detail = "unparseable_error_response"
        raise OAuthError(f"Google token endpoint returned {response.status_code}: {detail}")

    payload = response.json()
    if "access_token" not in payload:
        raise OAuthError("Google token response did not include an access token.")
    return payload
