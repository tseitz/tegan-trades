"""Sending the digest.

The transport is thin and boring on purpose. What is worth testing is the configuration edge:
a half-configured mailer must say which piece is missing rather than failing at the socket, and
no failure here may raise into the nightly.
"""
from __future__ import annotations

import pytest
from digest import mail


def _env(**over) -> dict:
    env = {"DIGEST_SMTP_USER": "me@gmail.com",
           "DIGEST_SMTP_APP_PASSWORD": "abcd efgh ijkl mnop",
           "DIGEST_TO": "me@gmail.com"}
    env.update(over)
    return {k: v for k, v in env.items() if v is not None}


# ── configuration ─────────────────────────────────────────────────────────────

def test_a_complete_environment_configures():
    config = mail.configure(_env())
    assert config.user == "me@gmail.com" and config.to == ("me@gmail.com",)


def test_a_missing_setting_is_named_rather_than_failing_at_the_socket():
    """"Connection refused" for a password that was never set costs an evening. The message has
    to name the variable."""
    with pytest.raises(mail.NotConfigured, match="DIGEST_SMTP_APP_PASSWORD"):
        mail.configure(_env(DIGEST_SMTP_APP_PASSWORD=None))


def test_an_empty_setting_counts_as_missing():
    """An exported-but-blank variable is the common shape of a half-finished ``.env``."""
    with pytest.raises(mail.NotConfigured, match="DIGEST_TO"):
        mail.configure(_env(DIGEST_TO="   "))


def test_several_recipients_split_on_commas():
    config = mail.configure(_env(DIGEST_TO="a@x.com, b@y.com"))
    assert config.to == ("a@x.com", "b@y.com")


def test_the_sender_defaults_to_the_authenticating_account():
    """Gmail rejects a ``From`` that is not the authenticated user, so defaulting to anything
    else would produce a send that authenticates and then bounces."""
    assert mail.configure(_env()).sender == "me@gmail.com"


# ── the message ───────────────────────────────────────────────────────────────

def test_the_message_carries_the_subject_and_a_plain_text_body():
    config = mail.configure(_env())
    message = mail.compose(config, "1 at trigger · 1 new", "AT THE TRIGGER\n  HYPE\n")
    assert message["Subject"] == "1 at trigger · 1 new"
    assert message["From"] == "me@gmail.com"
    assert "HYPE" in message.get_content()


def test_the_body_stays_plain_text():
    """One renderer, no drift. The terminal, the vault note and the mail all show the same
    characters — and a plain-text part renders everywhere without a client deciding for us."""
    config = mail.configure(_env())
    assert mail.compose(config, "s", "b").get_content_type() == "text/plain"


# ── failure never reaches the nightly ─────────────────────────────────────────

def test_a_send_failure_warns_and_returns_false(tmp_path):
    """The digest is the last nightly step, after the ore is safe. An SMTP timeout must not
    turn a good night into a failed one — and the note is already filed by this point."""
    def _explode(*_args, **_kwargs):
        raise OSError("connection refused")

    warned: list[str] = []
    sent = mail.send(mail.configure(_env()), "s", "b",
                     transport=_explode, warn=warned.append)
    assert sent is False
    assert warned and "connection refused" in warned[0]


def test_a_successful_send_reports_true():
    sent = []
    assert mail.send(mail.configure(_env()), "s", "b",
                     transport=lambda *a, **k: sent.append(a)) is True
    assert sent


# ── settings that fail late unless they are caught early ─────────────────────

def test_a_recipient_list_that_is_only_commas_is_refused():
    """Clears ``_required``'s non-blank test and still names nobody. An empty ``To:`` header is
    rejected by the server — the late failure this exception exists to convert into an early
    one."""
    with pytest.raises(mail.NotConfigured, match="DIGEST_TO"):
        mail.configure(_env(DIGEST_TO=" , , "))


def test_a_non_numeric_port_is_reported_as_a_configuration_problem():
    """As a bare ``ValueError`` this escaped every handler and took down the whole digest run,
    after the vault note had already been written, for a typo in an optional variable."""
    with pytest.raises(mail.NotConfigured, match="DIGEST_SMTP_PORT"):
        mail.configure(dict(_env(), DIGEST_SMTP_PORT="not-a-port"))


def test_a_numeric_port_is_honoured():
    assert mail.configure(dict(_env(), DIGEST_SMTP_PORT="2525")).port == 2525


def test_the_port_defaults_when_unset():
    assert mail.configure(_env()).port == mail.DEFAULT_PORT
