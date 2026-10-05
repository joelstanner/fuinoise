"""Bounded provider requests for sign-in and Discord membership verification."""

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings
from django.views.decorators.debug import sensitive_variables

TWITCH_AUTHORIZE = "https://id.twitch.tv/oauth2/authorize"
TWITCH_TOKEN = "https://id.twitch.tv/oauth2/token"
DISCORD_API = "https://discord.com/api/v10"
MAX_RESPONSE_BYTES = 65536


class ProviderError(Exception):
    def __init__(
        self,
        message: str = "The provider is unavailable. Try again later.",
        *,
        status: int | None = None,
        code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        # Never forward authorization headers or client secrets to redirects.
        return None


@sensitive_variables()
def request_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    form: dict[str, str] | None = None,
) -> dict[str, Any]:
    body = urlencode(form).encode() if form is not None else None
    request = Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "User-Agent": "Fuinoise/1.0",
            **(headers or {}),
        },
    )
    try:
        with build_opener(NoRedirects()).open(request, timeout=5) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ProviderError()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ProviderError()
        return payload
    except HTTPError as error:
        code = None
        try:
            payload = json.loads(error.read(MAX_RESPONSE_BYTES))
            if isinstance(payload, dict) and type(payload.get("code")) is int:
                code = payload["code"]
        except (ValueError, OSError):
            pass
        raise ProviderError(status=error.code, code=code) from None
    except (URLError, OSError, ValueError):
        raise ProviderError() from None


def provider_config(provider: str) -> tuple[str, str, str]:
    if provider not in ("twitch", "discord"):
        raise ProviderError("Unknown sign-in provider.")
    client_id = str(getattr(settings, f"{provider.upper()}_CLIENT_ID"))
    secret = str(getattr(settings, f"{provider.upper()}_CLIENT_SECRET"))
    if not client_id or not secret:
        raise ProviderError(f"{provider.title()} connection is not configured yet.")
    origin = str(settings.FUINOISE_ORIGIN)
    parsed = urlsplit(origin)
    local_http = (
        settings.DEBUG
        and parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "[::1]", "::1"}
    )
    if (
        not (parsed.scheme == "https" or local_http)
        or not parsed.netloc
        or (
            parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        )
    ):
        raise ProviderError("Configure a valid HTTPS site origin for sign-in.")
    if provider == "discord":
        guild_id()
    return client_id, secret, f"{origin}/auth/{provider}/callback/"


def guild_id() -> str:
    value = str(settings.DISCORD_GUILD_ID)
    if not re.fullmatch(r"[0-9]{1,32}", value):
        raise ProviderError("The Fuinoise Discord server is not configured yet.")
    return value


def authorization_url(provider: str, state: str) -> str:
    client_id, _, callback = provider_config(provider)
    params = {
        "client_id": client_id,
        "redirect_uri": callback,
        "response_type": "code",
        "state": state,
        "scope": "identify guilds.members.read" if provider == "discord" else "",
    }
    if provider == "twitch":
        return f"{TWITCH_AUTHORIZE}?{urlencode(params)}"
    return f"https://discord.com/oauth2/authorize?{urlencode(params)}"


@sensitive_variables()
def exchange_code(provider: str, code: str) -> str:
    client_id, secret, callback = provider_config(provider)
    url = TWITCH_TOKEN if provider == "twitch" else f"{DISCORD_API}/oauth2/token"
    payload = request_json(
        url,
        form={
            "client_id": client_id,
            "client_secret": secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": callback,
        },
    )
    token = payload.get("access_token")
    token_type = payload.get("token_type")
    if (
        not isinstance(token, str)
        or not token
        or not isinstance(token_type, str)
        or token_type.lower() != "bearer"
    ):
        raise ProviderError("The provider returned an invalid token response.")
    if provider == "discord" and not {"identify", "guilds.members.read"}.issubset(
        set(str(payload.get("scope", "")).split())
    ):
        raise ProviderError("Discord did not grant the required account permissions.")
    return token


def identity_id(payload: dict[str, Any], key: str = "id") -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,32}", value):
        raise ProviderError("The provider returned an invalid account identity.")
    return value


@dataclass(frozen=True)
class TwitchIdentity:
    id: str
    login: str
    display_name: str


@sensitive_variables()
def twitch_identity(code: str) -> TwitchIdentity:
    client_id, _, _ = provider_config("twitch")
    token = exchange_code("twitch", code)
    verified = request_json(
        "https://id.twitch.tv/oauth2/validate",
        headers={"Authorization": f"OAuth {token}"},
    )
    user_id = identity_id(verified, "user_id")
    expires = verified.get("expires_in")
    if (
        verified.get("client_id") != client_id
        or type(expires) is not int
        or expires < 1
    ):
        raise ProviderError("Twitch could not verify this sign-in.")
    payload = request_json(
        "https://api.twitch.tv/helix/users",
        headers={"Authorization": f"Bearer {token}", "Client-Id": client_id},
    )
    users = payload.get("data")
    if not isinstance(users, list) or len(users) != 1 or not isinstance(users[0], dict):
        raise ProviderError("Twitch returned an invalid profile response.")
    profile = users[0]
    if identity_id(profile) != user_id:
        raise ProviderError("Twitch profile identity did not match the sign-in.")
    login = profile.get("login")
    display = profile.get("display_name")
    if (
        not isinstance(login, str)
        or not re.fullmatch(r"[a-z0-9_]{1,25}", login)
        or (
            verified.get("login") != login
            or not isinstance(display, str)
            or not 1 <= len(display) <= 80
        )
    ):
        raise ProviderError("Twitch returned invalid profile details.")
    return TwitchIdentity(user_id, login, display)


@dataclass(frozen=True)
class DiscordIdentity:
    id: str
    username: str
    member: bool | None
    guild: str


def _member_response(url: str, authorization: str, user_id: str) -> bool:
    try:
        member = request_json(url, headers={"Authorization": authorization})
    except ProviderError as error:
        if error.status == 404 and error.code == 10007:
            return False
        raise
    user = member.get("user")
    if (
        not isinstance(user, dict)
        or identity_id(user) != user_id
        or type(member.get("pending", False)) is not bool
    ):
        raise ProviderError("Discord returned invalid membership information.")
    return not member.get("pending", False)


@sensitive_variables()
def discord_identity(code: str) -> DiscordIdentity:
    token = exchange_code("discord", code)
    profile = request_json(
        f"{DISCORD_API}/users/@me", headers={"Authorization": f"Bearer {token}"}
    )
    user_id = identity_id(profile)
    username = profile.get("username")
    if not isinstance(username, str) or not 1 <= len(username) <= 80:
        raise ProviderError("Discord returned invalid profile details.")
    guild = guild_id()
    try:
        member: bool | None = _member_response(
            f"{DISCORD_API}/users/@me/guilds/{guild}/member", f"Bearer {token}", user_id
        )
    except ProviderError:
        # A verified identity can be linked while membership is unavailable;
        # unavailable membership never grants eligibility.
        member = None
    return DiscordIdentity(user_id, username, member, guild)


@sensitive_variables()
def discord_membership(user_id: str) -> bool:
    if not re.fullmatch(r"[0-9]{1,32}", user_id):
        raise ProviderError("Invalid Discord account identity.")
    guild = guild_id()
    bot = str(settings.DISCORD_BOT_TOKEN)
    if not bot:
        raise ProviderError("Discord membership checks are not configured yet.")
    return _member_response(
        f"{DISCORD_API}/guilds/{guild}/members/{user_id}", f"Bot {bot}", user_id
    )
