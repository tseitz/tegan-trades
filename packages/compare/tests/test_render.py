from datetime import UTC, datetime, timedelta

from compare import card, render
from oracle.altsignal_config import ProtocolEntry

NOW = datetime(2026, 9, 16, 6, 15, tzinfo=UTC)

HYPE = ProtocolEntry(
    asset="HYPE", llama_fees="hyperliquid", llama_tvl="hyperliquid",
    llama_oi=("hyperliquid-perps",), coingecko="hyperliquid",
    coingecko_derivatives=("hyperliquid",), venue="hyperliquid",
)
LIT = ProtocolEntry(
    asset="LIT", llama_fees="lighter", llama_tvl="lighter",
    llama_oi=("lighter-perps",), coingecko="lighter",
    coingecko_derivatives=("lighter",), venue="lighter",
)


def _present(value, observed_at=NOW):
    return card.Cell(state=card.PRESENT, value=value, observed_at=observed_at)


def _result(*, metrics, ratios=(), freshest=(NOW, NOW), oldest=(NOW, NOW)):
    return card.CompareResult(
        left=HYPE, right=LIT, metrics=metrics, ratios=ratios, freshest=freshest, oldest=oldest
    )


def test_renders_the_header_with_both_asset_names():
    result = _result(metrics=())
    text = render.render(result, now=NOW)
    assert "HYPE vs LIT" in text


def test_present_cell_renders_through_the_rows_own_formatter():
    row = card.MetricRow(
        label="Revenue, 30d", tier=card.TIER_1,
        left=_present(60_000_000), right=_present(4_000_000), format=card._fmt_usd,
    )
    text = render.render(_result(metrics=(row,)), now=NOW)
    assert "$60.00M" in text
    assert "$4.00M" in text


def test_each_non_present_state_renders_its_own_dash_text():
    rows = (
        card.MetricRow(label="Not fetched", tier=card.TIER_1, left=card.Cell(state=card.NOT_FETCHED),
                        right=_present(1), format=str),
        card.MetricRow(label="No free source", tier=card.TIER_3, left=card.Cell(state=card.NO_FREE_SOURCE),
                        right=card.Cell(state=card.NO_FREE_SOURCE), format=str),
        card.MetricRow(label="TVL", tier=card.TIER_2, left=card.Cell(state=card.NOT_COMPARABLE),
                        right=card.Cell(state=card.NOT_COMPARABLE), format=str),
    )
    text = render.render(_result(metrics=rows), now=NOW)
    assert "— not fetched" in text
    assert "— no free source" in text
    assert "— categories differ" in text


def test_tier_heading_prints_once_per_group_not_once_per_row():
    rows = (
        card.MetricRow(label="A", tier=card.TIER_1, left=_present(1), right=_present(1), format=str),
        card.MetricRow(label="B", tier=card.TIER_1, left=_present(1), right=_present(1), format=str),
        card.MetricRow(label="C", tier=card.TIER_2, left=_present(1), right=_present(1), format=str),
    )
    text = render.render(_result(metrics=rows), now=NOW)
    assert text.count("TIER 1") == 1
    assert text.count("TIER 2") == 1


def test_ratio_lines_render_with_their_caption():
    ratio = card.RatioLine(label="mcap / OI", left=2.0, right=1.0, caption="HYPE is the MORE expensive of the two per dollar of real risk")
    text = render.render(_result(metrics=(), ratios=(ratio,)), now=NOW)
    assert "mcap / OI" in text
    assert "2.00x" in text
    assert "MORE expensive" in text


def test_a_ratio_with_no_computable_side_renders_a_dash_not_a_crash():
    ratio = card.RatioLine(label="mcap / OI", left=None, right=None, caption="")
    text = render.render(_result(metrics=(), ratios=(ratio,)), now=NOW)
    assert "—" in text


def test_no_age_banner_when_both_sides_are_fresh():
    text = render.render(_result(metrics=(), freshest=(NOW, NOW)), now=NOW)
    assert "STALE" not in text


def test_age_banner_fires_when_either_sides_freshest_reading_is_past_the_threshold():
    stale = NOW - render.STALE_AFTER - timedelta(hours=1)
    text = render.render(_result(metrics=(), freshest=(stale, NOW)), now=NOW)
    assert "STALE" in text
    assert "HYPE" in text  # names the stale side, not just "something is stale"


def test_age_banner_fires_when_a_side_has_never_been_fetched():
    text = render.render(_result(metrics=(), freshest=(None, NOW)), now=NOW)
    assert "STALE" not in text  # a side with no data at all is covered by NOT_FETCHED cells,
    # not the age banner -- see render._age_banner's docstring reasoning in card.py
