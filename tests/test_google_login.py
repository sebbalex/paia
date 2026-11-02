#!/usr/bin/env python3
"""Google sign-in from the chat: /google and the pasted redirect address.

Run: .venv/bin/python tests/test_google_login.py
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from assistant import google_login as G  # noqa: E402
from assistant import gws as W  # noqa: E402
from assistant import main as M  # noqa: E402
from assistant.agent import Turn  # noqa: E402
from assistant.telegram import Incoming  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILURES.append(f"{name}: {detail}")
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")


CLIENT = G.Client("1234-abc.apps.googleusercontent.com", "s3cret")
ALL_SCOPES = " ".join(G.SCOPES)


def id_token(email: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).rstrip(b"=")
    return f"h.{payload.decode()}.sig"


class Google:
    """A token and revoke endpoint that records what it was sent."""

    def __init__(self, token_reply: dict | None = None, status: int = 200,
                 revoke_status: int = 200) -> None:
        self.token_reply = token_reply if token_reply is not None else {
            "access_token": "ya29.x", "refresh_token": "1//refresh",
            "scope": ALL_SCOPES, "id_token": id_token("owner@example.com"),
        }
        self.status = status
        self.revoke_status = revoke_status
        self.requests: list[tuple[str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.requests.append((str(request.url), form))
        if str(request.url) == G.REVOKE_URL:
            return httpx.Response(self.revoke_status, json={})
        return httpx.Response(self.status, json=self.token_reply)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def redirect_for(start_message: str, **extra: str) -> str:
    """What the phone's address bar shows after the owner allows."""
    url = start_message.split("](", 1)[1].split(")", 1)[0]
    state = parse_qs(urlsplit(url).query)["state"][0]
    params = {"state": state, "code": "4/0Abc", "scope": ALL_SCOPES, **extra}
    if "error" in extra:
        params.pop("code")
    return G.REDIRECT + "?" + urlencode(params)


# -- pieces ------------------------------------------------------------------


def test_find_client() -> None:
    print("find_client")
    check("options win", G.find_client("a.apps.googleusercontent.com", "b") ==
          G.Client("a.apps.googleusercontent.com", "b"))
    with tempfile.TemporaryDirectory() as t:
        home = Path(t)
        old = os.environ.pop("GOOGLE_WORKSPACE_CLI_CONFIG_DIR", None)
        try:
            check("nothing anywhere: None", G.find_client("", "", home) is None)
            (home / ".config" / "gws").mkdir(parents=True)
            secret = home / ".config" / "gws" / "client_secret.json"
            secret.write_text(json.dumps({"web": {"client_id": "w", "client_secret": "x"}}))
            check("a web client is not taken", G.find_client("", "", home) is None)
            secret.write_text(json.dumps({"installed": {"client_id": "i", "client_secret": "j"}}))
            check("gws auth setup's Desktop client is taken",
                  G.find_client("", "", home) == G.Client("i", "j"))
            check("only an id in the options: falls back to the file",
                  G.find_client("a", "", home) == G.Client("i", "j"))
            secret.write_text("{not json")
            check("a broken file: None", G.find_client("", "", home) is None)
        finally:
            if old is not None:
                os.environ["GOOGLE_WORKSPACE_CLI_CONFIG_DIR"] = old


def test_gws_env() -> None:
    print("\ngws_env")
    with tempfile.TemporaryDirectory() as t:
        state = Path(t)
        check("no state dir: inherit", G.gws_env(None) is None)
        check("no sign-in: inherit, so an old gws login keeps working",
              G.gws_env(state) is None)
        path = G.credentials_path(state)
        path.parent.mkdir(parents=True)
        path.write_text("{}")
        env = G.gws_env(state)
        check("signed in: the file is named", env is not None
              and env[G.CREDENTIALS_VAR] == str(path))
        check("and the rest of the environment is kept", env is not None
              and env.get("PATH") == os.environ.get("PATH"))


def test_looks_like_redirect() -> None:
    print("\nlooks_like_redirect")
    yes = (
        "http://127.0.0.1:53682/?state=abc&code=4/0x&scope=email",
        "127.0.0.1:53682/?state=abc&code=4/0x",
        "  http://localhost/?code=1&state=2  ",
        "/google http://127.0.0.1:53682/?state=a&code=b",
        "http://127.0.0.1:53682/?state=a&error=access_denied",
        "http://127.0.0.1:53682/?state=a&code=4/0x&scope=email https://www.googleapis.com/auth/drive",
        "here it is http://127.0.0.1/?state=a&code=b",
    )
    no = (
        "", "hello", "http://localhost:8123", "http://127.0.0.1:53682/",
        "https://example.com/?code=1", "/google", "/google connect",
        "the state of the code", "statement=1&decode=2",
    )
    for text in yes:
        check(f"caught: {text[:40]!r}", G.looks_like_redirect(text))
    for text in no:
        check(f"left alone: {text[:40]!r}", not G.looks_like_redirect(text))


# -- the sign-in -------------------------------------------------------------


def test_start() -> None:
    print("\nstart")
    with tempfile.TemporaryDirectory() as t:
        unset = G.GoogleLogin(Path(t), None)
        check("no client: setup steps, no link", unset.start() == G.SETUP_HINT)
        check("no client: status says the same", unset.status() == G.SETUP_HINT)

        login = G.GoogleLogin(Path(t), CLIENT)
        message = login.start()
        url = message.split("](", 1)[1].split(")", 1)[0]
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        check("Google's endpoint", url.startswith(G.AUTH_URL + "?"))
        check("our client", q["client_id"] == CLIENT.client_id)
        check("the loopback redirect", q["redirect_uri"] == G.REDIRECT)
        check("every scope", q["scope"].split() == list(G.SCOPES))
        check("offline + consent: a refresh token every time",
              q["access_type"] == "offline" and q["prompt"] == "consent")
        check("PKCE S256", q["code_challenge_method"] == "S256"
              and q["code_challenge"] == base64.urlsafe_b64encode(
                  hashlib.sha256(login._verifier.encode()).digest()).rstrip(b"=").decode())
        check("state is ours", q["state"] == login._state and len(login._state) >= 24)
        check("the secret is not in the link", CLIENT.client_secret not in message)
        check("the verifier is not in the link", login._verifier not in message)
        first = login._state
        login.start()
        check("a second /google mints a new state", login._state != first)


async def test_finish_ok() -> None:
    print("\nfinish: success")
    with tempfile.TemporaryDirectory() as t:
        state_dir = Path(t)
        google = Google()
        login = G.GoogleLogin(state_dir, CLIENT, transport=google.transport())
        verifier = None
        start = login.start()
        verifier = login._verifier
        reply = await login.finish(redirect_for(start))
        check("says which account", "owner@example.com" in reply, reply)
        url, form = google.requests[0]
        check("posted to the token endpoint", url == G.TOKEN_URL)
        check("with the code, verifier, secret and same redirect",
              form.get("code") == "4/0Abc" and form.get("code_verifier") == verifier
              and form.get("client_secret") == CLIENT.client_secret
              and form.get("redirect_uri") == G.REDIRECT
              and form.get("grant_type") == "authorization_code", str(form))
        path = G.credentials_path(state_dir)
        saved = json.loads(path.read_text())
        check("authorized_user, the shape gws reads",
              saved == {"type": "authorized_user", "client_id": CLIENT.client_id,
                        "client_secret": CLIENT.client_secret,
                        "refresh_token": "1//refresh"}, str(saved))
        check("the file is 0600", stat.S_IMODE(path.stat().st_mode) == 0o600,
              oct(path.stat().st_mode))
        check("its folder is 0700", stat.S_IMODE(path.parent.stat().st_mode) == 0o700)
        check("no temp file left", not list(path.parent.glob("*.tmp")))
        check("connected", login.connected)
        status = login.status()
        check("status names the account", "owner@example.com" in status, status)
        again = await login.finish(redirect_for(start))
        check("the same address twice: refused, single use",
              "no sign-in I am waiting for" in again, again)
        check("and Google is not asked twice", len(google.requests) == 1)


async def test_finish_partial() -> None:
    print("\nfinish: boxes left unticked")
    with tempfile.TemporaryDirectory() as t:
        google = Google({"refresh_token": "r", "id_token": id_token("o@x.it"),
                         "scope": "openid email https://www.googleapis.com/auth/calendar.readonly"})
        login = G.GoogleLogin(Path(t), CLIENT, transport=google.transport())
        reply = await login.finish(redirect_for(login.start()))
        check("still saved", login.connected)
        check("names what will fail", "reading mail" in reply and "editing Docs" in reply
              and "reading the calendar" not in reply.split("fail:")[1], reply)
        check("status repeats it", "Not granted:" in login.status())


async def test_finish_refusals() -> None:
    print("\nfinish: refusals")
    with tempfile.TemporaryDirectory() as t:
        state_dir = Path(t)
        google = Google()
        clock = Clock()
        login = G.GoogleLogin(state_dir, CLIENT, clock=clock, transport=google.transport())

        reply = await login.finish(G.REDIRECT + "?state=x&code=y")
        check("nothing pending", "no sign-in I am waiting for" in reply, reply)

        start = login.start()
        reply = await login.finish(G.REDIRECT + "?state=forged&code=y")
        check("wrong state", "older link" in reply, reply)
        check("wrong state does not burn the live one", login._state != "")

        reply = await login.finish(redirect_for(start, error="access_denied"))
        check("denied at Google", "did not allow" in reply, reply)

        start = login.start()
        clock.now += 601
        reply = await login.finish(redirect_for(start))
        check("expired", "no sign-in I am waiting for" in reply, reply)

        bad = Google({"error": "invalid_grant", "error_description": "Bad Request"}, status=400)
        login = G.GoogleLogin(state_dir, CLIENT, transport=bad.transport())
        reply = await login.finish(redirect_for(login.start()))
        check("Google refuses the code", "Bad Request" in reply, reply)
        check("refused: nothing written", not login.connected)

        none = Google({"access_token": "a", "scope": ALL_SCOPES})
        login = G.GoogleLogin(state_dir, CLIENT, transport=none.transport())
        reply = await login.finish(redirect_for(login.start()))
        check("no refresh token: says how to fix", "myaccount.google.com/permissions" in reply)
        check("no refresh token: nothing written", not login.connected)

        def down(request):
            raise httpx.ConnectError("down")
        login = G.GoogleLogin(state_dir, CLIENT, transport=httpx.MockTransport(down))
        reply = await login.finish(redirect_for(login.start()))
        check("Google unreachable", "Could not reach Google" in reply, reply)


async def test_disconnect() -> None:
    print("\ndisconnect")
    with tempfile.TemporaryDirectory() as t:
        google = Google()
        login = G.GoogleLogin(Path(t), CLIENT, transport=google.transport())
        reply = await login.disconnect()
        check("nothing to disconnect", "nothing to disconnect" in reply, reply)
        await login.finish(redirect_for(login.start()))
        reply = await login.disconnect()
        check("revoked", "revoked" in reply and google.requests[-1] ==
              (G.REVOKE_URL, {"token": "1//refresh"}), str(google.requests[-1]))
        check("forgotten", not login.connected and not list(Path(t, "google").glob("*.json")))

        failing = Google(revoke_status=400)
        login = G.GoogleLogin(Path(t), CLIENT, transport=failing.transport())
        await login.finish(redirect_for(login.start()))
        reply = await login.disconnect()
        check("revoke failed: still forgotten, says where to finish",
              not login.connected and "myaccount.google.com" in reply, reply)


# -- gws ---------------------------------------------------------------------


def test_gws_errors_and_env() -> None:
    print("\ngws")
    def done(stderr: str) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess([], 2, "", stderr)
    msg = str(W._failure(done("error[auth]: Access denied. No credentials provided. Run `gws auth login`")))
    check("no credentials: points at /google, not gws auth login",
          "/google" in msg and "gws auth login" not in msg, msg)
    msg = str(W._failure(done("Failed to get token: invalid_grant: Token has been expired or revoked.")))
    check("invalid_grant: points at /google connect", "/google connect" in msg, msg)
    msg = str(W._failure(done("first\nerror[api]: 404 not found")))
    check("anything else: last line as before", msg == "error[api]: 404 not found", msg)

    with tempfile.TemporaryDirectory() as t:
        state_dir = Path(t)
        seen: list = []
        real = subprocess.run

        def fake(argv, **kwargs):
            seen.append(kwargs.get("env"))
            return subprocess.CompletedProcess(argv, 0, "{}", "")
        W.subprocess.run = fake
        try:
            gws = W.Gws("gws", 5, state_dir)
            gws._call(("calendar", "calendarList", "list"), {})
            check("not signed in from chat: env inherited", seen[-1] is None)
            path = G.credentials_path(state_dir)
            path.parent.mkdir(parents=True)
            path.write_text("{}")
            gws._call(("calendar", "calendarList", "list"), {})
            check("signed in: the next call carries the file, no restart",
                  seen[-1] and seen[-1].get(G.CREDENTIALS_VAR) == str(path))
        finally:
            W.subprocess.run = real


# -- the transport -----------------------------------------------------------


class Cfg:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.raw_memory_dir = root / "memory" / "raw"
        self.state_dir = root / "state"
        self.routines_dir = root / "routines"
        self.context_target = None


class Tg:
    def __init__(self, incoming: list[Incoming], can_delete: bool = True) -> None:
        self.incoming = incoming
        self.sent: list[str] = []
        self.deleted: list[tuple[int, int]] = []
        self.can_delete = can_delete

    async def poll(self):
        for item in self.incoming:
            yield item

    async def send(self, chat_id: int, text: str) -> None:
        self.sent.append(text)

    async def typing(self, chat_id: int) -> None:
        pass

    async def delete(self, chat_id: int, message_id: int) -> bool:
        self.deleted.append((chat_id, message_id))
        return self.can_delete


class Brain:
    def __init__(self) -> None:
        self.asked: list[str] = []

    async def ask(self, prompt: str, **_) -> Turn:
        self.asked.append(prompt)
        return Turn("ok", True)


class Delivery:
    def set_chat(self, chat_id: int) -> None:
        pass

    def available(self, channel: str) -> bool:
        return True


def said(text: str, message_id: int = 7) -> Incoming:
    return Incoming(chat_id=42, user_id=42, text=text, message_id=message_id)


async def test_pump() -> None:
    print("\ntransport")
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        cfg = Cfg(root)
        google = Google()
        login = G.GoogleLogin(cfg.state_dir, CLIENT, transport=google.transport())

        tg = Tg([said("/google")])
        brain = Brain()
        await M._pump(cfg, tg, brain, None, Delivery(), login)
        check("/google, configured and not connected: the link", "Sign in to Google" in tg.sent[0])
        check("the model is not asked", brain.asked == [])

        pasted = redirect_for(tg.sent[0])
        tg = Tg([said(pasted, 99), said("hi")])
        await M._pump(cfg, tg, brain, None, Delivery(), login)
        check("the address is deleted from the chat", tg.deleted == [(42, 99)])
        check("connected reply", "Google connected as owner@example.com" in tg.sent[0], tg.sent[0])
        check("the model saw only the next message", brain.asked == ["hi"], str(brain.asked))
        raw = "".join(p.read_text() for p in cfg.raw_memory_dir.glob("*.md"))
        check("the code is not in memory/raw", "4/0Abc" not in raw and "state=" not in raw)

        tg = Tg([said("/google")])
        await M._pump(cfg, tg, brain, None, Delivery(), login)
        check("/google when connected: the state", "connected as owner@example.com" in tg.sent[0])

        tg = Tg([said(G.REDIRECT + "?state=old&code=stale", 5)], can_delete=False)
        await M._pump(cfg, tg, brain, None, Delivery(), login)
        check("a stale address is still held from the model", brain.asked == ["hi"])
        check("and the owner is told to delete it", "delete it yourself" in tg.sent[0], tg.sent[0])

        tg = Tg([said("/google off")])
        await M._pump(cfg, tg, brain, None, Delivery(), login)
        check("/google off disconnects", "disconnected" in tg.sent[0] and not login.connected)

        tg = Tg([said("/google sideways")])
        await M._pump(cfg, tg, brain, None, Delivery(), login)
        check("unknown argument: usage", "/google connect" in tg.sent[0])

        unset = G.GoogleLogin(cfg.state_dir, None)
        tg = Tg([said("/google")])
        await M._pump(cfg, tg, brain, None, Delivery(), unset)
        check("no client: the setup hint", tg.sent[0] == G.SETUP_HINT)

    check("/google is in the menu", any(name == "google" for name, _ in M.COMMANDS))


async def main() -> None:
    test_find_client()
    test_gws_env()
    test_looks_like_redirect()
    test_start()
    await test_finish_ok()
    await test_finish_partial()
    await test_finish_refusals()
    await test_disconnect()
    test_gws_errors_and_env()
    await test_pump()


if __name__ == "__main__":
    asyncio.run(main())
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILED")
        for line in FAILURES:
            print("  " + line)
        sys.exit(1)
    print("\nall passed")
