import json
from datetime import UTC, date, datetime

from llm.auth_expiry import Expiry, read_expiry, verdict

TODAY = date(2026, 11, 12)


def _creds(tmp_path, when: datetime):
    path = tmp_path / ".credentials.json"
    ms = int(when.timestamp() * 1000)
    path.write_text(json.dumps({"claudeAiOauth": {"refreshTokenExpiresAt": ms}}))
    return path


def test_the_login_file_expiry_is_read(tmp_path):
    path = _creds(tmp_path, datetime(2026, 11, 12, 1, 2, tzinfo=UTC))
    assert read_expiry({}, path) == Expiry(date(2026, 11, 12), "login")


def test_a_setup_token_wins_over_the_login_file(tmp_path):
    path = _creds(tmp_path, datetime(2026, 11, 12, tzinfo=UTC))
    env = {"CLAUDE_CODE_OAUTH_TOKEN": "x", "CLAUDE_CODE_OAUTH_TOKEN_EXPIRES": "2027-11-12"}
    assert read_expiry(env, path) == Expiry(date(2027, 11, 12), "token")


def test_a_token_without_its_date_is_flagged_not_trusted(tmp_path):
    found = verdict(read_expiry({"CLAUDE_CODE_OAUTH_TOKEN": "x"}, tmp_path / "none"), TODAY)
    assert found[0] == "warn" and "CLAUDE_CODE_OAUTH_TOKEN_EXPIRES" in found[1]


def test_no_login_at_all_is_flagged(tmp_path):
    assert verdict(read_expiry({}, tmp_path / "none"), TODAY)[0] == "warn"


def test_far_off_is_quiet_two_weeks_warns_three_days_blocks():
    def at(days):
        return verdict(Expiry(date.fromordinal(TODAY.toordinal() + days), "token"), TODAY)
    assert at(15) is None
    assert at(14)[0] == "warn"
    assert at(3)[0] == "block"
    assert at(-1)[0] == "block" and "expired" in at(-1)[1]
