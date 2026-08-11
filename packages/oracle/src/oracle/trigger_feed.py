"""Assembling the trigger timeframe for one instrument, and deciding which rung sits above it.

Two jobs the pure layers cannot do, kept together because they share every input:

1. **Get H1 bars for a ref**, from whichever source prices it. Coinbase for crypto, Yahoo for
   everything else — the same split ``oracle.route`` already makes for daily bars.
2. **Say whether H12 or the daily is this instrument's setup rung**, which is measured from
   those bars rather than assigned from an asset class (``resample.straddles_the_split``).

**Bars are fetched for ``trade_symbol``, never ``symbol``.** ``^DJI`` is the Dow the roster's
theses are about; ``DIA`` is the only Dow anyone can buy, and the zone, stop and trigger are all
quoted on the thing an order reaches. ``assemble.load_daily`` already reads ``trade_symbol``
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

# How far back past the newest cached bar a refresh reaches. One bar would do for the forming-bar
# correction alone; a few hours also absorbs a source backfilling a gap it served as null earlier,
# which Yahoo does around holidays. Cheap either way — the cost of a request is per call, not per
# candle returned.
REFRESH_OVERLAP = timedelta(hours=6)


def fetch(ref, *, now: datetime | None = None, since: datetime | None = None,
          get_json=None) -> IntradaySeries | None:
    """H1 bars for ``ref.trade_symbol``, or None when its source serves no intraday.

    ``since`` narrows the request to what a warm cache is missing; it defaults to the full
    ``INTRADAY_DAYS`` window.

    Failure is None rather than an exception: a candidate whose trigger cannot be fetched is
    refused by the gate, and one unreachable symbol must not abort a whole queue build.
    """
    now = now or datetime.now(UTC)
    start = since or (now - timedelta(days=INTRADAY_DAYS))
    # ``DerivedRef`` — a ratio like ETH/BTC — carries neither, because it is computed from two
    # other series rather than fetched. There is no hourly chart of a ratio to ask for.
    symbol = getattr(ref, "trade_symbol", None)
    source = getattr(ref, "source", None)
    if symbol is None or source is None:
        return None
    kwargs = {"get_json": get_json} if get_json is not None else {}

    try:
        if source == coinbase.SOURCE:
            bars = coinbase.fetch_intraday(symbol, start, now, **kwargs)
        elif source == yahoo.SOURCE:
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
    return IntradaySeries(symbol=symbol, source=source, interval=H1, bars=tuple(bars))


def load_or_fetch(ref, *, root=cache.DATA_ROOT, now: datetime | None = None,
                  get_json=None) -> IntradaySeries | None:
    """Cached H1 for ``ref``, refreshed and merged. None when it cannot be had.

    Merge rather than overwrite, for a sharper reason than the daily cache has: the newest bar
    of any live fetch is still forming, so its close and volume are provisional and every run
    refetches a truer version of the same stamp.

    **Only the missing tail is requested.** 298 series are cached — 76 Coinbase and 222 Yahoo —
    and asking each for a full 60 days on every queue build is ~680 requests, which Yahoo
    rate-limits long before it finishes. The request deliberately *overlaps* the newest cached
    bar rather than starting after it: that bar was still forming when it was written, so
    re-asking is how the settled version arrives, and ``merge_intraday`` prefers incoming on a
    stamp collision.

    Nothing is trimmed. Dropping bars older than the window would have every run re-fetch what
    the last one just deleted.
    """
    now = now or datetime.now(UTC)
    source = getattr(ref, "source", None)
    symbol = getattr(ref, "trade_symbol", None)
    if source is None or symbol is None:
        return None
    cached = cache.load_intraday(source, H1, symbol, root=root)
    since = None
    if cached is not None and cached.span is not None:
        since = min(cached.span[1] - REFRESH_OVERLAP, now)

    fresh = fetch(ref, now=now, since=since, get_json=get_json)
    if fresh is not None:
        cache.merge_intraday(fresh, root=root)
    return cache.load_intraday(source, H1, symbol, root=root)


def load_cached(ref, *, root=cache.DATA_ROOT) -> IntradaySeries | None:
    """Whatever H1 is already on disk for ``ref``, without touching the network.

    The rung decision reads this rather than ``load_or_fetch``, and the split is what keeps
    ``setups`` usable. There are ~300 routable assets and a fetch each is ~300 round trips
    every run — a minute or more before the first candidate prints, whether the cache is warm
    or cold, because the cost is per request rather than per candle. The trigger itself only
    needs bars for assets that actually produced a candidate, which is a few dozen.

    Whether an instrument straddles 12:00 UTC does not change day to day, so reading a stale
    answer costs nothing; an asset with nothing cached simply takes the daily rung until
    ``fetch-prices`` warms it.
    """
    source = getattr(ref, "source", None)
    symbol = getattr(ref, "trade_symbol", None)
    if source is None or symbol is None:
        return None
    return cache.load_intraday(source, H1, symbol, root=root)


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
