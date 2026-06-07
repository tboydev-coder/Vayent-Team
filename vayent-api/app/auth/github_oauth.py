"""GitHub OAuth authentication handler."""
import httpx
import logging
from typing import Optional
from app.config import get_settings

logger = logging.getLogger(__name__)

GITHUB_API_URL = "https://api.github.com"
GITHUB_OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"


class GithubOAuthHandler:
    """Handle GitHub OAuth authentication flow."""

    def __init__(self):
        self.settings = get_settings()

    async def exchange_code_for_token(self, code: str) -> Optional[dict]:
        """
        Exchange authorization code for access token.

        Returns: {access_token, token_type, scope} or None if failed
        """
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                response = await client.post(
                    GITHUB_OAUTH_TOKEN_URL,
                    data={
                        "client_id": self.settings.github_client_id,
                        "client_secret": self.settings.github_client_secret,
                        "code": code,
                    },
                    headers={"Accept": "application/json"},
                )

                if response.status_code == 200:
                    return response.json()
                else:
                    logger.error(
                        "GitHub token exchange failed with status %s",
                        response.status_code,
                    )
                    return None

            except Exception as e:
                logger.error(f"GitHub OAuth token exchange error: {e}")
                return None

    async def get_user_info(self, access_token: str) -> Optional[dict]:
        """
        Get user information from GitHub using access token.

        We try both ``Bearer`` and ``token`` authorization schemes since
        GitHub historically accepted ``token`` for personal access tokens and
        ``Bearer`` for OAuth flows.  Log warnings for each failed attempt so
        callers can see what went wrong.

        Returns: {id, login, email, name, ...} or None if failed
        """
        headers_base = {"Accept": "application/vnd.github.v3+json"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            last_text = None
            # try two different auth schemes
            for scheme in ("Bearer", "token"):
                try:
                    response = await client.get(
                        f"{GITHUB_API_URL}/user",
                        headers={
                            **headers_base,
                            "Authorization": f"{scheme} {access_token}",
                        },
                    )

                    if response.status_code == 200:
                        return response.json()
                    else:
                        last_text = response.text
                        logger.warning(
                            f"GitHub user info fetch failed ({scheme}): {response.status_code} {response.text}"
                        )
                        # try next scheme
                except Exception as e:
                    logger.error(f"GitHub user info error ({scheme}): {e}")
                    # keep going; maybe next scheme works
            logger.error(
                f"GitHub user info fetch ultimately failed, last response: {last_text}"
            )

    async def get_primary_email(self, access_token: str) -> Optional[str]:
        """
        Get user's primary email from GitHub.

        Returns: email address or None if not found
        """
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                response = await client.get(
                    f"{GITHUB_API_URL}/user/emails",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/vnd.github.v3+json",
                    },
                )

                if response.status_code == 200:
                    emails = response.json()
                    # Find primary email
                    for email_obj in emails:
                        if email_obj.get("primary"):
                            return email_obj.get("email")
                    # Fallback: return first verified email
                    for email_obj in emails:
                        if email_obj.get("verified"):
                            return email_obj.get("email")
                    return None
                else:
                    logger.error(f"GitHub email fetch failed: {response.text}")
                    return None

            except Exception as e:
                logger.error(f"GitHub email fetch error: {e}")
                return None


# Singleton instance
github_oauth = GithubOAuthHandler()
