"""Google sign-in from the chat: `/google`, no terminal.

Before this, Calendar, Gmail and Drive needed `gws auth setup` and
`gws auth login` typed into the container through `docker exec`, because the
login is an OAuth flow in a browser and the add-on has no browser. Now the
transport runs that flow itself, with the owner's phone as the browser:

1. `/google`: this module mints a PKCE verifier and a `state`, keeps both in
   memory, and sends the owner Google's consent link.
2. The owner signs in and allows. Google redirects to a loopback address
   (`REDIRECT`) where nothing listens, so the phone shows a page that does
   not load -- with the code in its address bar.
3. The owner pastes that address into the chat. The transport catches it
   before the model or memory/raw sees it, deletes the message from the chat,
   swaps the code for a refresh token at Google, and writes it to
   state/google/credentials.json in the `authorized_user` shape `gws auth
   export` prints. Every `gws` call is then pointed at that file through
   GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE (`gws_env`).

Why the pasted address may cross Telegram when credentials may not: the code
in it is single-use, dies within minutes, and is worthless without the PKCE
verifier, which never leaves this process's memory, and the client secret,
which never leaves the options. The refresh token -- the thing that lasts --
goes from Google to state/ over TLS and nowhere else. state/ is unreadable to
every tool the model has (policy._SECRET_DIRS).

Why not a device flow: Google's device flow does not grant Calendar or Gmail
scopes. Why not a listening port: the add-on has none on purpose.

The Google Cloud project is still the owner's own: there is no shared client
to borrow, because one serving strangers' Gmail needs Google's security
review. Its client id and secret come from the options, or -- for an install
that once ran `gws auth setup` -- from the client_secret.json that left.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlencode

import httpx

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

# A loopback address is what Google allows a Desktop client to redirect to
# without registering it. Nothing listens here: the phone fails to load it,
# and that failed page is where the owner copies the code from.
REDIRECT = "http://127.0.0.1:53682/"

# What the tools in assistant/gws.py and the mailer in channels.py call, and
# no more. drive.readonly reads the whitelisted folders; drive.file creates
# a Doc; documents edits Docs already there; gmail.send is the routine mail
# to MAIL_TO. openid+email only say which account signed in.
SCOPES: dict[str, str] = {
    "openid": "",
    "email": "",
    "https://www.googleapis.com/auth/calendar.readonly": "reading the calendar",
    "https://www.googleapis.com/auth/calendar.events": "calendar changes",
    "https://www.googleapis.com/auth/gmail.readonly": "reading mail",
    "https://www.googleapis.com/auth/gmail.send": "email routines",
    "https://www.googleapis.com/auth/drive.readonly": "reading Drive",
    "https://www.googleapis.com/auth/drive.file": "creating Docs",
    "https://www.googleapis.com/auth/documents": "editing Docs",
}

CREDENTIALS_VAR = "GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"
_DIR = "google"
_CREDENTIALS = "credentials.json"
_ACCOUNT = "account.json"


def credentials_path(state_dir: Path) -> Path:
    return state_dir / _DIR / _CREDENTIALS


def gws_env(state_dir: Path | None) -> dict[str, str] | None:
    """The environment a `gws` call runs with: inherited, plus the chat
    login when there is one.

    None -- inherit unchanged -- when nobody signed in from the chat, so an
    install still logged in the old way keeps working. Only set when the
    file exists: gws fails outright on a variable naming a missing file
    rather than falling back to its own store.
    """
    if state_dir is None:
        return None
    path = credentials_path(state_dir)
    if not path.is_file():
        return None
    env = dict(os.environ)
    env[CREDENTIALS_VAR] = str(path)
    return env


@dataclass(frozen=True)
class Client:
    client_id: str
    client_secret: str


def find_client(client_id: str, client_secret: str, home: Path | None = None) -> Client | None:
    """The OAuth client: from the options, else from `gws auth setup`.

    Only a Desktop ("installed") client is taken from the file: a web client
    cannot redirect to an unregistered loopback port.
    """
    if client_id and client_secret:
        return Client(client_id, client_secret)
    config_dir = os.environ.get("GOOGLE_WORKSPACE_CLI_CONFIG_DIR")
    base = Path(config_dir) if config_dir else (home or Path.home()) / ".config" / "gws"
    try:
        data = json.loads((base / "client_secret.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    installed = data.get("installed") if isinstance(data, dict) else None
    if not isinstance(installed, dict):
        return None
    cid, secret = installed.get("client_id"), installed.get("client_secret")
    if isinstance(cid, str) and isinstance(secret, str) and cid and secret:
        return Client(cid, secret)
    return None


_PARAM = re.compile(r"(?:^|[?&#])(state|code|error)=([^&#\s]*)")


def _params(text: str) -> dict[str, str]:
    """state, code and error out of whatever was pasted: an address with
    its spaces encoded or decoded, with or without a scheme, after /google
    or inside a sentence. Values never hold a space, so they stop at one."""
    found: dict[str, str] = {}
    for name, value in _PARAM.findall(text.replace(" ", "&").replace("\n", "&")):
        found.setdefault(name, unquote(value))
    return found


def looks_like_redirect(text: str) -> bool:
    """A pasted redirect address, the kind that must not reach the model.

    Anything carrying `state` with `code` or `error`, not only ours and not
    only a clean address: a code on its way back is held from the model and
    memory/raw even when it is stale, mangled or inside a sentence. A bare
    `http://localhost:8123` is left alone.
    """
    found = _params(text)
    return "state" in found and ("code" in found or "error" in found)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _id_token_email(id_token: str) -> str:
    """The signed-in address. Not verified, and need not be: it came
    straight from Google's token endpoint over TLS, which is the case OIDC
    lets a client trust as it stands. Used only to tell the owner which
    account is connected."""
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return str(json.loads(base64.urlsafe_b64decode(payload)).get("email") or "")
    except (IndexError, ValueError):
        return ""


def _write_private(path: Path, payload: dict) -> None:
    """Written whole or not at all, readable by this user alone."""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


SETUP_HINT = (
    "Google is not set up yet: this needs a Google Cloud OAuth client of "
    "your own, made once in the browser. The steps are in the add-on "
    "Documentation tab under \"Google Workspace\". Put its client id and "
    "secret in the add-on options, restart, then send /google again."
)


class GoogleLogin:
    """One sign-in at a time, held in memory. A restart forgets a half-done
    one, which is the right outcome: the owner sends /google again."""

    def __init__(self, state_dir: Path, client: Client | None, *,
                 ttl_seconds: int = 600, clock=time.monotonic,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._dir = state_dir / _DIR
        self._client = client
        self._ttl = ttl_seconds
        self._clock = clock
        self._transport = transport
        self._state = ""
        self._verifier = ""
        self._expires = 0.0

    @property
    def configured(self) -> bool:
        return self._client is not None

    @property
    def connected(self) -> bool:
        return (self._dir / _CREDENTIALS).is_file()

    def _account(self) -> dict:
        try:
            return json.loads((self._dir / _ACCOUNT).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def status(self) -> str:
        if not self.connected:
            if not self.configured:
                return SETUP_HINT
            return "Google is not connected. `/google connect` to sign in."
        account = self._account()
        who = account.get("email") or "an account I could not name"
        since = (account.get("connected_at") or "")[:10]
        missing = account.get("missing") or []
        lines = [f"Google connected as {who}" + (f" since {since}." if since else ".")]
        if missing:
            lines.append("Not granted: " + ", ".join(missing) + ".")
        lines.append("`/google connect` to sign in again, `/google off` to disconnect.")
        return "\n".join(lines)

    def start(self) -> str:
        if self._client is None:
            return SETUP_HINT
        self._state = secrets.token_urlsafe(24)
        self._verifier = secrets.token_urlsafe(64)
        self._expires = self._clock() + self._ttl
        challenge = _b64url(hashlib.sha256(self._verifier.encode("ascii")).digest())
        url = AUTH_URL + "?" + urlencode({
            "client_id": self._client.client_id,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            # offline + consent: a refresh token every time, even for an
            # account that allowed this client before.
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": self._state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        minutes = self._ttl // 60
        return (
            "Sign in to Google:\n\n"
            f"1. Open [this link]({url}) and pick the account.\n"
            "2. If Google warns the app is unverified, tap Advanced, then Go to "
            "(it is your own Cloud project). Allow every box.\n"
            "3. The page that follows will not load. That is expected: copy its "
            "whole address from the address bar and send it here.\n\n"
            f"The link works for {minutes} minutes. I delete the address from "
            "the chat as soon as it arrives."
        )

    async def finish(self, text: str) -> str:
        """Take a pasted redirect address and end the sign-in it belongs to."""
        found = _params(text)
        state = found.get("state", "")
        code = found.get("code", "")
        error = found.get("error", "")

        if not self._state or self._clock() >= self._expires:
            self._clear()
            return "That address belongs to no sign-in I am waiting for. `/google connect` for a fresh link."
        if not hmac.compare_digest(state.encode(), self._state.encode()):
            return ("That address is from an older link. Use the newest one I sent, "
                    "or `/google connect` for a fresh link.")
        if error:
            self._clear()
            if error == "access_denied":
                return "Google says you did not allow it. `/google connect` to try again."
            return f"Google refused: `{error}`. `/google connect` to try again."
        if not code:
            return "No code in that address. Copy the whole address bar and send it again."

        assert self._client is not None  # a state exists only after start()
        verifier = self._verifier
        self._clear()  # single use, whatever happens next
        try:
            async with httpx.AsyncClient(timeout=20.0, transport=self._transport) as http:
                response = await http.post(TOKEN_URL, data={
                    "code": code,
                    "client_id": self._client.client_id,
                    "client_secret": self._client.client_secret,
                    "redirect_uri": REDIRECT,
                    "grant_type": "authorization_code",
                    "code_verifier": verifier,
                })
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("google token exchange failed: %s", type(exc).__name__)
            return "Could not reach Google to finish. `/google connect` to try again."
        if response.status_code != 200 or "error" in body:
            reason = body.get("error_description") or body.get("error") or f"HTTP {response.status_code}"
            log.warning("google token exchange refused: %s", body.get("error"))
            return f"Google refused the code: {reason}. `/google connect` to try again."
        refresh = body.get("refresh_token")
        if not refresh:
            return ("Google sent no lasting token. Remove this app at "
                    "https://myaccount.google.com/permissions, then `/google connect`.")

        granted = set(str(body.get("scope") or "").split())
        missing = [label for scope, label in SCOPES.items() if label and scope not in granted]
        email = _id_token_email(str(body.get("id_token") or ""))
        _write_private(self._dir / _CREDENTIALS, {
            "type": "authorized_user",
            "client_id": self._client.client_id,
            "client_secret": self._client.client_secret,
            "refresh_token": refresh,
        })
        _write_private(self._dir / _ACCOUNT, {
            "email": email,
            "connected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "missing": missing,
        })
        log.warning("google connected as %s", email or "an unnamed account")
        reply = f"Google connected as {email or 'your account'}. Calendar, Gmail and Drive work from your next message."
        if missing:
            reply += ("\n\nYou left some boxes unticked, so these will fail: "
                      + ", ".join(missing) + ". `/google connect` to grant them.")
        return reply

    async def disconnect(self) -> str:
        """Revoke at Google, then forget. Forgets even if Google is
        unreachable: the owner asked for this install to stop using it."""
        path = self._dir / _CREDENTIALS
        try:
            refresh = json.loads(path.read_text(encoding="utf-8")).get("refresh_token", "")
        except FileNotFoundError:
            return "Google is not connected from the chat here: nothing to disconnect."
        except (OSError, ValueError):
            refresh = ""
        revoked = False
        if refresh:
            try:
                async with httpx.AsyncClient(timeout=20.0, transport=self._transport) as http:
                    response = await http.post(REVOKE_URL, data={"token": refresh})
                revoked = response.status_code == 200
            except httpx.HTTPError:
                revoked = False
        for name in (_CREDENTIALS, _ACCOUNT):
            (self._dir / name).unlink(missing_ok=True)
        log.warning("google disconnected (revoked at Google: %s)", revoked)
        if revoked:
            return "Google disconnected and access revoked."
        return ("Google disconnected here, but I could not revoke it at Google: "
                "remove it at https://myaccount.google.com/permissions.")

    def _clear(self) -> None:
        self._state = ""
        self._verifier = ""
        self._expires = 0.0
