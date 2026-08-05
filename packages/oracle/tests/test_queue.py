from __future__ import annotations

import random
from dataclasses import replace
from datetime import date
from itertools import pairwise

import pytest
from core.exits import EXTREME
from core.setups import DAILY, TIER_MAJOR, WEEKLY, Candidate, View
from core.structure import BULLISH, SWING_HIGH, SWING_LOW, Break, OrderBlock, Swing
from oracle import queue


def _swing(price, kind, *, index=0, day=1):
    when = date(2025, 1, day)
    return Swing(date=when, price=price, kind=kind, confirmed_at=when, index=index)


def _block():
    """A hand-built order block — same approach as ``test_setups_cli._block``. Nothing in this
    module reads the block, but ``Candidate`` requires a real one."""
    broken = _swing(120.0, SWING_HIGH)
    origin = _swing(90.0, SWING_LOW, index=3, day=4)
    confirmed_at = date(2025, 1, 6)
    bos = Break(kind=BULLISH, level=broken.price, swing=broken, origin=origin,
                date=confirmed_at, index=5)
    return OrderBlock(kind=BULLISH, top=110.0, bottom=100.0, date=date(2025, 1, 5), index=4,
                      confirmed_at=confirmed_at, bos=bos, invalidation=90.0)


def _candidate(score: float, asset: str = "BTC") -> Candidate:
    return Candidate(
        asset=asset, direction="long", block=_block(),
        entry=110.0, entry_top=110.0, entry_bottom=100.0,
        stop=100.0, invalidation=90.0,
        target=140.0, target_source=EXTREME,
        reward_risk=3.0, reward_risk_from_price=3.5, approach=1.0, price=105.0,
        weekly_trend="uptrend", daily_trend="uptrend", zone="discount",
        zone_timeframe=DAILY, tier=TIER_MAJOR,
        freshness=1.0, trend_alignment=1.0,
        views=(View(person="Mayne", published_at="2026-07-20"),), thesis_ids=("t1",),
        score=score,
    )


def _population(scores):
    """One candidate per score, each on its own asset so rows stay distinguishable."""
    return [_candidate(s, asset=f"A{i}") for i, s in enumerate(scores)]


def _scores(q: queue.Queue) -> list[float]:
    return [row.candidate.score for row in q.rows]


# ── filtering ────────────────────────────────────────────────────────────────

def test_filter_by_min_score_drops_below_threshold():
    low, high = _candidate(0.2), _candidate(0.8)
    assert queue.filter_candidates([low, high], min_score=0.5) == [high]


def test_filter_with_no_arguments_is_a_passthrough():
    cands = _population([0.5, 0.5])
    assert queue.filter_candidates(cands) == cands


# ── the whole population fits ────────────────────────────────────────────────

def test_a_population_inside_the_limit_is_shown_whole_and_says_so():
    """Nothing was sampled, so the sitting is trivially full-range and must not claim
    otherwise — a mining pass conditioning on ``stratified`` would over-count sittings."""
    pop = _population([0.9, 0.5, 0.1])
    q = queue.build_queue(pop, limit=25)
    assert q.mode == queue.FULL
    assert [row.candidate for row in q.rows] == pop
    assert {row.band for row in q.rows} == {queue.FULL}


def test_no_limit_means_the_whole_population():
    pop = _population([0.9, 0.5, 0.1])
    assert len(queue.build_queue(pop, limit=None).rows) == 3


def test_a_zero_limit_is_refused_rather_than_read_as_an_empty_queue():
    """``--limit 0`` is the CLI's escape hatch *from* the cap, and ``main`` translates it to
    None. Honouring a literal 0 here would build an empty queue from a full population and
    report it as a ``top`` sitting — a silent no-op that looks like a decision."""
    with pytest.raises(ValueError, match="no cap"):
        queue.build_queue(_population([0.9, 0.5]), limit=0)


def test_an_empty_queue_says_which_check_the_caller_skipped():
    """An empty queue is ordinary — the corpus produces nothing to review most nights — so the
    range is genuinely undefined rather than wrong. ``min()``'s own message names neither."""
    q = queue.build_queue([], limit=None)
    assert q.rows == ()
    with pytest.raises(ValueError, match="empty queue"):
        _ = q.score_min
    with pytest.raises(ValueError, match="empty queue"):
        _ = q.score_max


# ── top mode: the behaviour that shipped before §4 ───────────────────────────

def test_top_mode_takes_the_head_of_the_list_verbatim():
    """``collapse`` returns weekly-then-score, and ``--sample top`` must keep honouring that
    ordering rather than re-sorting on score — the weekly-first rule lives in the sort."""
    pop = _population([0.4, 0.9, 0.8, 0.7, 0.6])
    q = queue.build_queue(pop, limit=3, stratified=False)
    assert [row.candidate for row in q.rows] == pop[:3]
    assert q.mode == queue.TOP


def test_a_limit_no_bigger_than_the_head_degenerates_to_top_and_admits_it():
    """There is no tail left to stratify, so calling the sitting stratified would tell the
    mining pass it was comparable when it is exactly the slice §4 says it cannot pool."""
    pop = _population([0.9, 0.8, 0.7, 0.6, 0.5, 0.4])
    q = queue.build_queue(pop, limit=queue.HEAD_SIZE, head=queue.HEAD_SIZE)
    assert q.mode == queue.TOP


# ── stratified mode ──────────────────────────────────────────────────────────

def test_the_stratified_queue_is_exactly_the_limit():
    pop = _population([i / 100 for i in range(100, 0, -1)])
    q = queue.build_queue(pop, limit=25, rng=random.Random(0))
    assert len(q.rows) == 25


def test_the_top_scorers_are_always_on_screen():
    """The head band exists so calibration never costs the queue its other job: whatever else
    is drawn, tonight's best candidate is still shown."""
    pop = _population([i / 100 for i in range(100, 0, -1)])
    for seed in range(20):
        q = queue.build_queue(pop, limit=25, head=5, rng=random.Random(seed))
        assert _scores(q)[:5] == [1.0, 0.99, 0.98, 0.97, 0.96]


def test_every_sitting_spans_the_population_range_which_is_the_whole_point_of_4():
    """The §4 defect, as a regression test. Top-of-queue hands each sitting a narrower slice
    than the last (measured: 0.377-0.906, then 0.528-0.580, then 0.403-0.527), so a term
    cannot order what barely varies. A stratified draw reaches the bottom stratum every time."""
    pop = _population([i / 100 for i in range(100, 0, -1)])

    topped = queue.build_queue(pop, limit=25, stratified=False)
    assert min(_scores(topped)) == 0.76  # the top 25 of 100 — the tail is never judged

    for seed in range(20):
        q = queue.build_queue(pop, limit=25, head=5, rng=random.Random(seed))
        # 5 head + 20 strata over the remaining 95, so the last stratum is the bottom ~5.
        assert min(_scores(q)) <= 0.05
        assert max(_scores(q)) == 1.0


def test_the_head_reaches_the_best_score_even_when_queue_order_buries_it():
    """Measured on the live queue 2026-07-28: ``collapse`` puts all 28 weekly rows before all
    39 daily ones, so ``--limit 25`` was 25 weekly and 0 daily — and TSLA at 0.90, the highest
    score in the whole population, sat at position 29 and was never shown, while the CLI said
    "showing the top 25 by score". The head band sorts on score, so it cannot recur."""
    buried_first = [_candidate(0.400 + i / 1000, asset=f"W{i}") for i in range(28)]
    best_but_last = [_candidate(s, asset=f"D{i}") for i, s in enumerate((0.90, 0.80, 0.70))]
    pop = buried_first + best_but_last

    topped = queue.build_queue(pop, limit=25, stratified=False)
    assert max(_scores(topped)) < 0.9  # the defect, reproduced

    for seed in range(10):
        q = queue.build_queue(pop, limit=25, head=5, rng=random.Random(seed))
        assert max(_scores(q)) == 0.9


def test_rows_are_presented_in_the_queues_own_order_not_by_score():
    """Sampling decides *which* candidates are shown; ``collapse`` still decides the order
    they are shown in, so §19(e)'s weekly-first rule survives untouched."""
    pop = _population([0.4, 0.95, 0.2, 0.9, 0.1, 0.85, 0.3, 0.8, 0.5, 0.7])
    q = queue.build_queue(pop, limit=6, head=2, rng=random.Random(3))
    shown = [row.candidate for row in q.rows]
    assert shown == [c for c in pop if c in shown]


def test_the_head_and_the_tail_are_labelled_so_the_tail_can_be_mined_alone():
    """The head is still a score-ordered slice and marches down between sittings exactly as
    before. Only the tail is a comparable sample, so the band has to travel with the row."""
    pop = _population([i / 100 for i in range(100, 0, -1)])
    q = queue.build_queue(pop, limit=25, head=5, rng=random.Random(0))
    heads = [row for row in q.rows if row.band == queue.BAND_HEAD]
    tails = [row for row in q.rows if row.band == queue.BAND_TAIL]
    assert len(heads) == 5
    assert len(tails) == 20
    assert min(c.candidate.score for c in heads) > max(c.candidate.score for c in tails)


def test_the_draw_is_deterministic_given_an_rng():
    pop = _population([i / 100 for i in range(100, 0, -1)])
    first = _scores(queue.build_queue(pop, limit=25, rng=random.Random(7)))
    second = _scores(queue.build_queue(pop, limit=25, rng=random.Random(7)))
    assert first == second


def test_the_draw_is_fixed_to_the_day_so_an_unattended_digest_does_not_reshuffle():
    """``scripts/nightly.sh`` runs ``setups --list`` every night. An unseeded draw would redraw
    the tail on every invocation, making "a new candidate appeared" indistinguishable from "the
    draw moved" — and it would also stop ``--list`` previewing what a sitting actually offers."""
    pop = _population([i / 100 for i in range(100, 0, -1)])
    today, tomorrow = date(2026, 7, 28), date(2026, 7, 29)

    same_day = [_scores(queue.build_queue(pop, limit=25, rng=queue.rng_for(today)))
                for _ in range(3)]
    assert same_day[0] == same_day[1] == same_day[2]

    assert _scores(queue.build_queue(pop, limit=25, rng=queue.rng_for(tomorrow))) != same_day[0]


def test_different_draws_reach_different_candidates():
    """Otherwise the tail would be as fixed as the head and nothing would have been fixed."""
    pop = _population([i / 100 for i in range(100, 0, -1)])
    draws = {tuple(_scores(queue.build_queue(pop, limit=25, rng=random.Random(s))))
             for s in range(10)}
    assert len(draws) > 1


def test_every_stratum_contributes_exactly_one_candidate():
    """Equal-count strata are what make the draw uniform over the score distribution rather
    than over the score *range* — a range-uniform draw would over-sample sparse extremes."""
    pop = _population([i / 100 for i in range(100, 0, -1)])
    q = queue.build_queue(pop, limit=25, head=5, rng=random.Random(1))
    tail = sorted((row.candidate.score for row in q.rows if row.band == queue.BAND_TAIL),
                  reverse=True)
    # 95 candidates below the head, 20 strata, so consecutive picks stay about a stratum apart.
    gaps = [round(a - b, 10) for a, b in pairwise(tail)]
    assert all(gap > 0 for gap in gaps)
    assert max(gaps) <= 2 * 95 / 20 / 100


# ── the recorded position ────────────────────────────────────────────────────

def test_the_position_describes_what_was_on_screen_not_just_the_row():
    """A sitting that quits early leaves fewer decisions than rows, so the range of *decided*
    rows understates what was actually offered. Recording the queue's own range is what makes
    a sitting self-describing regardless of where it stopped."""
    pop = _population([0.9, 0.1, 0.5, 0.3, 0.7])
    q = queue.build_queue(pop, limit=3, head=1, rng=random.Random(0))
    pos = q.position(1)
    assert pos.rank == 1
    assert pos.size == 3
    assert pos.population == 5
    assert pos.score_min == min(_scores(q))
    assert pos.score_max == max(_scores(q))
    assert pos.mode == q.mode


def test_rank_is_one_based_and_matches_the_row_order():
    pop = _population([0.9, 0.1, 0.5, 0.3, 0.7])
    q = queue.build_queue(pop, limit=3, head=1, rng=random.Random(0))
    assert [q.position(i).rank for i in range(1, 4)] == [1, 2, 3]
    assert [q.position(i).band for i in range(1, 4)] == [row.band for row in q.rows]


# ── one row per ticker ──────────────────────────────────────────────────────────────────────

def _cand(asset, timeframe, score, direction="long"):
    return replace(_candidate(score, asset=asset),
                   zone_timeframe=timeframe, direction=direction)


def test_two_zones_on_one_ticker_become_one_row():
    """Both used to be offered, deliberately — labelled "1 of 2" so the second was judged
    knowing the first existed. That reasoning was about confusion. The reason it changed is
    capital: approving both puts two commitments on one ticker, and the account discovered
    the hard way that commitments are the binding constraint, not risk."""
    kept, dropped = queue.one_per_asset([
        _cand("CRM", WEEKLY, 0.44), _cand("CRM", DAILY, 0.28), _cand("BE", DAILY, 0.42),
    ])
    assert [c.asset for c in kept] == ["CRM", "BE"]
    assert dropped == 1


def test_the_weekly_wins_even_when_it_scores_lower():
    """`collapse` orders weekly-then-score because "the macro is much stronger" — the same
    rule that lets a weekly trend veto a direction outright. Deduping must not quietly
    reintroduce score-alone ordering and pick the daily."""
    kept, _ = queue.one_per_asset([_cand("CRM", WEEKLY, 0.44), _cand("CRM", DAILY, 0.90)])
    assert kept[0].zone_timeframe == WEEKLY
    assert kept[0].score == 0.44


def test_order_is_otherwise_untouched():
    """It filters, it does not re-sort. `build_queue` re-derives scores rather than reading
    list position, and that contract only holds if collapse order survives."""
    rows = [_cand("A", DAILY, 0.9), _cand("B", DAILY, 0.8), _cand("A", DAILY, 0.7),
            _cand("C", DAILY, 0.6)]
    kept, dropped = queue.one_per_asset(rows)
    assert [c.asset for c in kept] == ["A", "B", "C"]
    assert dropped == 1


def test_opposite_directions_on_one_ticker_still_collapse_to_one():
    """Two commitments on one ticker is the thing being prevented, and a long plus a short
    is the worst version of it — they would fight each other for the same capital."""
    kept, dropped = queue.one_per_asset([
        _cand("CRM", DAILY, 0.5, "long"), _cand("CRM", DAILY, 0.4, "short"),
    ])
    assert len(kept) == 1
    assert dropped == 1


def test_nothing_to_drop_leaves_the_list_alone():
    rows = [_cand("A", DAILY, 0.9), _cand("B", DAILY, 0.8)]
    kept, dropped = queue.one_per_asset(rows)
    assert kept == rows
    assert dropped == 0
