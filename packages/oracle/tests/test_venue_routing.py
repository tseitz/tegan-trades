from dataclasses import dataclass

import pytest
from core.funding import FundingOutlook
from oracle import venue_routing
from oracle.venue_routing import Router


@dataclass(frozen=True)
class _Cand:
    asset: str
    direction: str
    entry: float
    stop: float


def _outlook(annual: float, n: int = 30):
    return FundingOutlook(venue="hyperliquid", median=annual, p90=annual, n=n)


def _gaps(n: int, gap: float):
    return tuple([gap] * n)


# ── stop distance ────────────────────────────────────────────────────────────

def test_stop_distance_is_a_fraction_of_entry():
    assert venue_routing.stop_distance(_Cand("X", "long", 100.0, 96.0)) == pytest.approx(0.04)


def test_stop_distance_is_absolute_so_a_short_is_not_negative():
    assert venue_routing.stop_distance(_Cand("X", "short", 100.0, 104.0)) == pytest.approx(0.04)


def test_a_candidate_without_a_usable_entry_has_no_stop_distance():
    # Returned rather than defaulted: an invented stop produces a confident wrong gap cost.
    assert venue_routing.stop_distance(_Cand("X", "long", 0.0, 96.0)) is None


# ── which venues get quoted ──────────────────────────────────────────────────

def test_an_asset_alpaca_does_not_list_gets_no_alpaca_quote():
    r = Router(alpaca_symbols={}, cohort={}, hl_outlooks={"SOL": _outlook(0.05)})
    assert [q.venue for q in r.quotes_for(_Cand("SOL", "long", 100.0, 96.0))] == ["hyperliquid"]


def test_an_asset_with_no_funding_observed_gets_no_hyperliquid_quote():
    # Absence is a real answer. Quoting it with carry=None would make it win at 0.0.
    r = Router(alpaca_symbols={"INTL": "INTL"}, cohort={"INTL": _gaps(300, -0.01)},
               hl_outlooks={})
    assert [q.venue for q in r.quotes_for(_Cand("INTL", "long", 100.0, 96.0))] == ["alpaca"]


def test_kraken_is_never_quoted_because_nothing_could_be_placed_there():
    assert "kraken" not in venue_routing.ROUTED_VENUES


def test_crossing_is_unpriced_on_every_quote_for_now():
    # §43. Recorded by test so that pricing it later is a visible change, not a silent one.
    r = Router(alpaca_symbols={"BE": "BE"}, cohort={"BE": _gaps(300, -0.001)},
               hl_outlooks={"BE": _outlook(0.40)})
    for q in r.quotes_for(_Cand("BE", "long", 100.0, 96.0)):
        assert "crossing" in q.unpriced


# ── the gap term ─────────────────────────────────────────────────────────────

def test_a_wide_stop_is_not_charged_for_gaps_it_never_suffers():
    # BE's real shape: a 22.7% stop against gaps that never reach it. The pool is evaluated at
    # that stop, so it is 0 too, and the shrunk answer stays 0 rather than inheriting a cohort
    # number set at much tighter stops.
    r = Router(alpaca_symbols={"BE": "BE", "OTHER": "OTHER"},
               cohort={"BE": _gaps(300, -0.01), "OTHER": _gaps(300, -0.02)},
               hl_outlooks={})
    q = r.quotes_for(_Cand("BE", "long", 100.0, 77.3))[0]
    assert q.gap == pytest.approx(0.0)


def test_a_tight_stop_is_charged_for_the_gaps_that_clear_it():
    r = Router(alpaca_symbols={"PLTR": "PLTR", "OTHER": "OTHER"},
               cohort={"PLTR": _gaps(300, -0.05), "OTHER": _gaps(300, -0.05)},
               hl_outlooks={})
    q = r.quotes_for(_Cand("PLTR", "long", 100.0, 98.0))[0]
    assert q.gap is not None and q.gap > 0.0


def test_a_long_and_a_short_on_the_same_asset_do_not_cost_the_same():
    # The cohort only gaps DOWN, so a long is exposed and a short is not.
    cohort = {"X": _gaps(300, -0.05), "OTHER": _gaps(300, -0.05)}
    r = Router(alpaca_symbols={"X": "X", "OTHER": "OTHER"}, cohort=cohort, hl_outlooks={})
    long_gap = r.quotes_for(_Cand("X", "long", 100.0, 98.0))[0].gap
    short_gap = r.quotes_for(_Cand("X", "short", 100.0, 102.0))[0].gap
    assert long_gap is not None and short_gap is not None
    assert long_gap > short_gap
    assert short_gap == pytest.approx(0.0)


def test_an_asset_with_no_bars_and_no_cohort_has_an_unpriced_gap():
    r = Router(alpaca_symbols={"NEW": "NEW"}, cohort={}, hl_outlooks={})
    q = r.quotes_for(_Cand("NEW", "long", 100.0, 96.0))[0]
    assert q.gap is None
    assert "gap" in q.unpriced


def test_an_asset_with_no_bars_still_takes_the_cohort_rate():
    # A newly listed instrument can never have its own history, so it must not price at zero.
    r = Router(alpaca_symbols={"PLUME": "PLUME", "OTHER": "OTHER"},
               cohort={"OTHER": _gaps(300, -0.10)}, hl_outlooks={})
    q = r.quotes_for(_Cand("PLUME", "long", 100.0, 96.0))[0]
    assert q.gap is not None and q.gap > 0.0


# ── the decision ─────────────────────────────────────────────────────────────

def test_the_cheaper_venue_wins_end_to_end():
    # BE's real numbers: free to hold on Alpaca, 40%/yr to carry on a perp.
    r = Router(alpaca_symbols={"BE": "BE", "OTHER": "OTHER"},
               cohort={"BE": _gaps(300, -0.01), "OTHER": _gaps(300, -0.01)},
               hl_outlooks={"BE": _outlook(0.40)})
    d = r.decide(_Cand("BE", "long", 100.0, 77.3))
    assert d.winner is not None and d.winner.venue == "alpaca"


def test_a_short_is_paid_on_a_perp_and_that_beats_a_gap():
    r = Router(alpaca_symbols={"PLTR": "PLTR", "OTHER": "OTHER"},
               cohort={"PLTR": _gaps(300, 0.05), "OTHER": _gaps(300, 0.05)},
               hl_outlooks={"PLTR": _outlook(0.055)}, can_short=True)
    d = r.decide(_Cand("PLTR", "short", 100.0, 102.0))
    assert d.winner is not None and d.winner.venue == "hyperliquid"
    assert d.winner.total < 0


def test_an_unchecked_account_refuses_the_alpaca_leg_of_a_short():
    r = Router(alpaca_symbols={"CRM": "CRM"}, cohort={"CRM": _gaps(300, 0.01)},
               hl_outlooks={})
    d = r.decide(_Cand("CRM", "short", 100.0, 110.0))
    assert d.winner is None
    assert [x.reason for x in d.refused] == ["short_unknown"]


# ── the review's HIGH: pooled_weight must survive the trip to the display ────

def test_a_mostly_pooled_gap_is_flagged_borrowed_on_the_quote():
    """The HIGH finding: ``_gap`` returned a bare float, so ``pooled_weight`` never left this
    module and the queue showed a cohort's number as this instrument's own. 13 of 18
    Alpaca-listed assets in the live queue are in that state.
    """
    r = Router(alpaca_symbols={"BE": "BE", "OTHER": "OTHER"},
               cohort={"BE": _gaps(98, -0.02), "OTHER": _gaps(400, -0.06)},
               hl_outlooks={})
    q = r.quotes_for(_Cand("BE", "long", 100.0, 96.0))[0]
    assert q.gap is not None
    assert "gap" in q.borrowed, "98 sessions leans mostly on the pool and must say so"
    assert q.evidence > 0


def test_a_long_history_is_not_flagged_borrowed():
    r = Router(alpaca_symbols={"XLE": "XLE", "OTHER": "OTHER"},
               cohort={"XLE": _gaps(100_000, -0.001), "OTHER": _gaps(400, -0.06)},
               hl_outlooks={})
    q = r.quotes_for(_Cand("XLE", "long", 100.0, 96.0))[0]
    assert "gap" not in q.borrowed


def test_gap_cost_exposes_the_whole_estimate_not_just_a_number():
    r = Router(alpaca_symbols={"BE": "BE", "OTHER": "OTHER"},
               cohort={"BE": _gaps(98, -0.02), "OTHER": _gaps(400, -0.06)},
               hl_outlooks={})
    cost = r.gap_cost("BE", 0.04, "long")
    assert cost is not None
    assert cost.pooled_weight is not None
    assert cost.sessions == 98


def test_a_single_asset_cohort_reports_no_pool_rather_than_full_confidence():
    r = Router(alpaca_symbols={"INTL": "INTL"}, cohort={"INTL": _gaps(98, -0.05)},
               hl_outlooks={})
    cost = r.gap_cost("INTL", 0.02, "long")
    assert cost is not None
    assert cost.pooled_weight is None, "no cohort existed; that is not the same as not needing one"
    assert not cost.borrowed


# ``build`` reads the price cache and the funding log out of ``data/``, so these two say
# nothing about the code when that ore is absent — they fail on an empty cohort rather
# than on the behaviour they describe. Marked, not deleted: the regression each one pins
# is real and was live.
@pytest.mark.needs_ore
def test_the_cohort_is_the_whole_universe_not_the_assets_asked_for():
    """Regression for a live bug: ``triage`` built the router from ``queue.candidates`` — the
    *limited* sample — so a small sitting left one Alpaca-listed asset in the cohort, the
    self-excluding pool came back empty, and shrinkage switched off silently. ``SMH`` priced at
    its raw 0.00% instead of the shrunk 0.31% and lost its "mostly pooled" flag.
    """
    one = venue_routing.build({"SMH"})
    assert len(one.cohort) > 1, "asking about one asset must not shrink the cohort to one"
    cost = one.gap_cost("SMH", 0.0726, "long")
    assert cost is not None
    assert cost.pooled_weight is not None, "a real pool must exist even for a single-asset query"
    assert cost.borrowed, "72 sessions leans on the pool, and the queue has to be told"


@pytest.mark.needs_ore
def test_asking_about_nothing_still_yields_a_usable_cohort():
    empty = venue_routing.build(set())
    assert len(empty.cohort) > 1


# ── which venues are worth opening a session to ──────────────────────────────────────────────

def test_the_venues_to_open_are_the_ones_that_quote():
    r = Router(alpaca_symbols={"BE": "BE"}, cohort={"BE": _gaps(300, -0.01)},
               hl_outlooks={"SOL": _outlook(0.05)})
    got = venue_routing.candidate_venues(
        r, [_Cand("BE", "long", 100.0, 96.0), _Cand("SOL", "long", 100.0, 96.0)])
    assert got == ("alpaca", "hyperliquid")


def test_a_venue_that_quotes_nothing_is_left_shut():
    r = Router(alpaca_symbols={}, cohort={}, hl_outlooks={"SOL": _outlook(0.05)})
    assert venue_routing.candidate_venues(r, [_Cand("SOL", "long", 100.0, 96.0)]) \
        == ("hyperliquid",)


def test_a_venue_gated_out_of_every_candidate_is_still_opened():
    """The loop this avoids: a short-heavy queue with ``can_short`` unknown gates Alpaca out of
    every row, so choosing venues by *winner* would leave Alpaca shut — and ``can_short`` is
    read from Alpaca's own account, so it would stay unknown, so Alpaca would stay gated.
    """
    r = Router(alpaca_symbols={"CRM": "CRM"}, cohort={"CRM": _gaps(300, 0.01)}, hl_outlooks={})
    short = _Cand("CRM", "short", 100.0, 110.0)
    assert r.decide(short).winner is None, "no winner, because shortability was never asked"
    assert venue_routing.candidate_venues(r, [short]) == ("alpaca",)


def test_an_empty_queue_wants_no_venues():
    assert venue_routing.candidate_venues(venue_routing.build(set()), []) == ()
