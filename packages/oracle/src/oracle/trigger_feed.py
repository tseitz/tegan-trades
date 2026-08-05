"""Assembling the trigger timeframe for one instrument, and deciding which rung sits above it.

Two jobs the pure layers cannot do, kept together because they share every input:

1. **Get H1 bars for a ref**, from whichever source prices it. Coinbase for crypto, Yahoo for
   everything else — the same split ``oracle.route`` already makes for daily bars.
2. **Say whether H12 or the daily is this instrument's setup rung**, which is measured from
   those bars rather than assigned from an asset class (``resample.straddles_the_split``).

**Bars are fetched for ``trade_symbol``, never ``symbol``.** ``^DJI`` is the Dow the roster's
theses are about; ``DIA`` is the only Dow anyone can buy, and the zone, stop and trigger are all
quoted on the thing an order reaches. ``setups_cli._load_daily`` already reads ``trade_symbol``
for exactly this reason; getting it wrong here would compute a trigger on one instrument and
place it on another, which is the failure ``cfg/venue_map.yaml``'s header exists to prevent.

**Only ``trade_symbol`` legs need H1 at all.** ``oracle.plan`` fetches both legs of a proxied
asset because ``score-roster`` grades the priced one — but grading reads daily closes and has no
use for an hourly bar, so the intraday fetch is half the work of the daily one on those rows.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.setups import DAILY, H12

from oracle import cache
from oracle.intraday import H1, IntradaySeries
from oracle.resample import straddles_the_split, to_h12
from oracle.sources import coinbase, yahoo

# How much hourly history to hold. The trigger needs enough for a 14-bar ATR window, the swing
# that sets the stop, and the ``PARTICIPATION_WINDOW`` of 80 bars behind the displacement candle
# — call it 100 bars of margin over the longest of those. 60 days is ~1,440 crypto bars and ~640
# equity bars, comfortably past that, and costs ~160KB per crypto symbol on disk (measured: 80KB
# for 30 days of BTC-USD). Asking for years would be free accuracy nowhere and real cost here:
# nothing in the trigger looks further back than a few days.
INTRADAY_DAYS = 60


def fetch(ref, *, now: datetime | None = None,
          get_json=None) -> IntradaySeries | None:
    """H1 bars for ``ref.trade_symbol``, or None when its source serves no intraday.

    Failure is None rather than an exception: a candidate whose trigger cannot be fetched is
    refused by the gate, and one unreachable symbol must not abort a whole queue build.
    """
    now = now or datetime.now(UTC)
    start = now - timedelta(days=INTRADAY_DAYS)
    symbol = ref.trade_symbol
    kwargs = {"get_json": get_json} if get_json is not None else {}

    try:
        if ref.source == coinbase.SOURCE:
            bars = coinbase.fetch_intraday(symbol, start, now, **kwargs)
        elif ref.source == yahoo.SOURCE:
            bars = yahoo.fetch_intraday(symbol, start, now, **kwargs)
        else:
            # Kraken and the derived-ratio refs have no intraday adapter. Not an error — the
            # gate reads None as "cannot be computed" and declines to offer, which is the
            # decided behaviour for a candidate we cannot confirm an entry on.
            return None
    except Exception:  # noqa: BLE001 - unreachable is "not measured", not a crash
        return None

    if not bars:
        return None
    return IntradaySeries(symbol=symbol, source=ref.source, interval=H1, bars=tuple(bars))


def load_or_fetch(ref, *, root=cache.DATA_ROOT, now: datetime | None = None,
                  get_json=None) -> IntradaySeries | None:
    """Cached H1 for ``ref``, refreshed and merged. None when it cannot be had.

    Merge rather than overwrite, for a sharper reason than the daily cache has: the newest bar
    of any live fetch is still forming, so its close and volume are provisional and every run
    refetches a truer version of the same stamp.
    """
    fresh = fetch(ref, now=now, get_json=get_json)
    if fresh is not None:
        cache.merge_intraday(fresh, root=root)
    return cache.load_intraday(ref.source, H1, ref.trade_symbol, root=root)


def setup_rung(hourly: IntradaySeries | None) -> tuple[str, IntradaySeries | None]:
    """Which timeframe supplies this instrument's zones, and the bars for it if it is H12.

    Returns ``(core.setups.H12, series)`` for anything trading on both sides of 12:00 UTC, and
    ``(core.setups.DAILY, None)`` otherwise. Note the returned *rung* is a zone-timeframe tag
    (``"h12"``) while the returned *series* carries an interval label (``"12h"``) — two
    same-named constants holding different strings, deliberately, since one lands in
    ``Candidate.key`` and the other in a cache path — the daily bars already exist in the price cache and this
    module has no business re-deriving them.

    An asset gets one rung or the other, never both: *"the H12 is just the daily, two H12
    candles are one daily candle"*, so carrying both would be a 2x step in the middle of the
    hierarchy holding no new information.
    """
    if hourly is None or not straddles_the_split(hourly):
        return DAILY, None
    return H12, to_h12(hourly)
