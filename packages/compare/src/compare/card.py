"""The seam: two protocol identities in, one ``CompareResult`` out.

Pure — no network I/O of its own. ``store_read`` is injected the same way `review.altsignal`
injects it, so tests never touch a file or the network. See ADR-0004 for why this logic lives
here and not in ``core``: nothing but `compare` needs a tier table or a ratio line.

Every reading is looked up through `oracle.altsignal_store`, keyed by the identifiers
`ProtocolEntry` carries — never by ticker, per `core.altsignal.AltSignalReading.key`'s own
contract. Where an identifier is a list (`llama_oi`, `coingecko_derivatives`), the latest
reading under every key is summed, and how many keys actually had data rides along on
`ProtocolReadings` so a row can say when a comparison summed a different number of rows on
each side.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NamedTuple

from oracle import altsignal_store
from oracle.altsignal_config import AltSignalConfig, ProtocolEntry

SOURCE_DEFILLAMA = "defillama"
SOURCE_COINGECKO = "coingecko"

# Four cell states, not two. Collapsing any pair re-introduces the bug the card exists to
# avoid: NOT_FETCHED and NO_FREE_SOURCE both print as "no number", but one is a property of
# this machine (run `fetch-altsignal`) and the other is a property of the world (nobody serves
# it free) — conflating them tells a reader "no free source" when the truth is "you haven't
# fetched yet", a confident wrong answer. NOT_COMPARABLE is the TVL-only refusal: two real
# numbers that must not be read side by side because they measure different things.
PRESENT = "present"
NO_FREE_SOURCE = "no_free_source"
NOT_FETCHED = "not_fetched"
NOT_COMPARABLE = "not_comparable"

TIER_1 = 1
TIER_2 = 2
TIER_3 = 3


@dataclass(frozen=True, slots=True)
class Cell:
    """One protocol's value for one metric, or the reason it has none.

    ``value`` and ``observed_at`` are only meaningful when ``state`` is ``PRESENT`` — never
    ``0`` for anything else, which is the whole point of a four-state cell instead of an
    optional number.
    """
    state: str
    value: float | str | dict | None = None
    observed_at: datetime | None = None


_NO_FREE_SOURCE_CELL = Cell(state=NO_FREE_SOURCE)
_NOT_FETCHED_CELL = Cell(state=NOT_FETCHED)
_NOT_COMPARABLE_CELL = Cell(state=NOT_COMPARABLE)


@dataclass(frozen=True, slots=True)
class MetricRow:
    """One line of the tier table, already resolved for both protocols.

    ``format`` turns a ``PRESENT`` cell's raw value into text — carried on the row rather than
    hard-coded in the renderer, because ``Unlock schedule`` formats as a sentence and every
    other row as a number, and the row is what knows which.
    """
    label: str
    tier: int
    left: Cell
    right: Cell
    format: Callable[[Any], str]
    note: str = ""


@dataclass(frozen=True, slots=True)
class RatioLine:
    """One derived ratio, for both protocols, with a caption naming the side it favours.

    The caption is derived from the computed ratio and the two assets' names, never a fixed
    string — a fixed caption written for one pair would confidently assert the wrong side once
    a caller passes a different pair (AC1: "not hard-wired to one pair").
    """
    label: str
    left: float | None
    right: float | None
    caption: str


class UnknownAssetError(LookupError):
    """Raised by `compare_for` when an asset has no `protocols:` row in `cfg/altsignal.yaml`."""


class Observation(NamedTuple):
    value: float | str | dict
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class ProtocolReadings:
    """Every stored reading for one protocol, gathered once and read by every row builder.

    ``open_interest``/``volume_24h`` are already summed across every configured
    `coingecko_derivatives` id — see the module docstring — and the paired ``*_rows`` field is
    how many of those ids actually had data, so a row can flag when the two sides of a
    comparison summed a different number of rows.
    """
    entry: ProtocolEntry
    fees_30d: Observation | None
    revenue_30d: Observation | None
    tvl: Observation | None
    category: Observation | None
    unlock_schedule: Observation | None
    market_cap: Observation | None
    fdv: Observation | None
    circulating_supply: Observation | None
    total_supply: Observation | None
    open_interest: Observation | None
    open_interest_rows: int
    volume_24h: Observation | None
    volume_24h_rows: int


class CompareResult(NamedTuple):
    """Everything the compare view can say about two protocols, from a single `compare_for` call.

    Bundled rather than left as separate return values so a renderer never has to reassemble
    the same arguments a second time — see `review.cli.ReviewResult`'s docstring for the
    precedent ADR-0005 set and the reason it applies verbatim here: a named field can be added
    without breaking any caller that does not read it.

    ``freshest``/``oldest`` are ``(left, right)`` pairs of the newest and oldest
    ``observed_at`` across every reading gathered for that side — `None` when a side has no
    stored readings at all. The renderer uses ``freshest`` to decide whether to print an age
    banner; nothing here decides that on its behalf, the same separation `review.cli.ReviewResult`
    draws between gathering data and deciding how much of it fits on a screen.
    """
    left: ProtocolEntry
    right: ProtocolEntry
    metrics: tuple[MetricRow, ...]
    ratios: tuple[RatioLine, ...]
    freshest: tuple[datetime | None, datetime | None]
    oldest: tuple[datetime | None, datetime | None]


def _latest(store_read, *, source: str, kind: str, key: str) -> Observation | None:
    rows = store_read(source=source, kind=kind, key=key)
    if not rows:
        return None
    latest = rows[-1]  # altsignal_store.read is time-ordered ascending.
    return Observation(value=latest.value, observed_at=latest.observed_at)


def _latest_summed(store_read, *, source: str, kind: str, keys) -> tuple[Observation | None, int]:
    """The sum of the latest reading under every key, and how many keys had one.

    Never pre-summed at the adapter — `oracle.altsignal.coingecko.fetch` stores one reading per
    key precisely so this can count rows rather than trust a scalar that already lost them.
    """
    values: list[float] = []
    freshest: datetime | None = None
    rows_seen = 0
    for key in keys:
        obs = _latest(store_read, source=source, kind=kind, key=key)
        if obs is None:
            continue
        rows_seen += 1
        values.append(obs.value)
        if freshest is None or obs.observed_at > freshest:
            freshest = obs.observed_at
    if not values:
        return None, 0
    return Observation(value=sum(values), observed_at=freshest), rows_seen


def _gather(entry: ProtocolEntry, *, store_read) -> ProtocolReadings:
    oi, oi_rows = _latest_summed(
        store_read, source=SOURCE_COINGECKO, kind="open_interest", keys=entry.coingecko_derivatives
    )
    volume, volume_rows = _latest_summed(
        store_read, source=SOURCE_COINGECKO, kind="volume_24h", keys=entry.coingecko_derivatives
    )
    return ProtocolReadings(
        entry=entry,
        fees_30d=_latest(store_read, source=SOURCE_DEFILLAMA, kind="protocol_fees_30d", key=entry.llama_fees),
        revenue_30d=_latest(
            store_read, source=SOURCE_DEFILLAMA, kind="protocol_revenue_30d", key=entry.llama_fees
        ),
        tvl=_latest(store_read, source=SOURCE_DEFILLAMA, kind="protocol_tvl", key=entry.llama_tvl),
        category=_latest(store_read, source=SOURCE_DEFILLAMA, kind="protocol_category", key=entry.llama_tvl),
        unlock_schedule=_latest(
            store_read, source=SOURCE_DEFILLAMA, kind="unlock_schedule", key=entry.llama_fees
        ),
        market_cap=_latest(store_read, source=SOURCE_COINGECKO, kind="market_cap", key=entry.coingecko),
        fdv=_latest(store_read, source=SOURCE_COINGECKO, kind="fdv", key=entry.coingecko),
        circulating_supply=_latest(
            store_read, source=SOURCE_COINGECKO, kind="circulating_supply", key=entry.coingecko
        ),
        total_supply=_latest(store_read, source=SOURCE_COINGECKO, kind="total_supply", key=entry.coingecko),
        open_interest=oi,
        open_interest_rows=oi_rows,
        volume_24h=volume,
        volume_24h_rows=volume_rows,
    )


def _span(readings: ProtocolReadings) -> tuple[datetime | None, datetime | None]:
    timestamps = [
        obs.observed_at
        for obs in (
            readings.fees_30d, readings.revenue_30d, readings.tvl, readings.category,
            readings.unlock_schedule, readings.market_cap, readings.fdv,
            readings.circulating_supply, readings.total_supply,
            readings.open_interest, readings.volume_24h,
        )
        if obs is not None
    ]
    if not timestamps:
        return None, None
    return max(timestamps), min(timestamps)


# ------------------------------------------------------------------------------- formatting


def _fmt_usd(value: float) -> str:
    for threshold, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= threshold:
            return f"${value / threshold:,.2f}{suffix}"
    return f"${value:,.0f}"


def _fmt_turnover(value: float) -> str:
    return f"{value:.2f}x/day"


def _fmt_pct(value: float) -> str:
    return f"{value:.1%}"


def _fmt_unlock(value: dict) -> str:
    end = value.get("documented_end")
    end_text = end.split("T")[0] if isinstance(end, str) else "unknown"
    tbd = value.get("tbd_amount")
    tbd_text = f", {tbd:,.0f} TBD" if tbd else ""
    return (
        f"{value['unlocked_30d']:,.0f} unlocked/30d, {value['scheduled_90d']:,.0f} scheduled/90d"
        f"{tbd_text}, documented through {end_text}"
    )


# ------------------------------------------------------------------------------- tier rows


def _obs_cell(obs: Observation | None) -> Cell:
    return _NOT_FETCHED_CELL if obs is None else Cell(state=PRESENT, value=obs.value, observed_at=obs.observed_at)


def _rows_suffix(base_note: str, left: ProtocolReadings, right: ProtocolReadings, rows_field: str) -> str:
    """``base_note``, with a clause naming which side(s) summed more than one row appended.

    Returns bare text, never pre-wrapped in parens — `render.render` wraps a row's whole
    ``note`` in one set of parens, so wrapping here too would nest them.
    """
    parts = [
        f"{r.entry.asset}: {getattr(r, rows_field)} rows summed"
        for r in (left, right)
        if getattr(r, rows_field) > 1
    ]
    if not parts:
        return base_note
    extra = ", ".join(parts)
    return f"{base_note}; {extra}" if base_note else extra


def _field_row(label: str, tier: int, field: str, fmt, note: str = ""):
    def build(left: ProtocolReadings, right: ProtocolReadings) -> MetricRow:
        return MetricRow(
            label=label, tier=tier,
            left=_obs_cell(getattr(left, field)), right=_obs_cell(getattr(right, field)),
            format=fmt, note=note,
        )
    return build


def _open_interest_row(left: ProtocolReadings, right: ProtocolReadings) -> MetricRow:
    return MetricRow(
        label="Open interest", tier=TIER_1,
        left=_obs_cell(left.open_interest), right=_obs_cell(right.open_interest),
        format=_fmt_usd,
        note=_rows_suffix("CoinGecko /derivatives, one source both sides", left, right, "open_interest_rows"),
    )


def _volume_row(left: ProtocolReadings, right: ProtocolReadings) -> MetricRow:
    return MetricRow(
        label="Volume, 24h", tier=TIER_2,
        left=_obs_cell(left.volume_24h), right=_obs_cell(right.volume_24h),
        format=_fmt_usd, note=_rows_suffix("", left, right, "volume_24h_rows"),
    )


def _tvl_row(left: ProtocolReadings, right: ProtocolReadings) -> MetricRow:
    """Refuses to compare TVL across two different DefiLlama categories — Research §4:
    for a Derivatives venue and a Lending market it means four different quantities under one
    heading. Only fires when both categories were actually fetched and are known to differ; a
    missing category leaves the numeric cells as they are, since "unconfirmed" is not the same
    claim as "confirmed to differ"."""
    left_cell, right_cell = _obs_cell(left.tvl), _obs_cell(right.tvl)
    if left.category is not None and right.category is not None and left.category.value != right.category.value:
        return MetricRow(
            label="TVL", tier=TIER_2, left=_NOT_COMPARABLE_CELL, right=_NOT_COMPARABLE_CELL,
            format=_fmt_usd, note="bridged collateral, not lending liquidity — categories differ",
        )
    return MetricRow(
        label="TVL", tier=TIER_2, left=left_cell, right=right_cell,
        format=_fmt_usd, note="bridged collateral, not lending liquidity",
    )


def _turnover_row(left: ProtocolReadings, right: ProtocolReadings) -> MetricRow:
    def cell(r: ProtocolReadings) -> Cell:
        if r.volume_24h is None or r.open_interest is None or not r.open_interest.value:
            return _NOT_FETCHED_CELL
        value = r.volume_24h.value / r.open_interest.value
        observed_at = min(r.volume_24h.observed_at, r.open_interest.observed_at)
        return Cell(state=PRESENT, value=value, observed_at=observed_at)
    return MetricRow(label="Turnover (vol/OI)", tier=TIER_2, left=cell(left), right=cell(right), format=_fmt_turnover)


def _float_share_row(left: ProtocolReadings, right: ProtocolReadings) -> MetricRow:
    def cell(r: ProtocolReadings) -> Cell:
        if r.circulating_supply is None or r.total_supply is None or not r.total_supply.value:
            return _NOT_FETCHED_CELL
        value = r.circulating_supply.value / r.total_supply.value
        observed_at = min(r.circulating_supply.observed_at, r.total_supply.observed_at)
        return Cell(state=PRESENT, value=value, observed_at=observed_at)
    return MetricRow(label="Float share", tier=TIER_2, left=cell(left), right=cell(right), format=_fmt_pct)


def _tier3_row(label: str):
    def build(_left: ProtocolReadings, _right: ProtocolReadings) -> MetricRow:
        return MetricRow(label=label, tier=TIER_3, left=_NO_FREE_SOURCE_CELL, right=_NO_FREE_SOURCE_CELL, format=str)
    return build


# Order mirrors the #54 prototype's `METRICS` — the AC-pinned ordering: tier 1 first in the
# research doc's stated ranking, tier 2 after, tier 3 (never free) last.
_ROWS = (
    _field_row("Revenue, 30d", TIER_1, "revenue_30d", _fmt_usd),
    _open_interest_row,
    _field_row("Market cap", TIER_1, "market_cap", _fmt_usd),
    _field_row("Unlock schedule", TIER_1, "unlock_schedule", _fmt_unlock,
               note="the field that can reverse the other three"),
    _field_row("Fees, 30d", TIER_2, "fees_30d", _fmt_usd),
    _volume_row,
    _tvl_row,
    _turnover_row,
    _float_share_row,
    _field_row("FDV", TIER_2, "fdv", _fmt_usd),
    _tier3_row("Active users"),
    _tier3_row("Liquidations"),
    _tier3_row("Book depth"),
)


# ------------------------------------------------------------------------------- ratio lines


def _divide(numerator: Observation | None, denominator: Observation | None) -> float | None:
    if numerator is None or denominator is None or not denominator.value:
        return None
    return numerator.value / denominator.value


def _cheaper_caption(left_entry: ProtocolEntry, right_entry: ProtocolEntry, left: float | None, right: float | None) -> str:
    if left is None or right is None:
        return ""
    cheaper = left_entry if left < right else right_entry
    return f"naive read: {cheaper.asset} cheaper"


def _real_risk_caption(left_entry: ProtocolEntry, right_entry: ProtocolEntry, left: float | None, right: float | None) -> str:
    if left is None or right is None:
        return ""
    pricier = left_entry if left > right else right_entry
    return f"{pricier.asset} is the MORE expensive of the two per dollar of real risk"


def _mcap_oi_ratio(left: ProtocolReadings, right: ProtocolReadings) -> RatioLine:
    lv, rv = _divide(left.market_cap, left.open_interest), _divide(right.market_cap, right.open_interest)
    return RatioLine(label="mcap / OI", left=lv, right=rv, caption=_real_risk_caption(left.entry, right.entry, lv, rv))


def _mcap_revenue_ratio(left: ProtocolReadings, right: ProtocolReadings) -> RatioLine:
    lv, rv = _divide(left.market_cap, left.revenue_30d), _divide(right.market_cap, right.revenue_30d)
    return RatioLine(label="mcap / revenue", left=lv, right=rv, caption=_cheaper_caption(left.entry, right.entry, lv, rv))


def _fdv_revenue_ratio(left: ProtocolReadings, right: ProtocolReadings) -> RatioLine:
    lv, rv = _divide(left.fdv, left.revenue_30d), _divide(right.fdv, right.revenue_30d)
    return RatioLine(label="FDV / revenue", left=lv, right=rv, caption=_cheaper_caption(left.entry, right.entry, lv, rv))


_RATIOS = (_mcap_oi_ratio, _mcap_revenue_ratio, _fdv_revenue_ratio)


def compare_for(
    left: str, right: str, *, altsignal_cfg: AltSignalConfig, store_read=altsignal_store.read
) -> CompareResult:
    """The whole card for two assets, looked up in `cfg/altsignal.yaml`'s `protocols:` block.

    Takes assets as arguments rather than a fixed pair (AC: "not hard-wired to one pair"), and
    raises `UnknownAssetError` — named, not a bare `KeyError` — when either has no configured
    `protocols:` row.
    """
    by_asset = {entry.asset: entry for entry in altsignal_cfg.protocols}
    missing = [asset for asset in (left, right) if asset not in by_asset]
    if missing:
        raise UnknownAssetError(
            f"{', '.join(missing)} has no protocols: row in cfg/altsignal.yaml"
        )

    left_readings = _gather(by_asset[left], store_read=store_read)
    right_readings = _gather(by_asset[right], store_read=store_read)

    metrics = tuple(build(left_readings, right_readings) for build in _ROWS)
    ratios = tuple(build(left_readings, right_readings) for build in _RATIOS)

    left_freshest, left_oldest = _span(left_readings)
    right_freshest, right_oldest = _span(right_readings)

    return CompareResult(
        left=by_asset[left], right=by_asset[right], metrics=metrics, ratios=ratios,
        freshest=(left_freshest, right_freshest), oldest=(left_oldest, right_oldest),
    )
