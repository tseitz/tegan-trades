"""Mailing the digest. Thin on purpose.

**Why SMTP and not the Gmail MCP.** The MCP server is interactively authenticated — it needs a
browser and a person. The nightly runs under launchd with nobody there, so the MCP is not an
option however convenient it looks from a Claude Code session. ``smtplib`` with a Gmail app
password survives a headless run — on a machine that can reach port 587 at all. Most cloud hosts
block outbound SMTP by default (spam prevention), so a droplet has no working SMTP path
regardless of credentials — see ``configure``.

**Email is the mirror, not the primary.** The vault note is written first and a failure here
never rolls it back, the same contract ``oracle.decisions`` states for its own mirror.

**The send path never raises; ``configure`` does, and the caller catches it.** That is the
whole exception surface, stated exactly — the docstring used to claim nothing here raised at
all, which was false in two ways and hid a real one: a malformed port produced a bare
``ValueError`` that no caller caught, killing the digest *after* the vault note was already
written.

## The credential

``DIGEST_SMTP_USER``, ``DIGEST_SMTP_APP_PASSWORD`` and ``DIGEST_TO`` live in ``.env``, which
holds **two unrelated kinds of secret**: the signing key that places orders, and these mail
credentials. Do not assume anything in that file is trading-related.

A Gmail app password is not a Google account password: it is scoped to SMTP, revocable on its
own, and useless for signing in.

## Resend, for a machine SMTP cannot reach

``RESEND_API_KEY`` + ``RESEND_FROM_EMAIL`` send over plain HTTPS instead — the path that works
from a datacenter IP with SMTP ports blocked. ``configure`` picks Resend whenever the key is
set, SMTP otherwise, so a machine with a working SMTP path (a laptop, a residential IP) needs no
change at all.
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.policy import SMTP

import requests

from digest import htmlmail

# RFC 5321's line limit, used as the header-folding width. The default policy folds at 78, and a
# subject carrying "·" gets split into encoded-words at that boundary — one of which encodes a
# lone space. A client unfolds the break to a space and decodes that word to a second one, so
# every long subject arrived with a double space in it. Nothing here can emit a line near 998.
_POLICY = SMTP.clone(max_line_length=998)

# Gmail's submission endpoint. STARTTLS on 587 rather than implicit TLS on 465 — both work,
# and 587 is the one that survives networks that block 465 outright.
DEFAULT_HOST = "smtp.gmail.com"
DEFAULT_PORT = 587

USER = "DIGEST_SMTP_USER"
PASSWORD = "DIGEST_SMTP_APP_PASSWORD"
TO = "DIGEST_TO"
HOST = "DIGEST_SMTP_HOST"
PORT = "DIGEST_SMTP_PORT"

RESEND_API_KEY = "RESEND_API_KEY"
RESEND_FROM_EMAIL = "RESEND_FROM_EMAIL"
RESEND_ENDPOINT = "https://api.resend.com/emails"


class NotConfigured(RuntimeError):
    """A setting is missing or blank. Raised at configure time, deliberately.

    The alternative is a connection error at send time, which reads as a network problem and
    sends you looking in the wrong place for an evening. The message names the variable.
    """


@dataclass(frozen=True, slots=True)
class Config:
    user: str
    password: str
    to: tuple[str, ...]
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    @property
    def sender(self) -> str:
        """Gmail rejects a ``From`` that is not the authenticated account, so this is not a
        preference — anything else authenticates successfully and then bounces."""
        return self.user


@dataclass(frozen=True, slots=True)
class ResendConfig:
    api_key: str
    sender: str
    to: tuple[str, ...]


def _recipients(env, *, required: bool = True) -> tuple[str, ...]:
    raw = (env.get(TO) or "").strip()
    if not raw:
        if required:
            raise NotConfigured(f"{TO} is not set — the digest cannot be emailed.")
        return ()
    recipients = tuple(part.strip() for part in raw.split(",") if part.strip())
    # ``DIGEST_TO=","`` clears the blank check above and still yields no recipients.
    # Caught here rather than at ``send``, because an empty ``To:`` header is rejected by the
    # server — the exact late failure this exception exists to convert into an early one.
    if not recipients:
        raise NotConfigured(f"{TO} names no recipients (got {env.get(TO)!r})")
    return recipients


def _configure_resend(env) -> ResendConfig:
    def _required(name: str) -> str:
        value = (env.get(name) or "").strip()
        if not value:
            raise NotConfigured(
                f"{name} is not set — the digest cannot be emailed via Resend. Set "
                f"{RESEND_API_KEY}, {RESEND_FROM_EMAIL} and {TO} in .env, or run without --email.")
        return value

    return ResendConfig(api_key=_required(RESEND_API_KEY), sender=_required(RESEND_FROM_EMAIL),
                        to=_recipients(env))


def _configure_smtp(env) -> Config:
    def _required(name: str) -> str:
        value = (env.get(name) or "").strip()
        if not value:
            raise NotConfigured(
                f"{name} is not set — the digest cannot be emailed. Set {USER}, {PASSWORD} and "
                f"{TO} in .env, or run without --email.")
        return value

    # Re-raised as ``NotConfigured`` so it joins the one exception the caller catches. As a bare
    # ``ValueError`` this escaped every handler and took down the whole digest run, after the
    # vault note had already been written, for a typo in an optional variable.
    raw_port = env.get(PORT) or DEFAULT_PORT
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise NotConfigured(f"{PORT} must be a number, got {raw_port!r}") from exc

    return Config(user=_required(USER), password=_required(PASSWORD), to=_recipients(env),
                  host=(env.get(HOST) or DEFAULT_HOST).strip(), port=port)


def configure(env) -> Config | ResendConfig:
    """Read the mailer's settings, or say precisely which one is missing.

    Resend whenever ``RESEND_API_KEY`` is set — the path that works from a machine whose
    outbound SMTP is blocked. SMTP otherwise, unchanged for a machine that can already reach
    it. See the module docstring's Resend section.
    """
    if (env.get(RESEND_API_KEY) or "").strip():
        return _configure_resend(env)
    return _configure_smtp(env)


def compose(config: Config, subject: str, body: str) -> EmailMessage:
    """The message. Two parts carrying one string.

    The plain-text part is the rendered digest verbatim, so the terminal, the vault note and the
    inbox still show the same characters and there is still one renderer. The HTML part is that
    same text in a monospace block — see ``htmlmail``, which may restyle and may not restate.
    Sending it as ``multipart/alternative`` means a client that prefers plain text loses nothing.
    """
    message = EmailMessage(policy=_POLICY)
    message["Subject"] = subject
    message["From"] = config.sender
    message["To"] = ", ".join(config.to)
    message.set_content(body)
    message.add_alternative(htmlmail.wrap(body), subtype="html")
    return message


def _smtp_send(config: Config, message: EmailMessage) -> None:
    with smtplib.SMTP(config.host, config.port, timeout=30) as server:
        server.starttls()
        server.login(config.user, config.password)
        server.send_message(message)


def _resend_send(config: ResendConfig, subject: str, text: str, html: str) -> None:
    response = requests.post(
        RESEND_ENDPOINT,
        headers={"Authorization": f"Bearer {config.api_key}"},
        json={"from": config.sender, "to": list(config.to), "subject": subject,
              "text": text, "html": html},
        timeout=30,
    )
    response.raise_for_status()


def send(config: Config | ResendConfig, subject: str, body: str, *,
         transport=None, warn=None) -> bool:
    """Send it. ``True`` if it went. Never raises.

    ``transport`` is injected so the send path is testable without a socket — everything above
    it is pure construction, and this function's only real job is turning an exception into a
    warning. Dispatches on ``config``'s type: ``ResendConfig`` sends plain text/html over HTTPS,
    ``Config`` sends a MIME message over SMTP — see the module docstring's Resend section for why
    two paths exist.
    """
    try:
        if isinstance(config, ResendConfig):
            (transport or _resend_send)(config, subject, body, htmlmail.wrap(body))
        else:
            (transport or _smtp_send)(config, compose(config, subject, body))
    except (OSError, smtplib.SMTPException, requests.RequestException) as exc:
        if warn is not None:
            warn(f"warning: could not email the digest: {exc}")
        return False
    return True
