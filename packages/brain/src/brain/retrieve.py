"""Hybrid lookup — the structured leg and the evidence leg, merged.

Answering "where is my roster on ETH, where do they disagree, and what changed?" needs
two things that neither leg supplies alone. Aggregation across people ("5 bullish,
2 bearish") is a *count*, and a vector search returns passages, not counts. But a
credible answer needs real quotes, which counts can't produce. So:

- the **structured leg** folds `core.stance.Stance` records to each person's current view
- the **evidence leg** is injected as `search_fn` — a callable over the embedding index

Both legs join on `transcript_ref`. This module is pure: no file, network, or database
access anywhere. The caller supplies the loaded stances and a search callable.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from core.canon import Registry, resolve_asset, resolve_person
from core.rank import parse_date
from core.stance import Stance

LEANS = ("bullish", "bearish", "neutral", "uncertain")


@dataclass(frozen=True)
class FoldedStance:
    """One person's *current* view on one asset at one horizon, plus the view it
    replaced — so "what changed" is answerable without a second pass."""
    current: Stance
    previous: Stance | None
    restated: int                 # how many older statements folded away
    restated_since: str | None    # published_at of the oldest folded statement
    person_canonical: str
    asset_canonical: str

    @property
    def flipped(self) -> bool:
        return self.previous is not None and self.previous.lean != self.current.lean


@dataclass(frozen=True)
class Split:
    counts: dict[str, int] = field(default_factory=dict)
    people: dict[str, list[str]] = field(default_factory=dict)
    total_people: int = 0


@dataclass(frozen=True)
class RosterView:
    """What the roster currently thinks about one asset — the answer shape."""
    asset: str | None
    folded: list[FoldedStance] = field(default_factory=list)
    split: Split = field(default_factory=Split)
    evidence: list = field(default_factory=list)
    changed: list[FoldedStance] = field(default_factory=list)
    # canonical feed name -> the people actually speaking on it. Attribution is
    # feed-level, so any consumer printing "5 bullish, 2 bearish" MUST surface this
    # or a two-person feed reads as two independent opinions.
    multi_author: dict[str, list[str]] = field(default_factory=dict)


def _fold_key(person_canonical: str, asset_canonical: str, stance: Stance) -> tuple:
    """`(person, asset, horizon)` — deliberately WITHOUT `lean`.

    This is the decisive divergence from `distill.triage_cli.collapse_restatements`,
    where `direction` IS part of the key because a long and a short are different calls.
    Here the lean *changing* is the signal we want, so keying on it would split every
    flip into two unrelated entries and make "what changed" unanswerable.

    `horizon` stays in the key for the same reason `timeframe` does over there, and it
    is not hypothetical: observed live, Cowen holds BTC.D bullish on `swing` and bearish
    on `macro` in the same video. Folding those together would invent a flip.
    """
    return (person_canonical, asset_canonical, stance.horizon)


def fold_stances(stances: list[Stance], registry: Registry) -> list[FoldedStance]:
    """Collapse repeat statements to each person's current view per (asset, horizon).

    The survivor is always the most recent, never the "best" — this answers *what do
    they think now*. `restated`/`restated_since` always describe the full history, so
    folding is never silent truncation.

    Undated stances are never folded: with no date there is no way to say which
    statement is current. They are kept as their own entries rather than dropped,
    mirroring `collapse_restatements`.
    """
    groups: dict[tuple, list[tuple[str, str, Stance]]] = {}
    singles: list[FoldedStance] = []

    for s in stances:
        person_c, _ = resolve_person(s.source.person, registry)
        asset_c, _, _ = resolve_asset(s.asset, registry)
        if parse_date(s.source.published_at) is None:
            singles.append(FoldedStance(
                current=s, previous=None, restated=0, restated_since=None,
                person_canonical=person_c, asset_canonical=asset_c,
            ))
            continue
        groups.setdefault(_fold_key(person_c, asset_c, s), []).append((person_c, asset_c, s))

    folded: list[FoldedStance] = []
    for members in groups.values():
        # Newest first. `id` breaks ties so same-day statements fold deterministically
        # rather than depending on corpus walk order.
        members.sort(key=lambda m: (m[2].source.published_at, m[2].id), reverse=True)
        person_c, asset_c, current = members[0]
        folded.append(FoldedStance(
            current=current,
            previous=members[1][2] if len(members) > 1 else None,
            restated=len(members) - 1,
            restated_since=members[-1][2].source.published_at if len(members) > 1 else None,
            person_canonical=person_c,
            asset_canonical=asset_c,
        ))
    return folded + singles


def summarize_split(folded: list[FoldedStance]) -> Split:
    """Count *people*, not statements — one voice restating a view 20 times is still
    one voice. All four leans are always reported, including zeros, so a caller can't
    mistake an absent key for an absent question."""
    counts = dict.fromkeys(LEANS, 0)
    people: dict[str, list[str]] = {lean: [] for lean in LEANS}
    for f in folded:
        lean = f.current.lean
        if f.person_canonical not in people[lean]:
            people[lean].append(f.person_canonical)
            counts[lean] += 1
    return Split(
        counts=counts,
        people={k: sorted(v) for k, v in people.items()},
        total_people=len({f.person_canonical for f in folded}),
    )


def retrieve(
    *,
    stances: list[Stance],
    registry: Registry,
    asset: str | None = None,
    person: str | None = None,
    since: str | None = None,
    k: int = 10,
    search_fn: Callable | None = None,
) -> RosterView:
    """Structured leg + evidence leg, filtered by the same facets and merged.

    `search_fn(*, k, person, since, assets) -> list[hit]` is injected; hits are duck-typed
    and only need `.transcript_ref` and `.score`. Passing `search_fn=None` returns the
    structured leg alone — the embedding index may not be built yet, and a roster split
    is still worth answering without quotes.
    """
    asset_c = None
    if asset is not None:
        asset_c, _, _ = resolve_asset(asset, registry)
    person_c = resolve_person(person, registry)[0] if person is not None else None

    selected = []
    for s in stances:
        if asset_c is not None and resolve_asset(s.asset, registry)[0] != asset_c:
            continue
        if person_c is not None and resolve_person(s.source.person, registry)[0] != person_c:
            continue
        if since is not None and (s.source.published_at or "") < since:
            continue
        selected.append(s)

    folded = fold_stances(selected, registry)
    folded.sort(key=lambda f: (f.current.source.published_at or "", f.person_canonical),
                reverse=True)

    evidence = []
    if search_fn is not None:
        hits = search_fn(k=k, person=person_c or person, since=since,
                         assets=[asset_c] if asset_c else None)
        # The `assets` column on a chunk is a TRANSCRIPT-level pre-filter, not a claim
        # that the chunk is about that asset. A hit from a transcript that actually
        # produced a stance here is corroborated evidence; one that merely passed the
        # pre-filter is not. Rank corroborated first, then by score.
        corroborated = {f.current.source.transcript_ref for f in folded}
        evidence = sorted(
            hits,
            key=lambda h: (getattr(h, "transcript_ref", None) in corroborated,
                           getattr(h, "score", 0.0)),
            reverse=True,
        )

    return RosterView(
        asset=asset_c,
        folded=folded,
        split=summarize_split(folded),
        evidence=evidence,
        changed=[f for f in folded if f.flipped],
        multi_author={
            name: members
            for name, members in registry.members.items()
            if any(f.person_canonical == name for f in folded)
        },
    )
