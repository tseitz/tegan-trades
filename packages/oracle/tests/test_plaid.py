"""The pure half of the Plaid adapter: what a response becomes, and what gets written.

Nothing here touches the network. The calls that do are one-liners over ``urllib``; what is
worth testing is the mapping — a cash row that must never become a position, a security with
no ticker that must be named rather than dropped, and a file rewrite that must not eat the
settings above ``positions:``.
"""
from __future__ import annotations

import pytest
from oracle import plaid


def _payload(*holdings, securities=(), accounts=()):
    return {
        "holdings": list(holdings),
        "securities": list(securities),
        "accounts": list(accounts),
    }


def _security(sid, ticker, *, kind="equity", name="A Thing"):
    return {"security_id": sid, "ticker_symbol": ticker, "type": kind, "name": name}


def _holding(sid, quantity, *, account="acct", basis=None):
    return {"security_id": sid, "account_id": account, "quantity": quantity,
            "cost_basis": basis}


def test_a_plain_equity_becomes_a_row():
    rows, skipped, _ = plaid.rows_from(_payload(
        _holding("s1", 42.5, basis=8942.0),
        securities=[_security("s1", "vti")],
    ))
    assert skipped == ()
    assert len(rows) == 1
    assert rows[0].ticker == "VTI"          # upcased, matching what the file reader expects
    assert rows[0].shares == 42.5
    assert rows[0].cost == pytest.approx(8942.0 / 42.5)
    assert rows[0].domain == "stock"


def test_crypto_carries_the_domain_routing_needs():
    rows, _, _ = plaid.rows_from(_payload(
        _holding("s1", 0.5),
        securities=[_security("s1", "BTC", kind="cryptocurrency")],
    ))
    assert rows[0].domain == "crypto"


@pytest.mark.parametrize("security", [
    _security("s1", "USD", kind="cash"),
    _security("s1", "CUR:USD", kind="equity"),   # some institutions mistype it
])
def test_cash_never_becomes_a_position(security):
    """Buying power has no chart. A cash row would sit in the review forever with no roster
    view and no level, which is noise dressed as a holding."""
    rows, skipped, _ = plaid.rows_from(_payload(_holding("s1", 174.58), securities=[security]))
    assert rows == ()
    assert skipped == ()          # not a problem, so not reported as one


def test_a_security_with_no_ticker_is_named_not_dropped():
    rows, skipped, _ = plaid.rows_from(_payload(
        _holding("s1", 3.0),
        securities=[{"security_id": "s1", "type": "mutual fund", "name": "House Fund"}],
    ))
    assert rows == ()
    assert len(skipped) == 1
    assert "House Fund" in skipped[0].what


def test_a_nonsense_quantity_is_reported_rather_than_sized_at_nothing():
    _, skipped, _ = plaid.rows_from(_payload(
        _holding("s1", None), securities=[_security("s1", "VTI")]))
    assert len(skipped) == 1 and "VTI" in skipped[0].what


def test_the_same_ticker_in_two_accounts_is_summed():
    """The file allows one row per ticker, so two sleeves of the same fund have to merge or
    `portfolios.load` refuses the whole account."""
    rows, _, accounts = plaid.rows_from(_payload(
        _holding("s1", 10.0, account="roth", basis=1000.0),
        _holding("s1", 5.0, account="taxable", basis=1000.0),
        securities=[_security("s1", "VTI")],
        accounts=[{"account_id": "roth", "name": "Roth", "mask": "1111"},
                  {"account_id": "taxable", "name": "Taxable", "mask": "2222"}],
    ))
    assert len(rows) == 1
    assert rows[0].shares == 15.0
    assert rows[0].cost == pytest.approx(2000.0 / 15.0)
    assert len(accounts) == 2


def test_one_missing_basis_costs_the_whole_ticker_its_cost():
    """A blend drawn from half the shares is a wrong average, which is worse than none."""
    rows, _, _ = plaid.rows_from(_payload(
        _holding("s1", 10.0, account="a", basis=1000.0),
        _holding("s1", 5.0, account="b", basis=None),
        securities=[_security("s1", "VTI")],
    ))
    assert rows[0].cost is None


def test_naming_accounts_narrows_to_them():
    rows, _, accounts = plaid.rows_from(_payload(
        _holding("s1", 10.0, account="roth"),
        _holding("s2", 5.0, account="taxable"),
        securities=[_security("s1", "VTI"), _security("s2", "QQQ")],
        accounts=[{"account_id": "roth", "name": "Roth", "mask": "1111"},
                  {"account_id": "taxable", "name": "Taxable", "mask": "2222"}],
    ), accounts=("roth",))
    assert [r.ticker for r in rows] == ["VTI"]
    assert len(accounts) == 1


def test_env_key_is_stable_for_an_awkward_name():
    assert plaid.env_key("retirement") == "PLAID_ACCESS_TOKEN_RETIREMENT"
    assert plaid.env_key("roth-ira 2") == "PLAID_ACCESS_TOKEN_ROTH_IRA_2"


def test_a_token_is_never_written_twice(tmp_path):
    env = tmp_path / ".env"
    env.write_text("PLAID_ACCESS_TOKEN_RETIREMENT=first\n", encoding="utf-8")
    said = plaid.remember_token("retirement", "second", env=env)
    assert "already set" in said
    assert "second" not in env.read_text(encoding="utf-8")


def test_a_new_token_is_appended_without_disturbing_the_rest(tmp_path):
    env = tmp_path / ".env"
    env.write_text("HL_PRIVATE_KEY=abc", encoding="utf-8")   # no trailing newline on purpose
    plaid.remember_token("retirement", "tok-123", env=env)
    text = env.read_text(encoding="utf-8")
    assert "HL_PRIVATE_KEY=abc" in text
    assert "PLAID_ACCESS_TOKEN_RETIREMENT=tok-123" in text


# ── cash, and which accounts count as cash ──

def _account(aid, *, kind="investment", available=100.0, name="An Account"):
    return {"account_id": aid, "type": kind, "name": name, "mask": "1111",
            "balances": {"available": available, "current": 9999.0}}


def test_cash_is_what_you_could_deploy_not_what_the_account_is_worth():
    """`current` on a brokerage account is every security in it. Reporting that as cash would
    tell a $115k retirement book it has $115k to spend."""
    total, _ = plaid.cash_from(_payload(accounts=[_account("a", available=3379.57)]))
    assert total == 3379.57


def test_a_savings_account_reached_by_the_same_login_is_not_buying_power():
    """One SoFi connection arrives with Checking, Savings and Self-directed together. Only the
    last one holds money that can enter a position."""
    total, _ = plaid.cash_from(_payload(accounts=[
        _account("brokerage", available=6979.55),
        _account("savings", kind="depository", available=44611.10),
        _account("card", kind="credit", available=7209.0),
    ]))
    assert total == 6979.55


def test_naming_accounts_narrows_the_cash_too():
    total, _ = plaid.cash_from(_payload(accounts=[
        _account("roth", available=3379.57), _account("taxable", available=1000.0),
    ]), accounts=("roth",))
    assert total == 3379.57


def test_a_broker_that_reports_no_balance_says_none_not_zero():
    """"We do not know" and "you have nothing to spend" are different facts, and only one of
    them should ever be printed as a number."""
    assert plaid.cash_from(_payload(accounts=[
        {"account_id": "a", "type": "investment", "name": "x", "balances": {}}])) == (None, {})
    assert plaid.cash_from(_payload()) == (None, {})


# ── the broker's own identity and mark ──

def test_the_broker_identity_and_mark_ride_along(tmp_path):
    """Neither is used to fetch anything. They exist so `core.review.mark_disagrees` has a
    second opinion to check our ticker-fetched price against."""
    rows, _, _ = plaid.rows_from(_payload(
        _holding("s1", 3.0),
        securities=[{**_security("s1", "LEU"), "figi": "BBG000BQ2L37"}],
    ))
    assert rows[0].figi == "BBG000BQ2L37"
    assert rows[0].mark is None          # this holding carried no institution_price

    priced = dict(_holding("s1", 3.0), institution_price=188.98)
    rows, _, _ = plaid.rows_from(_payload(priced, securities=[_security("s1", "LEU")]))
    assert rows[0].mark == 188.98


def test_cash_is_broken_down_per_account():
    """What makes merging two IRAs into one file honest. Their positions genuinely sum — the
    same ticker in both is one exposure — but you cannot buy in the Roth with Traditional
    money, so the total alone would name a sum that is not spendable anywhere."""
    total, by = plaid.cash_from(_payload(accounts=[
        _account("roth", available=3379.57, name="Roth IRA"),
        _account("trad", available=500.0, name="Traditional IRA"),
    ]))
    assert total == 3879.57
    assert by == {"Roth IRA": 3379.57, "Traditional IRA": 500.0}


