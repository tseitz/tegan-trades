"""Where the roster moved, for the assets you could actually act on. Pure.

Reuses ``brain.retrieve`` rather than re-folding stances here. ``fold_stances`` already
collapses repeat statements to each person's current view per ``(person, asset, horizon)`` and
already computes ``flipped``; ``summarize_split`` already counts *people* rather than
statements. Re-deriving either would produce a second, subtly different answer to a question
the repo has answered once.

## Everything here is a delta, and that is what makes it safe

An absolute lean count is not reportable. It moves with corpus size, with who happened to
publish, and with how much of the corpus has been extracted — none of which is news. The
*change* is the news, and a change cancels most of the distortions: a stale view contributes
identically to both sides of the comparison, so ``brain.report``'s staleness labelling is not
needed here. This is why the section counts what moved and never prints a standing total.

## The abstention

The nightly caps ``brain-extract`` at 12 transcripts (``BRAIN_EXTRACT_LIMIT`` in
``scripts/nightly.sh`` — the command itself has no cap), so on an ordinary night new
extractions track new videos and a publication-dated delta is right. Run a backfill and that
breaks: a batch of 2025 videos lands at once and reads as the roster turning overnight, when
nothing moved at all.

So the fold is keyed on ``source.published_at`` — never ``extraction.extracted_at`` — and when
the night's *new extractions* span more than ``BACKFILL_SPAN_DAYS`` of publication dates, the
whole section is withheld with the reason attached. An explicit abstention is the deliverable
on such a night. A wrong story about sentiment is worse than no story, because it reads as a
signal rather than as an error.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import timedelta

from brain.report import is_stale
from brain.retrieve import Split, fold_stances, summarize_split
from core.canon import resolve_asset
from core.rank import parse_date

# How far back "moved" reaches. Overnight is too tight to be useful — most of the roster does
# not publish daily, so a one-day window would report nothing on most nights and the section
# would read as broken rather than as quiet.
#
# The cost of a window this wide is that one event sits inside it for seven nights. That is
# what ``unreported`` exists to cancel: the window decides what is RECENT, the memory decides
# what is NEW, and a daily email needs both. Narrowing the window instead would empty the
# section on most nights, which is the trade this number was chosen to avoid.
WINDOW_DAYS = 7

# How long a reported event is remembered. An event leaves the window ``WINDOW_DAYS`` after
# publication and is first reported on or after publication, so twice the window clears
# everything that could still be in play — with a week of slack for nights the digest did not
# run. Beyond that a key can only suppress an event that the window has already dropped.
MEMORY_DAYS = WINDOW_DAYS * 2

# Publication span across one night's new extractions that means a backfill rather than a
# night's videos. Two weeks is comfortably wider than any normal night and far narrower than a
# real backfill, which reaches back months.
#
# Sized against the nightly's ``BRAIN_EXTRACT_LIMIT`` of 12, which lives in
# ``scripts/nightly.sh`` and is overridable from the environment. Raise it there, or run
# ``brain-extract`` by hand, and this number wants revisiting — nothing here can detect that.
BACKFILL_SPAN_DAYS = 14

# Below this many newly-extracted stances the span test is not meaningful — one re-extracted
# old video is ordinary catch-up and must not silence a night that also carries real movement.
BACKFILL_MIN_ROWS = 2


@dataclass(frozen=True, slots=True)
class Flip:
    """One person who changed their lean on one asset inside the window."""
    person: str
    was: str
    now: str
    horizon: str
    published_at: str


@dataclass(frozen=True, slots=True)
class NewView:
    """A view on a horizon this person had not spoken on before.

    Its own category rather than folded into either of the others, because it is neither. It
    is not a flip — nothing was contradicted, and ``_fold_key`` keeps horizons apart precisely
    so a swing view and a macro view are never compared. It is not a new voice either, since
    the person was already on the record about this asset. Dropping it was the first behaviour
    here and it silently lost a real event: someone who held only a swing view turning bearish
    on ``macro`` is a statement worth reading, and it produced no line at all.
    """
    person: str
    lean: str
    horizon: str
    published_at: str


@dataclass(frozen=True, slots=True)
class AssetMove:
    asset: str
    before: Split
    after: Split
    flips: tuple[Flip, ...] = ()
    fresh_people: tuple[str, ...] = ()
    new_views: tuple[NewView, ...] = ()
    #: How many of the people counted in ``after`` last spoke past their own horizon.
    #:
    #: The counts are printed as context — "bearish leads 12 to 3" — and eleven of MSTR's
    #: twenty-nine voices were stale on 2026-08-26, as was 41% of the corpus. The stale ones
    #: are still *counted*: withholding them would silently redefine a number the reader has
    #: been reading for weeks. What this adds is that the age travels with it.
    stale_now: int = 0


@dataclass(frozen=True, slots=True)
class RosterDelta:
    moved: tuple[AssetMove, ...] = ()
    #: Why the section was suppressed, or ``None``. Set means: do not narrate, print this.
    withheld: str | None = None
    window_days: int = WINDOW_DAYS
    assets_considered: tuple[str, ...] = field(default_factory=tuple)


def moved(stances, registry, *, assets, on, window_days: int = WINDOW_DAYS,
          extracted_since: str | None = None) -> RosterDelta:
    """What changed on ``assets`` in the ``window_days`` before ``on``.

    ``assets`` is the scope — tonight's qualified candidates plus anything in the book. It is
    matched after canonicalisation, because the corpus says "ethereum" where the queue says
    "ETH" and comparing raw labels drops most of the movement on any multi-spelling asset.

    ``extracted_since`` bounds "which stances arrived tonight" for the backfill guard. ``None``
    skips the guard — the first digest has no previous run to bound it by.
    """
    wanted = {resolve_asset(a, registry)[0] for a in assets}
    relevant = [s for s in stances if resolve_asset(s.asset, registry)[0] in wanted]

    withheld = _backfill_reason(relevant, extracted_since=extracted_since)
    if withheld is not None:
        return RosterDelta(withheld=withheld, window_days=window_days,
                           assets_considered=tuple(sorted(wanted)))

    cutoff = on - timedelta(days=window_days)
    # The corpus as it stood at the start of the window. Folding a filtered list rather than
    # unwinding the fold, so both sides go through exactly the same collapse rules.
    before_stances = [s for s in relevant
                      if (d := parse_date(s.source.published_at)) is not None and d <= cutoff]

    after = _by_asset(fold_stances(relevant, registry))
    before = _by_asset(fold_stances(before_stances, registry))

    moves = []
    for asset in sorted(set(after) | set(before)):
        move = _move_for(asset, before.get(asset, []), after.get(asset, []),
                         cutoff=cutoff, on=on)
        if move is not None:
            moves.append(move)
    return RosterDelta(moved=tuple(moves), window_days=window_days,
                       assets_considered=tuple(sorted(wanted)))


def _by_asset(folded) -> dict:
    out: dict[str, list] = {}
    for entry in folded:
        out.setdefault(entry.asset_canonical, []).append(entry)
    return out


def _move_for(asset: str, before_folded, after_folded, *, cutoff, on=None) -> AssetMove | None:
    """One asset's movement, or ``None`` if it held still.

    A restatement is not movement. Someone repeating a view they already held is the most
    common event in this corpus, and reporting it would make the section fire every night and
    therefore mean nothing.
    """
    flips = tuple(
        Flip(person=f.person_canonical, was=f.previous.lean, now=f.current.lean,
             horizon=f.current.horizon or "?", published_at=f.current.source.published_at)
        for f in after_folded
        if f.flipped and _within(f.current.source.published_at, cutoff)
    )

    before_people = {f.person_canonical for f in before_folded}
    before_views = {(f.person_canonical, f.current.horizon) for f in before_folded}

    fresh = tuple(sorted(
        f.person_canonical for f in after_folded
        if f.person_canonical not in before_people
        and _within(f.current.source.published_at, cutoff)
    ))

    # Someone already on the record about this asset, speaking on a horizon they had not.
    # Excluded from ``fresh`` above by the person check, and not a flip because horizons are
    # never compared — so without this category the statement disappears entirely.
    new_views = tuple(
        NewView(person=f.person_canonical, lean=f.current.lean,
                horizon=f.current.horizon or "?",
                published_at=f.current.source.published_at)
        for f in after_folded
        if f.person_canonical in before_people
        and (f.person_canonical, f.current.horizon) not in before_views
        and _within(f.current.source.published_at, cutoff)
    )

    if not flips and not fresh and not new_views:
        return None
    stale = sum(1 for f in after_folded
                if is_stale(f.current.source.published_at, f.current.horizon, on))
    return AssetMove(asset=asset, before=summarize_split(before_folded),
                     after=summarize_split(after_folded), flips=flips, fresh_people=fresh,
                     new_views=new_views, stale_now=stale)


def _within(published_at, cutoff) -> bool:
    when = parse_date(published_at)
    return when is not None and when > cutoff


def _backfill_reason(stances, *, extracted_since: str | None) -> str | None:
    """Say why the section is being withheld, or ``None`` to proceed."""
    if extracted_since is None:
        return None
    arrived = [s for s in stances
               if (s.extraction.extracted_at or "") > extracted_since]
    if len(arrived) < BACKFILL_MIN_ROWS:
        return None

    dates = sorted(d for s in arrived
                   if (d := parse_date(s.source.published_at)) is not None)
    if not dates:
        return None
    span = (dates[-1] - dates[0]).days
    if span <= BACKFILL_SPAN_DAYS:
        return None
    return (f"withheld — tonight's {len(arrived)} new extractions span {span} days of "
            f"publication dates ({dates[0].isoformat()} to {dates[-1].isoformat()}). That is a "
            f"backfill, not a night's videos, and a lean delta over it would report movement "
            f"that did not happen.")


# ── saying a thing once ───────────────────────────────────────────────────────
#
# ``moved`` answers "what is recent". These answer "what have I not said yet", which is a
# different question and the one a *daily* email asks. Without them a video published on Monday
# produces an identical paragraph every morning until the following Monday, and the section
# teaches the reader to skip it.
#
# The three key builders below are the contract. ``event_keys`` and ``unreported`` both go
# through them, so the key written on Monday and the key checked on Tuesday cannot drift apart.


def _flip_key(asset: str, flip: Flip) -> str:
    return f"{asset}|flip|{flip.person}|{flip.horizon}|{flip.published_at}"


def _voice_key(asset: str, person: str) -> str:
    """No date in the key. A person is new to an asset exactly once, and the fold does not
    carry which statement made them new — so the date would have to be invented."""
    return f"{asset}|voice|{person}"


def _view_key(asset: str, view: NewView) -> str:
    return f"{asset}|horizon|{view.person}|{view.horizon}|{view.published_at}"


def event_keys(delta: RosterDelta) -> tuple[str, ...]:
    """Every reportable event in ``delta``, as stable strings. Pure."""
    keys: list[str] = []
    for move in delta.moved:
        keys.extend(_flip_key(move.asset, f) for f in move.flips)
        keys.extend(_voice_key(move.asset, p) for p in move.fresh_people)
        keys.extend(_view_key(move.asset, v) for v in move.new_views)
    return tuple(keys)


def unreported(delta: RosterDelta, seen) -> RosterDelta:
    """``delta`` with every event in ``seen`` removed, and any asset left empty dropped.

    ``before`` and ``after`` are deliberately **not** recomputed. They are the standing split,
    which is context the narrator needs to say what the change did — recomputing them against
    the filtered events would describe a roster that never existed.

    A withheld delta passes through untouched. The abstention is the deliverable on a backfill
    night, and filtering it would turn an explicit "I do not trust tonight's numbers" back into
    the silence it exists to replace.
    """
    if delta.withheld is not None or not seen:
        return delta

    moves = []
    for move in delta.moved:
        flips = tuple(f for f in move.flips if _flip_key(move.asset, f) not in seen)
        fresh = tuple(p for p in move.fresh_people if _voice_key(move.asset, p) not in seen)
        views = tuple(v for v in move.new_views if _view_key(move.asset, v) not in seen)
        if not flips and not fresh and not views:
            continue
        moves.append(replace(move, flips=flips, fresh_people=fresh, new_views=views))
    return replace(delta, moved=tuple(moves))


def remember(seen: dict, keys, *, on, memory_days: int = MEMORY_DAYS) -> dict:
    """``seen`` plus ``keys`` stamped ``on``, minus what is too old to matter. A new dict.

    The stamp is when a key was FIRST said, never refreshed. Refreshing it on every run would
    keep a key alive for as long as its event stayed in the window and the file would never
    prune.

    A stamp nothing can parse is **dropped, not kept**. Keeping it would suppress its event
    permanently and silently, which is the worse of the two failures available here; dropping
    it costs one repeated line and then self-heals on the next write.
    """
    cutoff = on - timedelta(days=memory_days)
    kept = {key: stamp for key, stamp in seen.items()
            if (d := parse_date(stamp)) is not None and d > cutoff}
    today = on.isoformat()
    return {**kept, **{key: today for key in keys if key not in kept}}


def payload(delta: RosterDelta) -> list[dict]:
    """What the narrator is given: counts and names, nothing else.

    Deliberately carries **no rationale text and no transcript reference**. The model's job is
    prose over numbers Python already computed, so the worst case is an awkward sentence rather
    than an invented claim — and a wrong number is a rendering bug with a test behind it.
    """
    return [
        {
            "asset": move.asset,
            "before": dict(move.before.counts),
            "after": dict(move.after.counts),
            "stale_now": move.stale_now,
            "people_now": {lean: names for lean, names in move.after.people.items() if names},
            "flips": [
                {"person": f.person, "was": f.was, "now": f.now,
                 "horizon": f.horizon, "on": f.published_at}
                for f in move.flips
            ],
            "new_voices": list(move.fresh_people),
            "new_horizons": [
                {"person": v.person, "lean": v.lean, "horizon": v.horizon, "on": v.published_at}
                for v in move.new_views
            ],
        }
        for move in delta.moved
    ]
