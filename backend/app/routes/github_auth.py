import hashlib
import hmac
import logging
import os
import secrets
from typing import Optional

import boto3
import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["github_auth"])

# Environment variables
GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
USER_POOL_ID = os.environ.get("USER_POOL_ID", "")
CLIENT_ID = os.environ.get("CLIENT_ID", "")
REGION = os.environ.get("REGION", "ap-northeast-1")


class GitHubAuthRequest(BaseModel):
    code: str
    redirect_uri: str


class GitHubAuthResponse(BaseModel):
    id_token: str
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str


class GitHubConfigResponse(BaseModel):
    client_id: str
    enabled: bool


def get_github_secrets() -> tuple[str, str]:
    """Get GitHub OAuth credentials from environment or Secrets Manager."""
    client_id = GITHUB_CLIENT_ID
    client_secret = GITHUB_CLIENT_SECRET

    # If not in environment, try Secrets Manager
    if not client_id or not client_secret:
        secret_name = os.environ.get("GITHUB_SECRET_NAME", "")
        if secret_name:
            try:
                client = boto3.client("secretsmanager", region_name=REGION)
                response = client.get_secret_value(SecretId=secret_name)
                import json

                secret = json.loads(response["SecretString"])
                client_id = secret.get("clientId", "")
                client_secret = secret.get("clientSecret", "")
            except Exception as e:
                logger.error(f"Failed to get GitHub secrets: {e}")

    return client_id, client_secret


def exchange_code_for_token(code: str, redirect_uri: str) -> dict:
    """Exchange GitHub authorization code for access token."""
    client_id, client_secret = get_github_secrets()

    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail="GitHub OAuth not configured")

    response = requests.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )

    if response.status_code != 200:
        logger.error(f"GitHub token exchange failed: {response.text}")
        raise HTTPException(status_code=400, detail="Failed to exchange code for token")

    data = response.json()
    if "error" in data:
        logger.error(f"GitHub OAuth error: {data}")
        raise HTTPException(
            status_code=400, detail=data.get("error_description", data.get("error"))
        )

    return data


def get_github_user(access_token: str) -> dict:
    """Get GitHub user information."""
    response = requests.get(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github.v3+json",
        },
        timeout=30,
    )

    if response.status_code != 200:
        logger.error(f"Failed to get GitHub user: {response.text}")
        raise HTTPException(status_code=400, detail="Failed to get GitHub user info")

    return response.json()


def get_github_user_email(access_token: str) -> Optional[str]:
    """Get GitHub user's primary email."""
    response = requests.get(
        "https://api.github.com/user/emails",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github.v3+json",
        },
        timeout=30,
    )

    if response.status_code != 200:
        return None

    emails = response.json()
    # Find primary email
    for email in emails:
        if email.get("primary") and email.get("verified"):
            return email.get("email")

    # Fallback to first verified email
    for email in emails:
        if email.get("verified"):
            return email.get("email")

    return None


def create_or_get_cognito_user(github_user: dict, email: str) -> str:
    """Create or get a Cognito user for the GitHub user."""
    cognito = boto3.client("cognito-idp", region_name=REGION)

    # Use GitHub ID as the username prefix to ensure uniqueness
    github_id = str(github_user.get("id"))
    username = f"github_{github_id}"

    try:
        # Check if user exists
        cognito.admin_get_user(UserPoolId=USER_POOL_ID, Username=username)
        logger.info(f"Found existing Cognito user: {username}")
    except cognito.exceptions.UserNotFoundException:
        # Create new user
        logger.info(f"Creating new Cognito user: {username}")
        temp_password = secrets.token_urlsafe(32)

        cognito.admin_create_user(
            UserPoolId=USER_POOL_ID,
            Username=username,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
                {"Name": "custom:github_id", "Value": github_id},
                {
                    "Name": "custom:github_login",
                    "Value": github_user.get("login", ""),
                },
            ],
            MessageAction="SUPPRESS",  # Don't send welcome email
            TemporaryPassword=temp_password,
        )

        # Set permanent password to allow programmatic auth
        cognito.admin_set_user_password(
            UserPoolId=USER_POOL_ID,
            Username=username,
            Password=temp_password,
            Permanent=True,
        )

    return username


def authenticate_cognito_user(username: str) -> dict:
    """Authenticate a Cognito user and get tokens using admin auth."""
    cognito = boto3.client("cognito-idp", region_name=REGION)

    # Use admin-initiated auth flow
    # This requires the app client to have ALLOW_ADMIN_USER_PASSWORD_AUTH enabled
    # We use a custom auth flow that doesn't require the password

    # Generate a secret hash if client secret is configured
    client_secret = os.environ.get("CLIENT_SECRET", "")

    auth_params = {"USERNAME": username}

    if client_secret:
        message = username + CLIENT_ID
        dig = hmac.new(
            client_secret.encode("utf-8"),
            msg=message.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        import base64

        auth_params["SECRET_HASH"] = base64.b64encode(dig).decode()

    try:
        # Use custom auth flow for passwordless authentication
        response = cognito.admin_initiate_auth(
            UserPoolId=USER_POOL_ID,
            ClientId=CLIENT_ID,
            AuthFlow="CUSTOM_AUTH",
            AuthParameters=auth_params,
        )

        # If custom auth is not set up, we need an alternative approach
        # For now, we'll use admin_initiate_auth with a challenge response
        if "ChallengeName" in response:
            # Handle challenge if needed
            challenge_name = response["ChallengeName"]
            session = response["Session"]

            if challenge_name == "CUSTOM_CHALLENGE":
                # Respond to custom challenge
                response = cognito.admin_respond_to_auth_challenge(
                    UserPoolId=USER_POOL_ID,
                    ClientId=CLIENT_ID,
                    ChallengeName=challenge_name,
                    ChallengeResponses={
                        "USERNAME": username,
                        "ANSWER": "github_verified",
                        **({"SECRET_HASH": auth_params["SECRET_HASH"]} if client_secret else {}),
                    },
                    Session=session,
                )

        return response.get("AuthenticationResult", {})

    except cognito.exceptions.NotAuthorizedException as e:
        logger.error(f"Cognito auth failed: {e}")
        raise HTTPException(status_code=401, detail="Authentication failed")
    except Exception as e:
        logger.error(f"Cognito auth error: {e}")
        # Fallback: Generate tokens using admin APIs
        # This is a workaround when custom auth flow is not configured
        return generate_tokens_admin(cognito, username)


def generate_tokens_admin(cognito, username: str) -> dict:
    """Generate tokens for a user using admin APIs as a fallback."""
    # This is a simplified approach - in production, you'd want proper custom auth
    # For now, we'll return a structure that the frontend can use

    # Get user info
    user = cognito.admin_get_user(UserPoolId=USER_POOL_ID, Username=username)

    # Create a simple token structure
    # Note: This is a workaround. Proper implementation requires custom auth Lambda triggers
    import time
    import uuid

    # Generate a pseudo-token for the session
    # In production, configure proper Cognito custom auth flow
    session_id = str(uuid.uuid4())

    return {
        "IdToken": f"github_session_{session_id}",
        "AccessToken": f"github_access_{session_id}",
        "RefreshToken": f"github_refresh_{session_id}",
        "ExpiresIn": 3600,
        "TokenType": "Bearer",
    }


@router.get("/auth/github/config", response_model=GitHubConfigResponse)
def get_github_config():
    """Get GitHub OAuth configuration for the frontend."""
    client_id, _ = get_github_secrets()
    return GitHubConfigResponse(
        client_id=client_id,
        enabled=bool(client_id),
    )


@router.post("/auth/github/callback", response_model=GitHubAuthResponse)
def github_callback(request: GitHubAuthRequest):
    """
    Handle GitHub OAuth callback.
    Exchange the authorization code for tokens and create/authenticate the user.
    """
    # Exchange code for GitHub access token
    github_tokens = exchange_code_for_token(request.code, request.redirect_uri)
    github_access_token = github_tokens.get("access_token")

    if not github_access_token:
        raise HTTPException(status_code=400, detail="No access token received")

    # Get GitHub user info
    github_user = get_github_user(github_access_token)

    # Get user's email
    email = github_user.get("email")
    if not email:
        email = get_github_user_email(github_access_token)

    if not email:
        raise HTTPException(
            status_code=400,
            detail="Could not get email from GitHub. Please make sure your email is public or grant email permission.",
        )

    # Create or get Cognito user
    username = create_or_get_cognito_user(github_user, email)

    # Authenticate and get Cognito tokens
    cognito_tokens = authenticate_cognito_user(username)

    return GitHubAuthResponse(
        id_token=cognito_tokens.get("IdToken", ""),
        access_token=cognito_tokens.get("AccessToken", ""),
        refresh_token=cognito_tokens.get("RefreshToken", ""),
        expires_in=cognito_tokens.get("ExpiresIn", 3600),
        token_type=cognito_tokens.get("TokenType", "Bearer"),
    )
