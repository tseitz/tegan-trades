from __future__ import annotations

import re
from datetime import UTC, date, datetime
from functools import partial
from types import SimpleNamespace

import dashboard.api as api
import dashboard.assets as assets
import pytest
import review.cli as review_cli
import review.render as render_module
from core.nearby import RESISTANCE, SUPPORT, WEEKLY_ZONE, Level
from core.review import (
    HOLD,
    NO_VIEW,
    SILENT,
    TRIM,
    Holding,
    Location,
    Reading,
    RosterLean,
)
from core.setups import WEEKLY
from fastapi.testclient import TestClient
from oracle.portfolios import Benchmark, Mandate, Portfolio, Position
from review.altsignal import ChainLine, MacroRow
from review.cli import ReviewResult
from review.levels import SHOWN, Spotlight, cap

MANDATE = Mandate(name="test", benchmarks=(Benchmark(type="held_flat"),),
                  horizon="position", risk_posture="moderate")
LEVELS_LED_MANDATE = Mandate(name="test-levels-led", benchmarks=(Benchmark(type="held_flat"),),
                             horizon="position", risk_posture="conservative")


def _book(name, *, mandate=MANDATE, tickers=("VTI",), updated=None):
    return Portfolio(
        name=name, mandate=mandate,
        positions=tuple(
            Position(holding=Holding(ticker=t, shares=1.0, cost=None), domain="stock")
            for t in tickers
        ),
        updated=updated,
    )


def _reading(ticker, *, verdict, price, shares=1.0, cost=None):
    return Reading(
        holding=Holding(ticker=ticker, shares=shares, cost=cost),
        roster=RosterLean(lean=SILENT, bulls=0, bears=0, people=0, newest=None,
                          age_days=None, voices=(), thin=False),
        location=Location(where="at_resistance", basis="range", position=0.5),
        verdict=verdict, price=price, weekly_trend="uptrend",
    )


def _grid_readings():
    """A graded row, a priced-but-ungraded row, and an unpriced row — the fixture the #87
    fidelity tests all share."""
    return (
        _reading("AAA", verdict=TRIM, price=100.0, shares=2.0, cost=50.0),
        _reading("BBB", verdict=HOLD, price=10.0, shares=3.0, cost=None),
        _reading("CCC", verdict=NO_VIEW, price=None, shares=1.0),
    )


def _grid_result(readings, *, book_name="retirement", mandate=MANDATE, levels=((), (), 0),
                 chains=(), macro=()):
    return ReviewResult(
        book=_book(book_name, mandate=mandate), readings=list(readings), contexts=(),
        mismatched=(), levels=levels, chains=chains, macro=macro,
    )


def _level(kind=WEEKLY_ZONE, *, timeframe=WEEKLY, side=SUPPORT, top=99.0, bottom=95.0,
           distance=0.0, invalidation=None):
    return Level(kind=kind, timeframe=timeframe, side=side, top=top, bottom=bottom,
                distance=distance, invalidation=invalidation)


def _spot(ticker="DE", *, others=1, invalidation=95.0, **level_kw):
    """`others=1` and `invalidation` set by default so no cell in `render.level_row`'s output
    is blank — `re.split(r"\\s{2,}")` drops any empty cell, not just a trailing one, so the
    fidelity test below needs every column of every fixture row to hold text."""
    return Spotlight(reading=_reading(ticker, verdict=HOLD, price=100.0),
                     level=_level(invalidation=invalidation, **level_kw), others=others)


def _fake_freshness(message="prices: fetched 1 hours ago"):
    """A tiny stand-in for `oracle.freshness.Freshness` — not that type itself, which would
    put `oracle` in a dashboard test's imports (allowed there, but pointlessly coupling this
    file to a boundary it doesn't need to know about)."""
    return SimpleNamespace(message=message)


def _review_response(monkeypatch, tmp_path, result, *, name="retirement", freshness=None):
    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")
    app = api.create_app()
    app.dependency_overrides[api.mandate_review] = lambda: result
    app.dependency_overrides[api.data_freshness] = lambda: freshness or _fake_freshness()
    client = TestClient(app)
    return client.get(f"/api/mandates/{name}/review")


def test_two_mandates_come_back_as_two_names_in_order(monkeypatch, tmp_path):
    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")
    app = api.create_app()
    app.dependency_overrides[api.mandate_books] = lambda: [
        _book("retirement"), _book("crypto"),
    ]
    client = TestClient(app)

    response = client.get("/api/mandates")

    assert response.status_code == 200
    assert response.json() == {"mandates": [{"name": "retirement"}, {"name": "crypto"}]}


def test_envelope_is_an_object_not_a_bare_array(monkeypatch, tmp_path):
    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")
    app = api.create_app()
    app.dependency_overrides[api.mandate_books] = lambda: [_book("retirement")]
    client = TestClient(app)

    body = client.get("/api/mandates").json()

    assert isinstance(body, dict)
    assert "mandates" in body


def test_default_dependency_reaches_review_cli_load_books(monkeypatch, tmp_path):
    """Drives the endpoint through the real dependency, with no override — the only thing
    pinning "never by reading the directory again"."""
    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")
    calls = []

    def fake_load_books(*, warn=None):
        calls.append(warn)
        return [_book("retirement")]

    monkeypatch.setattr(api, "load_books", fake_load_books)
    client = TestClient(api.create_app())

    response = client.get("/api/mandates")

    assert response.status_code == 200
    assert calls, "review.cli.load_books (via dashboard.api.load_books) was never called"


def test_fresh_clone_with_no_portfolios_returns_an_empty_envelope(monkeypatch, tmp_path):
    """Never monkeypatch `portfolios.DATA_ROOT` — `available()` captured it as a default
    argument at import time, so the patch is inert. Bind `available` to `tmp_path` instead."""
    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")
    empty = tmp_path / "empty-portfolios"
    empty.mkdir()
    monkeypatch.setattr(review_cli.portfolios, "available", partial(
        review_cli.portfolios.available, root=empty,
    ))
    client = TestClient(api.create_app())

    response = client.get("/api/mandates")

    assert response.json() == {"mandates": []}


def test_name_comes_from_book_name_not_the_mandate_policy_name(monkeypatch, tmp_path):
    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")
    colliding = Mandate(name="growth", benchmarks=(Benchmark(type="held_flat"),),
                        horizon="position", risk_posture="moderate")
    app = api.create_app()
    app.dependency_overrides[api.mandate_books] = lambda: [
        _book("retirement", mandate=colliding),
    ]
    client = TestClient(app)

    response = client.get("/api/mandates")

    assert response.json() == {"mandates": [{"name": "retirement"}]}


def test_unregistered_api_path_returns_json_404_not_the_spa(monkeypatch, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>")
    monkeypatch.setattr(assets, "WEB_DIST", dist)
    app = api.create_app()
    app.dependency_overrides[api.mandate_books] = lambda: []
    client = TestClient(app)

    response = client.get("/api/nope")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_a_path_traversal_request_cannot_read_outside_web_dist(monkeypatch, tmp_path):
    """A plain `..` in a request `TestClient` builds gets normalised away before it ever
    reaches the app, which would pass against the vulnerable version of `assets.catch_all`
    just as easily as the fixed one. `%2e%2e` (or `//`) survives that normalisation and is
    what an actual client — including a percent-encoded `fetch()` from a browser tab — can
    send, so those are the forms that actually exercise the guard.
    """
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>")
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("should never be served")
    monkeypatch.setattr(assets, "WEB_DIST", dist)
    app = api.create_app()
    app.dependency_overrides[api.mandate_books] = lambda: []
    client = TestClient(app)

    for path in ("/%2e%2e/outside-secret.txt", f"/{'%2e%2e/' * 6}etc/passwd"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.text == "<html>spa</html>"


# ── the review grid (#87) ───────────────────────────────────────────────────


def test_the_grid_is_cell_by_cell_identical_to_the_terminal(monkeypatch, tmp_path):
    """AC 1 and AC 4. Row order is asserted by comparing line to line."""
    readings = _grid_readings()
    body = _review_response(monkeypatch, tmp_path, _grid_result(readings)).json()

    terminal = render_module.render(list(readings), portfolio="retirement",
                                    as_of=date.fromisoformat(body["as_of"]))
    lines = terminal.splitlines()
    header_index = next(i for i, line in enumerate(lines) if "TICKER" in line)
    header_cols = re.split(r"\s{2,}", lines[header_index].strip())
    # The terminal `rstrip()`s the trailing empty verdict header; `columns` still carries it.
    assert header_cols == body["grid"]["columns"][:-1]

    row_lines = lines[header_index + 1:header_index + 1 + len(readings)]
    for row_line, row in zip(row_lines, body["grid"]["rows"], strict=True):
        assert re.split(r"\s{2,}", row_line.strip()) == [c["text"] for c in row["cells"]]


def test_sorting_by_value_matches_the_terminals_by_size_ranking(monkeypatch, tmp_path):
    """AC3, at #84's agreed seam — the JSON the browser receives, not a front-end runner.
    `render.ranked` is already public so a second Surface sorts identically rather than
    growing its own copy of `_by_size`; `web/src/grid/sort.ts` is that second copy, and this
    pins the rule it implements: descending by `cells[3]["value"]`, nulls last."""
    readings = _grid_readings()
    body = _review_response(monkeypatch, tmp_path, _grid_result(readings)).json()

    def sort_key(row):
        value = row["cells"][3]["value"]
        return (value is None, -value if value is not None else 0)

    sorted_rows = sorted(body["grid"]["rows"], key=sort_key)
    expected = render_module.ranked(list(readings), by_size=True)

    assert [row["ticker"] for row in sorted_rows] == [r.holding.ticker for r in expected]
    # AC4, at the same seam: a sort reorders whole row objects, so the TRIM note stays on the
    # AAA row after the permutation rather than being left behind by ticker.
    aaa = next(row for row in sorted_rows if row["ticker"] == "AAA")
    assert [n["label"] for n in aaa["notes"]] == ["TRIM"]


def test_numeric_cells_are_correct_to_the_cent(monkeypatch, tmp_path):
    readings = _grid_readings()
    graded = readings[0]
    body = _review_response(monkeypatch, tmp_path, _grid_result(readings)).json()

    row = body["grid"]["rows"][0]
    assert row["cells"][3]["value"] == graded.market_value
    assert row["cells"][5]["value"] == graded.pnl
    assert row["cells"][6]["value"] == graded.pnl_pct


def test_an_unpriced_rows_computed_cells_survive_as_null_not_zero(monkeypatch, tmp_path):
    readings = _grid_readings()
    body = _review_response(monkeypatch, tmp_path, _grid_result(readings)).json()

    row = body["grid"]["rows"][2]
    assert row["cells"][3] == {"text": "—", "value": None}     # VALUE
    assert row["cells"][5] == {"text": "—", "value": None}     # P&L
    assert row["cells"][6] == {"text": "—", "value": None}     # P&L %


def test_totals_exclude_the_unpriced_row_and_match_the_terminals_tail(monkeypatch, tmp_path):
    readings = _grid_readings()
    graded, ungraded, _unpriced = readings
    body = _review_response(monkeypatch, tmp_path, _grid_result(readings)).json()

    assert body["grid"]["totals"]["market_value"] == graded.market_value + ungraded.market_value
    assert body["grid"]["totals"]["unpriced"] == 1

    terminal = render_module.render(list(readings), portfolio="retirement",
                                    as_of=date.fromisoformat(body["as_of"]))
    tail_lines = [line.strip() for line in terminal.splitlines()
                 if line.strip().startswith(("total", "P&L"))]
    # Lifted out of the same `render()` string above, not out of `render.totals_lines` — that
    # would only assert the wire calls the function the wire calls.
    assert body["grid"]["totals"]["lines"] == tail_lines


def test_a_book_with_no_cost_basis_anywhere_has_no_pnl_line(monkeypatch, tmp_path):
    """The ordinary shape of a synced crypto wallet — `render.py`'s own docstring on the
    tail says so."""
    only_ungraded = [_reading("BBB", verdict=HOLD, price=10.0, shares=3.0, cost=None)]
    body = _review_response(monkeypatch, tmp_path, _grid_result(only_ungraded)).json()

    assert not any(line.startswith("P&L") for line in body["grid"]["totals"]["lines"])


def test_an_empty_mandate_returns_no_rows_or_lines(monkeypatch, tmp_path):
    response = _review_response(monkeypatch, tmp_path, _grid_result([]))

    assert response.status_code == 200
    body = response.json()
    assert body["grid"]["rows"] == []
    assert body["grid"]["totals"]["lines"] == []


def test_review_mandate_comes_from_book_name_not_the_url_path(monkeypatch, tmp_path):
    result = _grid_result(_grid_readings(), book_name="actual-name")
    response = _review_response(monkeypatch, tmp_path, result, name="different-name")

    assert response.status_code == 200
    assert response.json()["mandate"] == "actual-name"


# ── row notes (#90) ──────────────────────────────────────────────────────────


def _yield_note(reading, *, wrapper="STETH", protocol="lido", apy=2.25, already=()):
    """A pure `SimpleNamespace` stand-in for `review.yield_note.YieldNote` — `render.yield_text`
    reads only `wrapper`/`apy`/`protocol`/`already`/`gate.passed`, and the wire reads only
    `note.reading`, so this keeps `oracle.altsignal_*` and `core.safety` out of a dashboard
    test's imports, the same reason `_fake_freshness` is a `SimpleNamespace` and not a real
    `Freshness`."""
    return SimpleNamespace(reading=reading, wrapper=wrapper, protocol=protocol, apy=apy,
                           already=already, gate=SimpleNamespace(passed=True))


def _note_line(terminal: str, ticker: str, label: str) -> str:
    """Selects by label *and* ticker — a YIELD line's second token is also the ticker, so a
    ticker-only match on a row carrying both notes would silently return the loud one twice."""
    return next(line for line in terminal.splitlines()
               if line.strip().split()[:2] == [label, ticker])


def test_a_loud_row_carries_its_note_and_a_quiet_row_carries_none(monkeypatch, tmp_path):
    readings = _grid_readings()  # AAA TRIM, BBB HOLD, CCC NO_VIEW
    body = _review_response(monkeypatch, tmp_path, _grid_result(readings)).json()

    terminal = render_module.render(list(readings), portfolio="retirement",
                                    as_of=date.fromisoformat(body["as_of"]))
    expected = _note_line(terminal, "AAA", "TRIM").split(" — ", 1)[1]

    rows_by_ticker = {row["ticker"]: row for row in body["grid"]["rows"]}
    assert [n["label"] for n in rows_by_ticker["AAA"]["notes"]] == ["TRIM"]
    assert rows_by_ticker["AAA"]["notes"][0]["text"] == expected
    assert rows_by_ticker["BBB"]["notes"] == []
    assert rows_by_ticker["CCC"]["notes"] == []


def test_a_yield_note_lands_on_its_own_reading_and_no_other(monkeypatch, tmp_path):
    readings = _grid_readings()
    result = ReviewResult(
        book=_book("retirement"), readings=list(readings), contexts=(), mismatched=(),
        levels=((), (), 0), chains=(), macro=(),
        yield_notes=(_yield_note(readings[1]),),  # BBB — a HOLD, not itself LOUD
    )
    body = _review_response(monkeypatch, tmp_path, result).json()

    rows_by_ticker = {row["ticker"]: row for row in body["grid"]["rows"]}
    assert [n["label"] for n in rows_by_ticker["BBB"]["notes"]] == ["YIELD"]
    assert [n["label"] for n in rows_by_ticker["AAA"]["notes"]] == ["TRIM"]
    assert rows_by_ticker["CCC"]["notes"] == []


def test_a_loud_row_with_a_yield_note_carries_both_loud_first(monkeypatch, tmp_path):
    readings = _grid_readings()
    yield_notes = (_yield_note(readings[0]),)  # AAA — already TRIM
    result = ReviewResult(
        book=_book("retirement"), readings=list(readings), contexts=(), mismatched=(),
        levels=((), (), 0), chains=(), macro=(), yield_notes=yield_notes,
    )
    body = _review_response(monkeypatch, tmp_path, result).json()
    terminal = render_module.render(list(readings), portfolio="retirement",
                                    as_of=date.fromisoformat(body["as_of"]),
                                    yield_notes=yield_notes)

    notes = next(row for row in body["grid"]["rows"] if row["ticker"] == "AAA")["notes"]
    assert [n["label"] for n in notes] == ["TRIM", "YIELD"]
    assert notes[0]["text"] == _note_line(terminal, "AAA", "TRIM").split(" — ", 1)[1]
    assert notes[1]["text"] == _note_line(terminal, "AAA", "YIELD").split(" — ", 1)[1]


def test_an_orphan_yield_note_fails_the_request_rather_than_vanishing(monkeypatch, tmp_path):
    """The direction `wire.review_document` builds its lookup in — keyed by reading, walked
    from the notes — makes this a `KeyError`, matching `render.py:156-160`'s own crash on the
    same data. The reverse direction would drop this note without a trace."""
    readings = _grid_readings()
    orphan = _reading("ZZZ", verdict=HOLD, price=1.0)
    result = ReviewResult(
        book=_book("retirement"), readings=list(readings), contexts=(), mismatched=(),
        levels=((), (), 0), chains=(), macro=(), yield_notes=(_yield_note(orphan),),
    )

    with pytest.raises(KeyError):
        _review_response(monkeypatch, tmp_path, result)


# ── the review header (#89) ─────────────────────────────────────────────────


def _result_with_book(book, readings=None, *, mismatched=()):
    return ReviewResult(
        book=book, readings=list(readings or _grid_readings()), contexts=(),
        mismatched=mismatched, levels=((), (), 0), chains=(), macro=(),
    )


def test_an_unpriced_row_is_marked_and_its_priced_siblings_are_not(monkeypatch, tmp_path):
    readings = _grid_readings()  # AAA priced, BBB priced, CCC unpriced
    body = _review_response(monkeypatch, tmp_path, _grid_result(readings)).json()

    unpriced_by_ticker = {row["ticker"]: row["unpriced"] for row in body["grid"]["rows"]}
    assert unpriced_by_ticker == {"AAA": False, "BBB": False, "CCC": True}


def test_mismatches_reach_the_header_and_match_render(monkeypatch, tmp_path):
    mismatched = (("AAA", 100.0, 20.0),)
    result = _result_with_book(_book("retirement"), mismatched=mismatched)
    body = _review_response(monkeypatch, tmp_path, result).json()

    assert body["header"]["mismatches"] == render_module.mismatch_lines(mismatched)


def test_a_clean_book_has_no_mismatches_in_the_header(monkeypatch, tmp_path):
    body = _review_response(monkeypatch, tmp_path, _grid_result(_grid_readings())).json()

    assert body["header"]["mismatches"] == []


def test_header_prices_is_present_on_every_response_including_an_empty_book(monkeypatch, tmp_path):
    body = _review_response(monkeypatch, tmp_path, _grid_result([]),
                            freshness=_fake_freshness("prices: fetched 3 hours ago")).json()

    assert body["header"]["prices"] == "prices: fetched 3 hours ago"


def test_written_reflects_the_books_age_and_is_none_without_one(monkeypatch, tmp_path):
    dated = _result_with_book(_book("retirement", updated=date(2025, 1, 1)))
    body = _review_response(monkeypatch, tmp_path, dated).json()
    as_of = date.fromisoformat(body["as_of"])
    assert body["header"]["written"] == render_module.written_text(
        dated.book.age_days(on=as_of))

    undated = _result_with_book(_book("retirement"))
    body = _review_response(monkeypatch, tmp_path, undated).json()
    assert body["header"]["written"] is None


def test_stale_banner_matches_render_for_a_stale_book_and_is_none_for_a_fresh_one(
    monkeypatch, tmp_path,
):
    stale = _result_with_book(_book("retirement", updated=date(2020, 1, 1)))
    body = _review_response(monkeypatch, tmp_path, stale).json()
    as_of = date.fromisoformat(body["as_of"])
    assert stale.book.is_stale(on=as_of)
    assert body["header"]["stale_banner"] == render_module.stale_banner(
        stale.book.age_days(on=as_of))

    fresh = _result_with_book(_book("retirement", updated=datetime.now(UTC).date()))
    body = _review_response(monkeypatch, tmp_path, fresh).json()
    as_of = date.fromisoformat(body["as_of"])
    assert not fresh.book.is_stale(on=as_of)
    assert body["header"]["stale_banner"] is None


def test_a_page_load_never_syncs_the_price_cache(monkeypatch, tmp_path):
    """AC6 / #84 story 37: `oracle.setups_sync.ensure_fresh` must never be reached from a page
    load. `api.review_for` is stubbed too — the real one reaches `listings.load_or_fetch`,
    which fetches over HTTP when the cache file is absent (`oracle/listings.py:62-70`), so an
    unstubbed version would break this test on a fresh clone regardless of the sync guard.
    Drives the real `mandate_review`, not an override, so its own lookup logic runs for real.
    """
    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")

    def _must_not_sync(*args, **kwargs):
        raise AssertionError("setups_sync.ensure_fresh must never run from a page load")

    monkeypatch.setattr(review_cli.setups_sync, "ensure_fresh", _must_not_sync)
    stub_result = _grid_result(_grid_readings())
    monkeypatch.setattr(api, "review_for", lambda books, **kw: [stub_result])

    app = api.create_app()
    app.dependency_overrides[api.mandate_books] = lambda: [_book("retirement")]
    app.dependency_overrides[api.data_freshness] = lambda: _fake_freshness()
    client = TestClient(app)

    response = client.get("/api/mandates/retirement/review")

    assert response.status_code == 200


def test_404_for_an_unknown_mandate(monkeypatch, tmp_path):
    """Drives the real `mandate_review`, only overriding `mandate_books` — proving the
    dependency is reachable and that a bad name never gets as far as `review_for`."""
    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")
    app = api.create_app()
    app.dependency_overrides[api.mandate_books] = lambda: [_book("retirement")]
    app.dependency_overrides[api.data_freshness] = lambda: _fake_freshness()
    client = TestClient(app)

    response = client.get("/api/mandates/nope/review")

    assert response.status_code == 404


# ── the Levels section (#91) ────────────────────────────────────────────────


def test_levels_rows_are_cell_by_cell_identical_to_the_terminal(monkeypatch, tmp_path):
    """AC1. `render_levels` interleaves a `LEVEL_GROUPS` label line between the two row
    blocks, so this walks the terminal's lines filtering those labels out rather than slicing
    a fixed window the way #87's grid test does."""
    standing = (_spot("AAA"), _spot("BBB", side=RESISTANCE))
    closing = (_spot("CCC", distance=0.02, side=RESISTANCE),)
    book = _book("retirement")
    result = ReviewResult(book=book, readings=[s.reading for s in (*standing, *closing)],
                          contexts=(), mismatched=(), levels=(standing, closing, 0),
                          chains=(), macro=())
    body = _review_response(monkeypatch, tmp_path, result).json()

    terminal = render_module.render_levels(standing, closing, 0, kinds=book.level_kinds)
    labels = set(render_module.LEVEL_GROUPS)
    row_lines = [line for line in terminal.splitlines()[3:] if line.strip() not in labels]

    wire_rows = [row for group in body["levels"]["groups"] for row in group["rows"]]
    for row_line, row in zip(row_lines, wire_rows, strict=True):
        assert re.split(r"\s{2,}", row_line.strip()) == row


def test_levels_columns_match_render_LEVEL_HEADERS(monkeypatch, tmp_path):
    body = _review_response(monkeypatch, tmp_path, _grid_result([])).json()
    assert body["levels"]["columns"] == list(render_module.LEVEL_HEADERS)


def test_a_30_deep_standing_group_arrives_uncapped_with_the_display_cap_and_withheld_count(
    monkeypatch, tmp_path,
):
    """AC2/AC3: the wire carries every row, not the terminal's cap; `shown`/`withheld` tell the
    browser what to slice. `withheld` matches `review.levels.cap` over the same groups, which
    is the assertion AC4's numeric half rests on."""
    standing = tuple(_spot(f"T{i}") for i in range(30))
    body = _review_response(
        monkeypatch, tmp_path, _grid_result([], levels=(standing, (), 0)),
    ).json()

    assert len(body["levels"]["groups"][0]["rows"]) == 30
    assert body["levels"]["shown"] == SHOWN
    assert body["levels"]["withheld"] == 18
    assert body["levels"]["withheld"] == cap(standing, (), limit=SHOWN)[2]


def test_a_levels_led_mandate_folds_the_standing_group_and_withheld_counts_only_the_closing_overflow(
    monkeypatch, tmp_path,
):
    """AC4: on a levels-led mandate the verdict grid already carries the standing group
    (ADR-0002), so it is folded out here and `withheld` counts only what the closing group is
    still hiding."""
    standing = tuple(_spot(f"T{i}") for i in range(5))
    closing = tuple(_spot(f"C{i}", distance=0.02, side=RESISTANCE) for i in range(20))

    sentiment = _review_response(
        monkeypatch, tmp_path, _grid_result([], levels=(standing, closing, 0), mandate=MANDATE),
    ).json()
    assert [g["label"] for g in sentiment["levels"]["groups"]] == list(render_module.LEVEL_GROUPS)

    levels_led = _review_response(
        monkeypatch, tmp_path,
        _grid_result([], levels=(standing, closing, 0), mandate=LEVELS_LED_MANDATE,
                    book_name="levels-led"),
    ).json()
    assert [g["label"] for g in levels_led["levels"]["groups"]] == ["closing in"]
    assert levels_led["levels"]["withheld"] == cap((), closing, limit=SHOWN)[2]


def test_an_empty_scan_carries_empty_note_and_no_groups(monkeypatch, tmp_path):
    body = _review_response(monkeypatch, tmp_path, _grid_result([])).json()
    assert body["levels"]["groups"] == []
    assert body["levels"]["empty_note"] == render_module.NOTHING_NEAR


# ── the alt-signal section (#92) ──────────────────────────────────────────


def test_altsignal_chains_and_macro_are_line_for_line_the_terminals_own(monkeypatch, tmp_path):
    """AC1. Partitions on `line.startswith("    ")` *before* stripping — the block's only
    structure is indent depth (2 spaces for a chain ticker and for `MACRO`, 4 for content), and
    stripping first makes a ticker line, the `MACRO` label and a content line
    indistinguishable. #91's `line.strip() not in labels` filter does not transfer — it works
    there because the groups carry labels."""
    chains = (
        ChainLine(reading=_reading("AAA", verdict=HOLD, price=100.0),
                 lines=("Chain A TVL: $1.00B", "Chain A stablecoins: $2.00B")),
        ChainLine(reading=_reading("BBB", verdict=HOLD, price=50.0),
                 lines=("Chain B TVL: $3.00B",)),
    )
    macro = (
        MacroRow(why="Fed decision", top=(("KXFED-26DEC-T3.75", 0.72),), others=0),
        MacroRow(why="BTC target", top=(("evt:strike-1", 0.5),), others=4),
    )
    result = _grid_result([], chains=chains, macro=macro)
    body = _review_response(monkeypatch, tmp_path, result).json()

    terminal = render_module.render_altsignal(chains, macro).splitlines()
    macro_idx = terminal.index(f"  {render_module.MACRO_LABEL}")

    parsed_chains: list[dict] = []
    for line in terminal[1:macro_idx]:
        if line.startswith("    "):
            parsed_chains[-1]["lines"].append(line.strip())
        elif line.startswith("  "):
            parsed_chains.append({"ticker": line.strip(), "lines": []})

    macro_lines = [line.strip() for line in terminal[macro_idx + 1:] if line.startswith("    ")]

    assert parsed_chains == body["altsignal"]["chains"]
    assert macro_lines == body["altsignal"]["macro"]


def test_altsignal_title_and_macro_label_are_renders_own_constants(monkeypatch, tmp_path):
    body = _review_response(monkeypatch, tmp_path, _grid_result([])).json()
    assert body["altsignal"]["title"] == render_module.ALTSIGNAL_TITLE
    assert body["altsignal"]["macro_label"] == render_module.MACRO_LABEL


def test_an_unconfigured_altsignal_result_carries_the_configured_empty_note(
    monkeypatch, tmp_path,
):
    body = _review_response(monkeypatch, tmp_path, _grid_result([])).json()
    assert body["altsignal"]["chains"] == []
    assert body["altsignal"]["macro"] == []
    assert body["altsignal"]["empty_note"] == render_module.NOTHING_CONFIGURED

    configured = _grid_result([], chains=(
        ChainLine(reading=_reading("AAA", verdict=HOLD, price=100.0), lines=("x",)),
    ))
    body = _review_response(monkeypatch, tmp_path, configured).json()
    assert body["altsignal"]["empty_note"] is None


def test_mandate_review_passes_a_populated_altsignal_config_through_to_review_for(
    monkeypatch, tmp_path,
):
    """AC2 + AC3, the load-bearing one. Drives the real `mandate_review`, overriding only
    `mandate_books`, `data_freshness` and the new `altsignal_cfg`, with `api.review_for`
    stubbed by a `fake` that mirrors `review_for`'s own branch. `fake` branches on the config
    being *populated* (`kw["altsignal_cfg"].chains`), not merely non-`None`, so an empty
    config fails here too.

    Be honest about its reach: this catches deleting the kwarg from `api.py`'s
    `mandate_review`, and a config that arrives empty. It cannot prove the View populates
    anything, because the populating is this fake's own re-implementation of
    `review.cli.review_for`'s branch — `test_cli.py`'s
    `test_review_for_fills_chains_and_macro_from_a_populated_config` is the other half, and
    neither is sufficient alone.
    """
    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")

    populated_cfg = review_cli.altsignal_config.AltSignalConfig(
        chains=(review_cli.altsignal_config.ChainEntry(asset="SOL", chain="solana"),),
        markets=(),
    )
    chain = ChainLine(reading=_reading("SOL", verdict=HOLD, price=100.0), lines=("x",))
    stub_result = _grid_result(_grid_readings())

    def _fake_review_for(books, *, as_of, altsignal_cfg=None, **kw):
        if altsignal_cfg is not None and altsignal_cfg.chains:
            return [stub_result._replace(chains=(chain,))]
        return [stub_result]

    monkeypatch.setattr(api, "review_for", _fake_review_for)

    app = api.create_app()
    app.dependency_overrides[api.mandate_books] = lambda: [_book("retirement")]
    app.dependency_overrides[api.data_freshness] = lambda: _fake_freshness()
    app.dependency_overrides[api.altsignal_cfg] = lambda: populated_cfg
    client = TestClient(app)

    response = client.get("/api/mandates/retirement/review")

    assert response.status_code == 200
    body = response.json()
    assert body["altsignal"]["chains"] == [{"ticker": "SOL", "lines": ["x"]}]


def test_the_altsignal_config_handed_to_review_for_is_the_dependencys_own_object(
    monkeypatch, tmp_path,
):
    """AC2: catches a future "helpfully" rebuilt config that would carry the same values but
    lose the point of a `Depends` — a test being able to swap the object out from under it."""
    monkeypatch.setattr(assets, "WEB_DIST", tmp_path / "no-dist")
    sentinel = review_cli.altsignal_config.AltSignalConfig(chains=(), markets=())
    stub_result = _grid_result(_grid_readings())
    captured = []

    def _fake_review_for(books, *, as_of, altsignal_cfg=None, **kw):
        captured.append(altsignal_cfg)
        return [stub_result]

    monkeypatch.setattr(api, "review_for", _fake_review_for)

    app = api.create_app()
    app.dependency_overrides[api.mandate_books] = lambda: [_book("retirement")]
    app.dependency_overrides[api.data_freshness] = lambda: _fake_freshness()
    app.dependency_overrides[api.altsignal_cfg] = lambda: sentinel
    client = TestClient(app)

    response = client.get("/api/mandates/retirement/review")

    assert response.status_code == 200
    assert captured[0] is sentinel


def test_the_default_altsignal_cfg_dependency_returns_a_populated_config():
    """Not "has the attributes" — a missing `cfg/altsignal.yaml` returns
    `AltSignalConfig(chains=(), markets=())`, which has both, so an attribute check would pass
    a wrong `CONFIG_DIR`, a renamed file, or a packaging move that shifts `cli.py`'s
    `parents[4]` — and ship the screen saying "nothing configured yet", #92's stated trap word
    for word. `cfg/altsignal.yaml` is committed and holds real chains and markets, so asserting
    non-empty is fresh-clone safe and needs no `needs_ore` marker. Duck-typed rather than
    `isinstance` — this test is about the seam's *answer*, not about coupling it to a boundary
    it does not need to know about.
    """
    cfg = api.altsignal_cfg()
    assert cfg.chains and cfg.markets


def test_the_headline_is_levels_headline_over_the_full_post_fold_groups(monkeypatch, tmp_path):
    standing = (_spot("AAA"),)
    closing = (_spot("BBB", distance=0.02, side=RESISTANCE),)
    book = _book("retirement")
    result = ReviewResult(book=book, readings=[], contexts=(), mismatched=(),
                          levels=(standing, closing, 0), chains=(), macro=())
    body = _review_response(monkeypatch, tmp_path, result).json()

    assert body["levels"]["headline"] == render_module.levels_headline(
        standing, closing, kinds=book.level_kinds)
