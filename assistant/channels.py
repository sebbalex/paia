"""Where a routine's result ends up.

Telegram is the default channel. Mail is for what you don't want to read on
your phone at seven in the morning -- a long report, something to file away.

SMTP is optional: with no configuration the channel does not exist, and a
routine that asks for it fails saying so, instead of making the result vanish
in silence.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import smtplib
import subprocess
import tempfile
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

from .format import to_telegram_html

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    sender: str
    recipient: str
    use_tls: bool = True

    @property
    def configured(self) -> bool:
        return bool(self.host and self.sender and self.recipient)


class GwsMailer:
    """Mail out through the `gws` CLI, as the Google account it is logged in as.

    Transport only: the model has no tool that reaches this, and the recipient
    is the one address from the environment (MAIL_TO). The message is built
    here as MIME -- so charts stay inline -- and handed to Gmail's upload
    endpoint as message/rfc822, from a temporary directory this owns: gws
    takes media only from a path under its working directory.
    """

    def __init__(self, binary: str, recipient: str, timeout: int = 60) -> None:
        self._binary = binary
        self.recipient = recipient
        self._timeout = timeout

    def send(self, message: EmailMessage) -> str:
        with tempfile.TemporaryDirectory(prefix="gws-mail-") as workdir:
            (Path(workdir) / "upload").write_bytes(message.as_bytes())
            done = subprocess.run(
                [self._binary, "gmail", "users", "messages", "send",
                 "--params", '{"userId":"me"}', "--upload", "upload",
                 "--upload-content-type", "message/rfc822", "--format", "json"],
                capture_output=True, text=True, timeout=self._timeout,
                check=False, cwd=workdir,
            )
        if done.returncode != 0:
            detail = (done.stderr or done.stdout or "").strip().splitlines()
            raise RuntimeError(f"gws mail send failed: {detail[-1] if detail else done.returncode}")
        try:
            return str(json.loads(done.stdout).get("id") or "")
        except ValueError:
            return ""


class Delivery:
    """Route a message to the requested channel."""

    def __init__(self, telegram, chat_id: int, smtp: SmtpConfig,
                 mailer: GwsMailer | None = None) -> None:
        self._telegram = telegram
        self._chat_id = chat_id
        self._smtp = smtp
        self._mailer = mailer

    def available(self, channel: str) -> bool:
        if channel == "telegram":
            return True
        if channel == "email":
            return self._mailer is not None or self._smtp.configured
        return False

    async def send(self, channel: str, subject: str, body: str,
                   charts: list | tuple = ()) -> None:
        """`charts` are coach_charts.Chart: inline in mail, an album on Telegram."""
        if channel == "email" and self._mailer is not None:
            try:
                message = build_email(subject, body, None, self._mailer.recipient, charts)
                await asyncio.to_thread(self._mailer.send, message)
                return
            except Exception:
                log.exception("gws mail failed, falling back to Telegram")
                body += "\n\n_(invio email fallito, mandato qui)_"
        elif channel == "email" and self._smtp.configured:
            try:
                await asyncio.to_thread(self._send_email, subject, body, charts)
                return
            except Exception:
                # A report that cannot be mailed still gets read.
                log.exception("email delivery failed, falling back to Telegram")
                body += "\n\n_(invio email fallito, mandato qui)_"
        elif channel == "email":
            # Fall back to Telegram instead of losing the result: a routine
            # that runs and delivers nothing is worse than one that delivers
            # somewhere else.
            body += "\n\n_(SMTP not configured, sent here instead)_"
        await self._telegram.send(self._chat_id, f"**{subject}**\n\n{body}")
        if charts:
            await self._telegram.send_photos(
                self._chat_id,
                [(c.filename, c.png, f"Grafico {i}: {c.title}") for i, c in enumerate(charts, 1)],
            )

    def _send_email(self, subject: str, body: str, charts=()) -> None:
        message = build_email(subject, body, self._smtp.sender, self._smtp.recipient, charts)
        if self._smtp.use_tls:
            with smtplib.SMTP(self._smtp.host, self._smtp.port, timeout=30) as server:
                server.starttls()
                if self._smtp.user:
                    server.login(self._smtp.user, self._smtp.password)
                server.send_message(message)
        else:
            with smtplib.SMTP_SSL(self._smtp.host, self._smtp.port, timeout=30) as server:
                if self._smtp.user:
                    server.login(self._smtp.user, self._smtp.password)
                server.send_message(message)


def build_email(subject: str, body: str, sender: str | None, recipient: str,
                charts=()) -> EmailMessage:
    """Plain text plus HTML, with each chart inline after the text.

    No sender means Gmail fills in the account that sends it (the gws path).
    """
    message = EmailMessage()
    message["Subject"] = subject
    if sender:
        message["From"] = sender
    message["To"] = recipient
    message.set_content(body)
    if not charts:
        return message
    cids = [make_msgid(domain="paia") for _ in charts]
    figures = "".join(
        f'<h3 style="margin:28px 0 8px">Grafico {i}: {html.escape(c.title)}</h3>'
        f'<img src="cid:{cid[1:-1]}" alt="{html.escape(c.title)}" '
        f'style="max-width:100%;height:auto">'
        for i, (c, cid) in enumerate(zip(charts, cids), 1)
    )
    text = to_telegram_html(body).replace("\n", "<br>\n")
    message.add_alternative(
        '<html><body style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;'
        f'font-size:15px;line-height:1.5;color:#0b0b0b;max-width:760px">{text}'
        f"{figures}</body></html>",
        subtype="html",
    )
    html_part = message.get_payload()[1]
    for chart, cid in zip(charts, cids):
        html_part.add_related(chart.png, "image", "png", cid=cid, filename=chart.filename)
    return message
