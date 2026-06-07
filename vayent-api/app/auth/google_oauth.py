"""Google OAuth authentication handler."""
import logging
from typing import Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_OAUTH_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


class GoogleOAuthHandler:
    """Handle Google OAuth authentication flow."""

    def __init__(self):
        self.settings = get_settings()

    async def exchange_code_for_token(self, code: str) -> Optional[dict]:
        """Exchange authorization code for an access token."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                response = await client.post(
                    GOOGLE_OAUTH_TOKEN_URL,
                    data={
                        "client_id": self.settings.google_client_id,
                        "client_secret": self.settings.google_client_secret,
                        "code": code,
                        "grant_type": "authorization_code",
                        "redirect_uri": self.settings.google_redirect_uri,
                    },
                    headers={"Accept": "application/json"},
                )

                if response.status_code == 200:
                    return response.json()

                logger.error(
                    "Google token exchange failed with status %s: %s",
                    response.status_code,
                    response.text,
                )
                return None
            except Exception as exc:
                logger.error("Google OAuth token exchange error: %s", exc)
                return None

    async def get_user_info(self, access_token: str) -> Optional[dict]:
        """Fetch the Google user profile for the given access token."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                response = await client.get(
                    GOOGLE_OAUTH_USERINFO_URL,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                    },
                )

                if response.status_code == 200:
                    return response.json()

                logger.error(
                    "Google user info fetch failed with status %s: %s",
                    response.status_code,
                    response.text,
                )
                return None
            except Exception as exc:
                logger.error("Google user info fetch error: %s", exc)
                return None


google_oauth = GoogleOAuthHandler()
