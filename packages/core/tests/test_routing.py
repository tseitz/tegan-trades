import pytest
from core.routing import (
    NOISE_FLOOR,
    REFUSAL_CANNOT_SHORT,
    REFUSAL_SHORT_UNKNOWN,
    TERMS,
    TIE_PREFERENCE,
    decide,
    direction_refusal,
    quote,
)

# ── which cost terms exist where ─────────────────────────────────────────────

def test_a_perp_venue_has_funding_and_no_gap():
    assert "carry" in TERMS["hyperliquid"]
    assert "gap" not in TERMS["hyperliquid"]


def test_an_equity_venue_has_a_gap_and_no_funding():
    assert "gap" in TERMS["alpaca"]
    assert "carry" not in TERMS["alpaca"]


def test_spot_crypto_has_neither_funding_nor_a_gap():
    # Kraken spot: 24/7 so nothing to gap over, and no perpetual so nothing to fund.
    assert TERMS["kraken"].isdisjoint({"carry", "gap"})


def test_quoting_a_term_a_venue_does_not_have_is_refused_not_ignored():
    # A gap on a continuously traded perp is not zero, it is meaningless. Accepting it silently
    # is how YM got charged 6.33% for an instrument that trades 24 hours.
    with pytest.raises(ValueError, match="gap"):
        quote("hyperliquid", "NVDA", carry=0.005, gap=0.01)


def test_an_unknown_venue_is_refused_rather_than_given_empty_terms():
    with pytest.raises(ValueError):
        quote("ftx", "NVDA", crossing=0.001)


# ── unpriced is not free ─────────────────────────────────────────────────────

def test_an_applicable_term_left_out_is_recorded_unpriced():
    q = quote("alpaca", "BE", gap=0.006)
    assert q.unpriced == ("crossing",)
    assert q.priced == ("gap",)


def test_a_fully_priced_quote_has_nothing_unpriced():
    q = quote("alpaca", "BE", gap=0.006, crossing=0.001)
    assert q.unpriced == ()
    assert q.total == pytest.approx(0.007)


def test_total_sums_only_the_known_terms():
    q = quote("hyperliquid", "NVDA", carry=0.0065)
    assert q.total == pytest.approx(0.0065)


def test_being_paid_to_hold_is_a_negative_cost_not_a_zero_one():
    # A short on Hyperliquid is paid funding. It has to be able to rank BELOW free.
    q = quote("hyperliquid", "PLTR", carry=-0.0032, crossing=0.0005)
    assert q.total < 0


def test_dominant_names_the_term_that_decided_it():
    q = quote("alpaca", "PLTR", gap=0.0523, crossing=0.0005)
    assert q.dominant == "gap"


def test_dominant_is_by_magnitude_so_a_credit_can_dominate():
    q = quote("hyperliquid", "PLTR", carry=-0.0063, crossing=0.0005)
    assert q.dominant == "carry"


def test_dominant_is_none_when_nothing_is_priced():
    assert quote("alpaca", "X").dominant is None


def test_dominant_is_none_when_the_venue_is_actually_free():
    # A wide-stop equity really does cost 0.00% to hold overnight. Naming "gap" as the dominant
    # term there reads as a charge being levied when the measurement is that there is none.
    assert quote("alpaca", "BE", gap=0.0, crossing=0.0).dominant is None


# ── gates: a rule or a missing fact, never a score ───────────────────────────

def test_spot_crypto_cannot_take_a_short():
    # Kraken spot is long-only and US margin is closed to retail (§30).
    assert direction_refusal("kraken", "short") is not None
    assert direction_refusal("kraken", "long") is None


def test_an_equity_account_that_cannot_short_refuses_a_short():
    # shorting_enabled is the truth, not an equity threshold — see alpaca-order-hazards #3.
    assert direction_refusal("alpaca", "short", can_short=False) is not None
    assert direction_refusal("alpaca", "short", can_short=True) is None


def test_an_unknown_shorting_capability_refuses_rather_than_assuming_yes():
    assert direction_refusal("alpaca", "short", can_short=None) is not None


def test_not_yet_asked_is_a_different_refusal_from_measured_false():
    # Both refuse, but only one is a fact. The queue runs with no credentials so it can only be
    # in the "not asked" state, and printing "cannot short" there would assert a stale
    # measurement — Alpaca has been seen reporting no_shorting false while shorting_enabled was
    # false, so the two really do disagree.
    unknown = direction_refusal("alpaca", "short", can_short=None)
    measured = direction_refusal("alpaca", "short", can_short=False)
    assert unknown is not None and measured is not None
    assert unknown.reason == REFUSAL_SHORT_UNKNOWN
    assert measured.reason == REFUSAL_CANNOT_SHORT
    assert unknown.reason != measured.reason


def test_a_perp_venue_shorts_without_asking():
    assert direction_refusal("hyperliquid", "short") is None


# ── ranking ──────────────────────────────────────────────────────────────────

def test_the_cheapest_venue_wins():
    d = decide("BE", "long", [
        quote("alpaca", "BE", gap=0.0, crossing=0.001),
        quote("hyperliquid", "BE", carry=0.0235, crossing=0.0005),
    ])
    assert d.winner is not None
    assert d.winner.venue == "alpaca"
    assert d.runner_up is not None and d.runner_up.venue == "hyperliquid"


def test_a_wide_margin_is_decisive_and_reports_the_term_behind_it():
    # PLTR short on a shorting-enabled account: 5.23% gap cost on Alpaca against being *paid*
    # 0.32% on a perp. Not a close call, and the margin has to say so.
    d = decide("PLTR", "short", [
        quote("alpaca", "PLTR", gap=0.0523, crossing=0.0005),
        quote("hyperliquid", "PLTR", carry=-0.0032, crossing=0.0005),
    ], can_short=True)
    assert d.winner is not None and d.winner.venue == "hyperliquid"
    assert d.decisive
    assert d.margin is not None and d.margin > 0.05
    assert d.winner.dominant == "carry"


def test_a_sole_surviving_venue_is_decisive_without_a_margin():
    # The live case: this account cannot short, so Alpaca is gated out of every short and the
    # cost comparison never happens. One venue left is nothing to be wrong about — but there is
    # no runner-up, so there is no margin, and callers must not read that None as a tie.
    d = decide("PLTR", "short", [
        quote("alpaca", "PLTR", gap=0.0523, crossing=0.0005),
        quote("hyperliquid", "PLTR", carry=-0.0032, crossing=0.0005),
    ])
    assert d.winner is not None and d.winner.venue == "hyperliquid"
    assert d.margin is None
    assert d.decisive
    assert d.tie_break is None
    assert [r.venue for r in d.refused] == ["alpaca"]


def test_a_margin_inside_the_noise_floor_is_not_decisive():
    # Median gap cost 0.595% against median HL carry 0.53% — the median trade is a coin flip,
    # and the router must say so rather than dressing 6bp up as a decision.
    d = decide("NVDA", "long", [
        quote("alpaca", "NVDA", gap=0.00595, crossing=0.0005),
        quote("hyperliquid", "NVDA", carry=0.0053, crossing=0.0005),
    ])
    assert not d.decisive
    assert d.margin is not None and d.margin < NOISE_FLOOR


def test_a_near_tie_prefers_the_better_evidenced_venue():
    # Same cost, but one venue has an unpriced term. Fewer unknowns wins before taste does.
    d = decide("X", "long", [
        quote("hyperliquid", "X", carry=0.005),                    # crossing unpriced
        quote("alpaca", "X", gap=0.005, crossing=0.0),
    ])
    assert not d.decisive
    assert d.winner is not None and d.winner.venue == "alpaca"
    assert d.tie_break == "evidence"


def test_a_near_tie_with_equal_evidence_falls_back_to_the_stated_preference():
    d = decide("X", "long", [
        quote("hyperliquid", "X", carry=0.005, crossing=0.0),
        quote("alpaca", "X", gap=0.005, crossing=0.0),
    ])
    assert not d.decisive
    assert d.winner is not None
    assert d.winner.venue == TIE_PREFERENCE[0] == "alpaca"
    assert d.tie_break == "preference"


def test_a_decisive_win_reports_no_tie_break():
    d = decide("BE", "long", [
        quote("alpaca", "BE", gap=0.0, crossing=0.0),
        quote("hyperliquid", "BE", carry=0.0235, crossing=0.0),
    ])
    assert d.decisive
    assert d.tie_break is None


def test_the_stated_preference_puts_the_unpriced_legal_exposure_last():
    # Hyperliquid's §1.5 exposure never enters the cost, so it can only ever break a tie.
    assert TIE_PREFERENCE[-1] == "hyperliquid"


# ── an unpriced venue must not win by being unmeasured ───────────────────────

def test_a_venue_with_nothing_priced_is_refused_rather_than_free():
    # Measured live 2026-07-29: a funding lookup miss left Hyperliquid entirely unpriced, and
    # because total sums only known terms it totalled 0.000% and beat a real measured cost on
    # 11 candidates. Unmeasured winning over measured is the bug this whole slice removes.
    d = decide("AMZN", "long", [
        quote("alpaca", "AMZN", gap=0.0044, crossing=0.0005),
        quote("hyperliquid", "AMZN"),
    ])
    assert d.winner is not None and d.winner.venue == "alpaca"
    assert [r.reason for r in d.refused] == ["unpriced"]


def test_every_venue_unpriced_leaves_no_winner_rather_than_a_free_one():
    d = decide("AMZN", "long", [quote("hyperliquid", "AMZN"), quote("alpaca", "AMZN")])
    assert d.winner is None
    assert len(d.refused) == 2


def test_a_partly_priced_venue_still_competes():
    # Crossing is never cached, so demanding every term would refuse everything. The line is
    # "nothing priced", not "not fully priced".
    d = decide("NVDA", "long", [quote("hyperliquid", "NVDA", carry=0.0065)])
    assert d.winner is not None and d.winner.venue == "hyperliquid"


def test_a_win_on_thinner_evidence_is_never_decisive_however_wide():
    # Winner prices one term, runner-up prices two. The winner's unpriced term can only ADD
    # cost, so its lead may be an artefact of what was not counted.
    d = decide("X", "long", [
        quote("hyperliquid", "X", carry=0.001),                 # crossing unpriced
        quote("alpaca", "X", gap=0.05, crossing=0.01),          # fully priced
    ])
    assert d.winner is not None and d.winner.venue == "hyperliquid"
    assert d.margin is not None and d.margin > 0.05
    assert not d.decisive


def test_a_win_on_equal_or_better_evidence_is_decisive():
    d = decide("X", "long", [
        quote("hyperliquid", "X", carry=0.001, crossing=0.0005),
        quote("alpaca", "X", gap=0.05, crossing=0.01),
    ])
    assert d.winner is not None and d.winner.venue == "hyperliquid"
    assert d.decisive


# ── nothing reachable is an answer, and has to be recorded ───────────────────

def test_a_short_with_no_venue_that_takes_it_has_no_winner():
    # §30's shape: a crypto short where the only spot venue is long-only.
    d = decide("SOL", "short", [quote("kraken", "SOL", crossing=0.005)])
    assert d.winner is None
    assert d.refused and d.refused[0].venue == "kraken"


def test_no_quotes_at_all_is_refused_rather_than_crashing():
    d = decide("TON", "long", [])
    assert d.winner is None
    assert d.ranked == ()


def test_a_refusal_carries_a_reason_a_person_can_read():
    d = decide("SOL", "short", [quote("kraken", "SOL", crossing=0.005)])
    assert "long" in d.refused[0].detail.lower()


# ── borrowed evidence (review HIGH) ──────────────────────────────────────────

def test_a_borrowed_term_is_worse_evidenced_than_a_measured_one():
    measured = quote("alpaca", "X", gap=0.01, crossing=0.0)
    pooled_gap = quote("alpaca", "X", gap=0.01, crossing=0.0, borrowed=("gap",))
    assert pooled_gap.evidence > measured.evidence


def test_an_unpriced_term_is_worse_evidenced_than_a_borrowed_one():
    # A missing number is unbounded; a pooled one is at least the right order of magnitude.
    borrowed = quote("alpaca", "X", gap=0.01, crossing=0.0, borrowed=("gap",))
    unpriced = quote("alpaca", "X", gap=0.01)
    assert unpriced.evidence > borrowed.evidence


def test_a_win_resting_on_a_borrowed_number_is_not_decisive():
    # The HIGH finding's substance: a mostly-pooled gap can move when the asset's own history
    # fills in, so a margin built on it is not one to act on however wide it looks.
    d = decide("BE", "long", [
        quote("alpaca", "BE", gap=0.0, crossing=0.0, borrowed=("gap",)),
        quote("hyperliquid", "BE", carry=0.0235, crossing=0.0),
    ])
    assert d.winner is not None and d.winner.venue == "alpaca"
    assert d.margin is not None and d.margin > 0.02
    assert not d.decisive


def test_borrowing_a_term_the_venue_does_not_have_is_refused():
    with pytest.raises(ValueError, match="borrow"):
        quote("hyperliquid", "X", carry=0.01, borrowed=("gap",))


def test_naming_a_wrong_term_is_refused_even_when_its_value_is_none():
    # LOW from review: validation filtered on value, so quote("hyperliquid", gap=None) passed
    # despite the docstring promising otherwise.
    with pytest.raises(ValueError, match="gap"):
        quote("hyperliquid", "X", carry=0.01, gap=None)
