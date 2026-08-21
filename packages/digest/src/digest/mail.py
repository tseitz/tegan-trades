"""Mailing the digest. Thin on purpose.

**Why SMTP and not the Gmail MCP.** The MCP server is interactively authenticated — it needs a
browser and a person. The nightly runs under launchd with nobody there, so the MCP is not an
option however convenient it looks from a Claude Code session. ``smtplib`` with a Gmail app
password is the only path that survives a headless run.

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
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

# Gmail's submission endpoint. STARTTLS on 587 rather than implicit TLS on 465 — both work,
# and 587 is the one that survives networks that block 465 outright.
DEFAULT_HOST = "smtp.gmail.com"
DEFAULT_PORT = 587

USER = "DIGEST_SMTP_USER"
PASSWORD = "DIGEST_SMTP_APP_PASSWORD"
TO = "DIGEST_TO"
HOST = "DIGEST_SMTP_HOST"
PORT = "DIGEST_SMTP_PORT"


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


def configure(env) -> Config:
    """Read the mailer's settings, or say precisely which one is missing."""
    def _required(name: str) -> str:
        value = (env.get(name) or "").strip()
        if not value:
            raise NotConfigured(
                f"{name} is not set — the digest cannot be emailed. Set {USER}, {PASSWORD} and "
                f"{TO} in .env, or run without --email.")
        return value

    recipients = tuple(part.strip() for part in _required(TO).split(",") if part.strip())
    # ``DIGEST_TO=","`` clears ``_required``'s non-blank test and still yields no recipients.
    # Caught here rather than at ``send``, because an empty ``To:`` header is rejected by the
    # server — the exact late failure this exception exists to convert into an early one.
    if not recipients:
        raise NotConfigured(f"{TO} names no recipients (got {env.get(TO)!r})")

    # Re-raised as ``NotConfigured`` so it joins the one exception the caller catches. As a bare
    # ``ValueError`` this escaped every handler and took down the whole digest run, after the
    # vault note had already been written, for a typo in an optional variable.
    raw_port = env.get(PORT) or DEFAULT_PORT
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise NotConfigured(f"{PORT} must be a number, got {raw_port!r}") from exc

    return Config(user=_required(USER), password=_required(PASSWORD), to=recipients,
                  host=(env.get(HOST) or DEFAULT_HOST).strip(), port=port)


def compose(config: Config, subject: str, body: str) -> EmailMessage:
    """The message. Plain text, one part.

    Plain text is the whole point of rendering the digest as plain text in the first place: the
    terminal, the vault note and the inbox show the same characters, so there is one renderer
    and nothing that can drift between them.
    """
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.sender
    message["To"] = ", ".join(config.to)
    message.set_content(body)
    return message


def _smtp_send(config: Config, message: EmailMessage) -> None:
    with smtplib.SMTP(config.host, config.port, timeout=30) as server:
        server.starttls()
        server.login(config.user, config.password)
        server.send_message(message)


def send(config: Config, subject: str, body: str, *, transport=_smtp_send, warn=None) -> bool:
    """Send it. ``True`` if it went. Never raises.

    ``transport`` is injected so the send path is testable without a socket — everything above
    it is pure construction, and this function's only real job is turning an exception into a
    warning.
    """
    try:
        transport(config, compose(config, subject, body))
    except (OSError, smtplib.SMTPException) as exc:
        if warn is not None:
            warn(f"warning: could not email the digest: {exc}")
        return False
    return True
