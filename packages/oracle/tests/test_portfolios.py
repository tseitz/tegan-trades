from datetime import UTC, date, datetime

import pytest
from core.nearby import ALL_KINDS, RANGE_EDGE, WEEKLY_ZONE
from oracle.portfolios import PortfolioError, available, load, names_to_load

GOOD = """\
account: retirement
horizon: macro
positions:
  - ticker: VTI
    shares: 42.5
    cost: 210.40
  - ticker: btc
    shares: 0.35
"""


def _write(tmp_path, name, body):
    path = tmp_path / f"{name}.yaml"
    path.write_text(body, encoding="utf-8")
    return tmp_path


def test_loads_a_portfolio(tmp_path):
    root = _write(tmp_path, "retirement", GOOD)
    book = load("retirement", root=root)
    assert book.name == "retirement"
    assert book.horizon == "macro"
    assert [h.ticker for h in book.holdings] == ["VTI", "BTC"]
    assert book.holdings[0].cost == 210.40
    assert book.holdings[1].cost is None


def test_tickers_are_upcased_but_not_otherwise_touched(tmp_path):
    """Canonicalisation is `core.canon`'s job and happens later, against the registry. Doing
    anything cleverer here would put a second asset-naming authority in the repo."""
    root = _write(tmp_path, "p", "positions:\n  - {ticker: ' eth ', shares: 1}\n")
    assert load("p", root=root).holdings[0].ticker == "ETH"


def test_a_missing_file_names_what_exists(tmp_path):
    _write(tmp_path, "retirement", GOOD)
    with pytest.raises(PortfolioError) as err:
        load("brokerage", root=tmp_path)
    assert "retirement" in str(err.value)


def test_zero_and_negative_shares_are_refused(tmp_path):
    """A closed position left in the file at 0 would draw a full reading — roster split,
    weekly location, a verdict — for something you do not own. Silently dropping it would
    hide a stale file instead of pointing at it."""
    root = _write(tmp_path, "p", "positions:\n  - {ticker: VTI, shares: 0}\n")
    with pytest.raises(PortfolioError, match="shares"):
        load("p", root=root)


def test_a_row_without_a_ticker_names_its_position_in_the_file(tmp_path):
    root = _write(tmp_path, "p", "positions:\n  - {shares: 3}\n  - {ticker: VTI, shares: 1}\n")
    with pytest.raises(PortfolioError, match="position 1"):
        load("p", root=root)


def test_an_unparseable_number_is_refused_rather_than_coerced(tmp_path):
    root = _write(tmp_path, "p", "positions:\n  - {ticker: VTI, shares: 'a lot'}\n")
    with pytest.raises(PortfolioError, match="shares"):
        load("p", root=root)


def test_an_empty_position_list_is_refused(tmp_path):
    root = _write(tmp_path, "p", "account: p\npositions: []\n")
    with pytest.raises(PortfolioError, match="no positions"):
        load("p", root=root)


def test_duplicate_tickers_are_refused(tmp_path):
    """Two rows for one ticker would produce two independent readings of the same position,
    each sized wrong. Merging them silently would be a guess about which cost basis is real."""
    root = _write(
        tmp_path, "p",
        "positions:\n  - {ticker: VTI, shares: 1}\n  - {ticker: vti, shares: 2}\n",
    )
    with pytest.raises(PortfolioError, match="VTI"):
        load("p", root=root)


def test_domain_defaults_to_stock_and_can_be_set_either_level(tmp_path):
    """Routing normally infers what an asset *is* from corpus consensus, and a retirement
    account holds things nobody on the roster has ever mentioned. Without a domain those
    route as `conflict` — priceable in principle, refused in practice. The file is the only
    place that knowledge exists, so it has to be able to say it."""
    root = _write(tmp_path, "p", GOOD)
    assert [p.domain for p in load("p", root=root).positions] == ["stock", "stock"]

    root = _write(tmp_path, "c", "domain: crypto\npositions:\n  - {ticker: BTC, shares: 1}\n")
    assert load("c", root=root).positions[0].domain == "crypto"

    root = _write(
        tmp_path, "m",
        "domain: stock\npositions:\n"
        "  - {ticker: VTI, shares: 1}\n"
        "  - {ticker: BTC, shares: 1, domain: crypto}\n",
    )
    assert [p.domain for p in load("m", root=root).positions] == ["stock", "crypto"]


def test_domain_rows_are_offered_to_the_routing_table(tmp_path):
    root = _write(tmp_path, "m",
                  "positions:\n  - {ticker: VTI, shares: 1}\n"
                  "  - {ticker: BTC, shares: 1, domain: crypto}\n")
    assert load("m", root=root).domain_rows == (("VTI", "stock"), ("BTC", "crypto"))


def test_available_lists_the_files(tmp_path):
    _write(tmp_path, "retirement", GOOD)
    _write(tmp_path, "brokerage", GOOD)
    (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")
    assert available(root=tmp_path) == ("brokerage", "retirement")


def test_available_on_a_missing_directory_is_empty_not_an_error(tmp_path):
    assert available(root=tmp_path / "nope") == ()


def test_names_to_load_combines_explicit_and_every_without_duplicates(tmp_path):
    _write(tmp_path, "retirement", GOOD)
    _write(tmp_path, "brokerage", GOOD)
    assert names_to_load([], every=False, root=tmp_path) == ()
    assert names_to_load(["retirement"], every=False, root=tmp_path) == ("retirement",)
    assert names_to_load([], every=True, root=tmp_path) == ("brokerage", "retirement")
    # Named AND asked for all: fetched once, and the named one keeps its position.
    assert names_to_load(["retirement"], every=True, root=tmp_path) == ("retirement", "brokerage")


def test_names_to_load_keeps_a_name_that_is_not_on_disk(tmp_path):
    """So the caller reports "no portfolio \'typo\'" rather than silently warming nothing —
    the same reason `load` names what exists instead of returning empty."""
    assert names_to_load(["typo"], every=True, root=tmp_path) == ("typo",)


def test_level_kinds_default_to_all_four(tmp_path):
    root = _write(tmp_path, "p", GOOD)
    assert load("p", root=root).level_kinds == ALL_KINDS


def test_level_kinds_can_be_narrowed_in_the_file(tmp_path):
    """The scale-back knob. A decade-horizon account eventually wants the daily blocks gone,
    and that is a fact about the account rather than about the code."""
    root = _write(tmp_path, "p",
                  "levels: [weekly_zone, range_edge]\npositions:\n  - {ticker: VTI, shares: 1}\n")
    assert load("p", root=root).level_kinds == (WEEKLY_ZONE, RANGE_EDGE)


def test_an_unknown_level_kind_is_refused_rather_than_matching_nothing(tmp_path):
    """`levels: [weekly]` is the obvious typo for `weekly_zone`. Passed through it would match
    nothing and print an empty section, which looks identical to an account with no levels
    near it — a wrong answer that cannot be told from a right one."""
    root = _write(tmp_path, "p",
                  "levels: [weekly]\npositions:\n  - {ticker: VTI, shares: 1}\n")
    with pytest.raises(PortfolioError, match="weekly"):
        load("p", root=root)


# ── how old is what you wrote down ─────────────────────────────────────────


def test_horizon_uses_the_repos_own_vocabulary(tmp_path):
    """`scalp | swing | position | macro` is what `core.thesis` and `HalfLife` already speak.
    A fifth word here would be a second vocabulary for one idea."""
    root = _write(tmp_path, "p", "horizon: swing\npositions:\n  - {ticker: VTI, shares: 1}\n")
    assert load("p", root=root).horizon == "swing"


def test_an_invented_horizon_is_refused(tmp_path):
    root = _write(tmp_path, "p", "horizon: long\npositions:\n  - {ticker: VTI, shares: 1}\n")
    with pytest.raises(PortfolioError, match="horizon"):
        load("p", root=root)


def test_the_file_dates_itself_from_its_mtime_when_it_does_not_say(tmp_path):
    """No bookkeeping to forget. You edited the file when you edited the file, and an
    `updated:` line you have to remember to bump is exactly the thing that goes stale first."""
    root = _write(tmp_path, "p", GOOD)
    assert load("p", root=root).updated == datetime.now(UTC).date()


def test_an_explicit_updated_date_wins_over_the_mtime(tmp_path):
    """So a file restored from a backup, or one you touched for an unrelated reason, can
    still say when the positions were actually true."""
    root = _write(tmp_path, "p", "updated: 2026-01-15\npositions:\n  - {ticker: V, shares: 1}\n")
    assert load("p", root=root).updated == date(2026, 1, 15)


def test_an_unreadable_updated_date_is_refused_rather_than_ignored(tmp_path):
    """Falling back to the mtime would silently report a fresh file when you meant to say it
    was six months old — the wrong direction to be wrong in."""
    root = _write(tmp_path, "p", "updated: last tuesday\npositions:\n  - {ticker: V, shares: 1}\n")
    with pytest.raises(PortfolioError, match="updated"):
        load("p", root=root)


def test_stale_after_defaults_to_the_horizons_half_life(tmp_path):
    root = _write(tmp_path, "p", "horizon: swing\npositions:\n  - {ticker: V, shares: 1}\n")
    assert load("p", root=root).stale_after == 21

    root = _write(tmp_path, "m", "horizon: macro\npositions:\n  - {ticker: V, shares: 1}\n")
    assert load("m", root=root).stale_after == 360


def test_stale_after_can_be_set_per_account(tmp_path):
    """An actively traded account goes wrong in days, whatever its horizon says about how
    long you intend to hold. The default is a starting point, not a measurement."""
    root = _write(tmp_path, "p",
                  "horizon: macro\nstale_after: 14\npositions:\n  - {ticker: V, shares: 1}\n")
    assert load("p", root=root).stale_after == 14


def test_a_fresh_file_is_not_stale_and_an_old_one_is(tmp_path):
    root = _write(tmp_path, "p", "stale_after: 30\npositions:\n  - {ticker: V, shares: 1}\n")
    book = load("p", root=root)
    assert book.age_days(on=datetime.now(UTC).date()) == 0
    assert book.is_stale(on=datetime.now(UTC).date()) is False

    old = load("o", root=_write(tmp_path, "o",
                                "updated: 2026-01-01\nstale_after: 30\n"
                                "positions:\n  - {ticker: V, shares: 1}\n"))
    assert old.is_stale(on=date(2026, 3, 1)) is True
