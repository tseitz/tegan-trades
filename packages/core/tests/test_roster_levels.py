from datetime import date
from types import SimpleNamespace

from core.roster_levels import AGAINST, HIT, LEVEL, TARGET, roster_levels

TODAY = date(2026, 11, 10)


def _row(*, person="a", direction="long", timeframe="swing", published_at="2026-11-05",
         key_levels=()):
    return SimpleNamespace(person=person, asset="BTC", direction=direction,
                           timeframe=timeframe, published_at=published_at,
                           key_levels=list(key_levels))


def _run(rows, *, price=100.0, cost=None, ref=100.0):
    return roster_levels(rows, price=price, cost=cost, close_on=lambda d: ref, today=TODAY)


def _roles(res):
    return {c.level: c.role for c in res.levels}


def test_long_target_hit_and_against():
    res = _run([_row(key_levels=[120, 90, 95])], price=100, ref=100)
    assert _roles(res) == {120: TARGET, 90: AGAINST, 95: AGAINST}
    res = _run([_row(key_levels=[110])], price=115, ref=100)
    assert _roles(res) == {110: HIT}


def test_short_roles_mirror():
    res = _run([_row(direction="short", key_levels=[80, 110])], price=100)
    assert _roles(res) == {80: TARGET, 110: AGAINST}
    res = _run([_row(direction="short", key_levels=[90])], price=85)
    assert _roles(res) == {90: HIT}


def test_neutral_is_unlabeled():
    assert _roles(_run([_row(direction="neutral", key_levels=[120])])) == {120: LEVEL}


def test_missing_close_or_date_is_unlabeled():
    rows = [_row(key_levels=[120])]
    res = roster_levels(rows, price=100, cost=None, close_on=lambda d: None, today=TODAY)
    assert _roles(res) == {120: LEVEL}
    res = _run([_row(published_at="garbage", key_levels=[120])])
    assert res.levels == ()


def test_band_drops_and_counts():
    res = _run([_row(key_levels=[0.031, 7.5, 30, 250, 110])], price=100)
    assert [c.level for c in res.levels] == [110]
    assert res.dropped == 4


def test_stale_swing_and_long_terms_excluded():
    rows = [
        _row(person="a", published_at="2026-09-01", key_levels=[110]),
        _row(person="b", timeframe="position", key_levels=[120]),
        _row(person="c", timeframe="macro", key_levels=[130]),
    ]
    assert _run(rows).levels == ()


def test_newest_per_person_keeps_all_rows_of_newest_date():
    rows = [
        _row(published_at="2026-11-01", key_levels=[105]),
        _row(published_at="2026-11-05", key_levels=[110]),
        _row(published_at="2026-11-05", key_levels=[120]),
    ]
    assert [c.level for c in _run(rows).levels] == [120, 110]


def test_newer_macro_does_not_hide_live_swing():
    rows = [
        _row(published_at="2026-11-01", key_levels=[110]),
        _row(timeframe="macro", published_at="2026-11-08", key_levels=[150]),
    ]
    assert [c.level for c in _run(rows).levels] == [110]


def test_vs_cost_none_without_cost_and_set_with_it():
    assert _run([_row(key_levels=[120])]).levels[0].vs_cost is None
    c = _run([_row(key_levels=[120])], cost=80).levels[0]
    assert c.vs_cost == 0.5 and c.distance == 0.2


def test_dedupe_within_person_but_not_across_people():
    rows = [
        _row(person="a", key_levels=[110]),
        _row(person="a", key_levels=[110]),
        _row(person="b", key_levels=[110]),
    ]
    res = _run(rows)
    assert len(res.levels) == 2 and res.people == 2


def test_no_price_is_empty():
    assert roster_levels([_row(key_levels=[1])], price=None, cost=None,
                         close_on=lambda d: 1.0, today=TODAY).levels == ()


def test_sorted_descending():
    res = _run([_row(key_levels=[90, 130, 110])])
    assert [c.level for c in res.levels] == [130, 110, 90]
