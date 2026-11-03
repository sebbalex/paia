#!/usr/bin/env python3
"""/selftest: each check's states, masking, timeouts, the mail test, and the
command wiring.

Run: .venv/bin/python tests/test_selftest.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant import selftest as S  # noqa: E402
from assistant.channels import Delivery, SmtpConfig  # noqa: E402
from assistant.gws import GwsError  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILURES.append(f"{name}: {detail}")
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")


def cfg(root: Path, **over):
    base = dict(
        root=root, state_dir=root / "state", routines_dir=root / "routines",
        skills_dir=root / ".claude" / "skills", backend="openrouter",
        model="vendor/model", openrouter_base_url="https://x", openrouter_api_key="k",
        gws_bin="gws", drive_folder_ids=frozenset(), drive_write_folders=(),
        scraper_url="", scraper_token="", skills=(), whisper_model="small",
        smtp=SmtpConfig("", 587, "", "", "", ""),
    )
    base.update(over)
    return SimpleNamespace(**base)


class FakeGws:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    async def call(self, path, params, body=None):
        self.calls.append((path, params))
        answer = self.answers.get(path)
        if callable(answer):
            answer = answer(params)
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeGoogle:
    def __init__(self, missing=None, email="me@example.com", configured=True):
        self.missing = missing
        self.email = email
        self.configured = configured


class FakeIcu:
    def __init__(self, enabled=True, error=None):
        self.enabled = enabled
        self.error = error

    async def ping(self):
        if self.error:
            raise self.error


class FakeTranscriber:
    available = True


def by_name(results):
    return {r.name: r for r in results}


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    (tmp / "state").mkdir()

    print("masking")
    check("mail masked", S.mask("to friend@example.com ok") == "to f***@example.com ok",
          S.mask("to friend@example.com ok"))

    print("google")
    c = cfg(tmp)
    check("off without gws", S.check_google(cfg(tmp, gws_bin=""), None).state == "off")
    r = S.check_google(c, FakeGoogle(missing=[]))
    check("connected ok, address masked", r.state == "ok" and "m***@example.com" in S._clean(r.detail), r.detail)
    r = S.check_google(c, FakeGoogle(missing=["email routines"]))
    check("missing scope warns", r.state == "warn" and "email routines" in r.detail, r.detail)
    r = S.check_google(c, FakeGoogle(missing=None))
    check("no chat sign-in warns", r.state == "warn" and "/google connect" in r.detail, r.detail)

    print("workspace reads")
    gws = FakeGws({
        ("calendar", "calendarList", "list"): {"items": [{}, {}]},
        ("gmail", "users", "messages", "list"): {"messages": []},
        ("drive", "files", "get"): lambda p: (
            {"mimeType": "application/vnd.google-apps.folder"} if p["fileId"] == "good-folder"
            else GwsError("File not found: bad-folder")),
    })
    c = cfg(tmp, drive_folder_ids=frozenset({"good-folder"}), drive_write_folders=("bad-folder-id",))
    cal = asyncio.run(S.check_calendar(c, gws))
    check("calendar counts", cal.state == "ok" and "2 calendars" in cal.detail, cal.detail)
    check("gmail reads one", asyncio.run(S.check_gmail(c, gws)).state == "ok")
    check("gmail asks for one message",
          any(p == ("gmail", "users", "messages", "list") and q.get("maxResults") == 1 for p, q in gws.calls))
    d = asyncio.run(S.check_drive(c, gws))
    check("drive names the bad folder", d.state == "fail" and "1 of 2" in d.detail and "bad-fold" in d.detail, d.detail)
    expired = FakeGws({("drive", "files", "get"): GwsError(
        "Google sign-in expired or was revoked (invalid_grant). Tell the owner to send /google connect")})
    r = asyncio.run(S._guard("drive", S.check_drive(c, expired), 5))[0]
    check("one shared cause said once, short", r.state == "fail" and r.detail.count("expired") == 1
          and "Tell the owner" not in r.detail, r.detail)
    gws_ok = FakeGws({("drive", "files", "list"): {"files": []}})
    d = asyncio.run(S.check_drive(cfg(tmp), gws_ok))
    check("drive with no whitelist warns", d.state == "warn" and "whole Drive" in d.detail, d.detail)
    check("drive only reads", all(p[-1] in ("get", "list") for p, _ in gws.calls + gws_ok.calls))

    print("intervals")
    check("intervals off", asyncio.run(S.check_intervals(c, FakeIcu(enabled=False))).state == "off")
    check("intervals ok", asyncio.run(S.check_intervals(c, FakeIcu())).state == "ok")

    print("mail")
    d_none = Delivery(None, 1, SmtpConfig("", 587, "", "", "", ""))
    check("no route is off", asyncio.run(S.check_mail(c, d_none, None)).state == "off")
    smtp = SmtpConfig("smtp.example.com", 587, "u", "p", "bot@example.com", "friend@example.com")
    d_smtp = Delivery(None, 1, smtp)
    logins = []
    d_smtp.smtp_login = lambda: logins.append(1)
    r = asyncio.run(S.check_mail(cfg(tmp, smtp=smtp), d_smtp, None))
    check("smtp logs in, sends nothing", r.state == "ok" and logins == [1] and "f***@example.com" in r.detail, r.detail)

    def refuse():
        raise OSError("(535, b'5.7.8 Username and Password not accepted')")
    d_smtp.smtp_login = refuse
    results = asyncio.run(S._guard("mail", S.check_mail(cfg(tmp, smtp=smtp), d_smtp, None), 5))
    check("smtp refusal is a fail with the reason",
          results[0].state == "fail" and "not accepted" in results[0].detail, results[0].detail)
    mailer = SimpleNamespace(recipient="friend@example.com", send=lambda m: "id")
    d_gws = Delivery(None, 1, SmtpConfig("", 587, "", "", "", ""), mailer)
    r = asyncio.run(S.check_mail(c, d_gws, FakeGoogle(missing=["email routines"])))
    check("gws without gmail.send fails", r.state == "fail" and "gmail.send" in r.detail, r.detail)
    r = asyncio.run(S.check_mail(c, d_gws, FakeGoogle(missing=None)))
    check("gws with no chat login warns", r.state == "warn", r.detail)

    print("mail test")
    sent = []
    d_gws_live = Delivery(None, 1, SmtpConfig("", 587, "", "", "", ""),
                          SimpleNamespace(recipient="friend@example.com", send=lambda m: sent.append(m)))
    out = asyncio.run(S.mail_test(d_gws_live))
    check("test mail goes to MAIL_TO", len(sent) == 1 and sent[0]["To"] == "friend@example.com", out)
    check("reply masks the address", "f***@example.com" in out and "friend@" not in out, out)

    def broken(m):
        raise RuntimeError("gws mail send failed: insufficient scope for friend@example.com")
    out = asyncio.run(S.mail_test(Delivery(None, 1, SmtpConfig("", 587, "", "", "", ""),
                                           SimpleNamespace(recipient="friend@example.com", send=broken))))
    check("failed test mail says why, no fallback", "FAILED" in out and "insufficient scope" in out
          and "friend@" not in out, out)
    check("no route says so", "No mail route" in asyncio.run(S.mail_test(d_none)))

    print("routines and skills")
    rd = tmp / "routines"
    rd.mkdir()
    (rd / "good.md").write_text("---\nname: good\nschedule: 0 7 * * *\nenabled: false\n---\nhi\n")
    (rd / "broken.md").write_text("no frontmatter\n")
    (rd / "mailer.md").write_text("---\nname: mailer\nschedule: 0 7 * * *\nenabled: true\nchannel: email\n---\nhi\n")
    r = S.check_routines(c, d_none)
    check("malformed named", "`broken.md` malformed" in r.detail, r.detail)
    check("enabled email without route named", r.state == "fail" and "`mailer` wants `email`" in r.detail, r.detail)
    (tmp / ".claude" / "skills" / "coach").mkdir(parents=True)
    (tmp / ".claude" / "skills" / "coach" / "SKILL.md").write_text("x")
    r = S.check_skills(cfg(tmp, skills=("coach", "ghost")))
    check("missing skill named", r.state == "fail" and "`ghost`" in r.detail, r.detail)
    check("skills ok", S.check_skills(cfg(tmp, skills=("coach",))).state == "ok")

    print("claude-mem observer")
    health = tmp / "observer-health.json"
    check("no health file is ok", S.observer_health(health).state == "ok")
    health.write_text('{"consecutiveFailures": 0, "failingSinceAt": null}')
    check("recording is ok", S.observer_health(health).state == "ok")
    health.write_text('{"consecutiveFailures": 223, "failingSinceAt": 1793185164187,'
                      ' "lastErrorMessage": "Claude Code could not authenticate the observer"}')
    r = S.observer_health(health)
    check("failing observer fails", r.state == "fail" and "223 in a row" in r.detail
          and "could not authenticate" in r.detail, r.detail)

    print("runner")

    async def slow():
        await asyncio.sleep(5)
    r = asyncio.run(S._guard("slow", slow(), 0.05))[0]
    check("timeout becomes a fail", r.state == "fail" and "no answer within" in r.detail, r.detail)

    async def boom():
        raise RuntimeError("model said 401 for friend@example.com")
    c2 = cfg(tmp)
    results = by_name(asyncio.run(S.run_all(
        c2, d_none, gws=None, icu=FakeIcu(enabled=False), transcriber=FakeTranscriber(),
        google=None, model_probe=boom)))
    check("every check reports", set(results) >= {
        "model", "google", "calendar", "gmail", "drive", "intervals", "scraper",
        "mail", "voice", "claude-mem", "routines", "skills"}, sorted(results))
    check("model failure isolated", results["model"].state == "fail"
          and results["voice"].state == "ok", str(results["model"]))
    check("model error masked", "friend@" not in results["model"].detail, results["model"].detail)
    skipped = by_name(asyncio.run(S.run_all(c2, d_none, icu=FakeIcu(enabled=False),
                                            probe_model=False, model_probe=boom)))
    check("startup run spends no tokens", skipped["model"].state == "off")
    text = S.render(list(results.values()))
    check("render has a header and a line each", text.startswith("Self-test:")
          and text.count("\n") >= len(results), text)
    check("summary is one line", "\n" not in S.summary(list(results.values())))

    print("command")
    from assistant.main import COMMANDS, _command
    from assistant.telegram import Incoming

    class Tg:
        def __init__(self):
            self.sent = []

        async def send(self, chat, text):
            self.sent.append(text)

    async def tester(argument="", probe_model=True):
        return f"ran:{argument}"

    async def go(text):
        tg = Tg()
        inc = Incoming(chat_id=1, user_id=7, text=text, message_id=1)
        handled = await _command(SimpleNamespace(root=tmp), tg, None, d_none, inc, None, tester)
        await asyncio.sleep(0.01)  # the background task
        return handled, tg.sent

    handled, sent = asyncio.run(go("/selftest"))
    check("handled in python", handled and sent[-1] == "ran:", str(sent))
    handled, sent = asyncio.run(go("/selftest mail"))
    check("mail argument passed", sent[-1] == "ran:mail", str(sent))
    handled, sent = asyncio.run(go("/selftest nonsense"))
    check("bad argument explained", handled and "`/selftest mail`" in sent[-1], str(sent))
    check("in the menu", any(n == "selftest" for n, _ in COMMANDS))

    if FAILURES:
        print("\n".join(FAILURES))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
