"""Store rows -> ``core.safety.VenueFacts`` — the assembly step two views need.

Moved out of ``treasury.book`` (#74) rather than copied: a view may not import a view
(ADR-0004), and both ``treasury`` and ``review``'s yield note need the same
``venue_safety_facts``/``venue_first_tvl``/``venue_pool`` rows turned into ``VenueFacts``. Pure
by the same test the module they came from already passed — zero I/O, ``store_read`` injected,
``VenueEntry``/``WrapperEntry`` duck-typed on ``llama_protocol``/``llama_pools`` alone — so
nothing here reaches upward into ``oracle``.
"""
from __future__ import annotations

from datetime import date

from core import safety

SOURCE_DEFILLAMA = "defillama"


def _latest(rows):
    """The most recent of several stored rows for one key — `altsignal_store.read` returns
    rows time-ordered ascending, so the last one is the latest."""
    return rows[-1]


def _latest_by_key(rows) -> dict:
    """`_latest`, applied per key, across rows spanning more than one key."""
    latest: dict = {}
    for row in rows:
        latest[row.key] = row
    return latest


def protocol_facts(*, store_read):
    """Every stored ``venue_safety_facts``/``venue_first_tvl`` row, gathered once — keyed on
    every slug the store has ever seen, not just the configured ones, because `core.safety.gate`
    needs a resolved parent's own facts to validate a fork's lineage."""
    facts_rows = store_read(source=SOURCE_DEFILLAMA, kind="venue_safety_facts")
    age_rows = store_read(source=SOURCE_DEFILLAMA, kind="venue_first_tvl")

    latest_facts = _latest_by_key(facts_rows)
    latest_age = {slug: row.value for slug, row in _latest_by_key(age_rows).items()}

    out: dict[str, safety.VenueFacts] = {}
    for slug, row in latest_facts.items():
        value = row.value
        first_tvl_on = date.fromisoformat(latest_age[slug]) if slug in latest_age else None
        out[slug] = safety.VenueFacts(
            slug=slug,
            audited=value.get("audited"),
            first_tvl_on=first_tvl_on,
            forked_from=tuple(value.get("forked_from") or ()),
            incidents=value.get("incidents"),
        )
    return out, list(facts_rows) + list(age_rows)


def pool_candidates(entries, facts_by_slug: dict, *, store_read):
    """One ``VenueFacts`` per configured pool, joined against its protocol's gate-level facts.

    ``entries`` is duck-typed on ``llama_protocol``/``llama_pools`` alone, so a
    ``VenueEntry`` and a ``WrapperEntry`` both work unchanged — the reason #74 needed no second
    fetch path.
    """
    candidates: list[safety.VenueFacts] = []
    all_rows = []
    for entry in entries:
        base = facts_by_slug.get(entry.llama_protocol)
        for pool_id in entry.llama_pools:
            pool_rows = store_read(source=SOURCE_DEFILLAMA, kind="venue_pool", key=pool_id)
            if not pool_rows:
                continue
            all_rows.extend(pool_rows)
            value = _latest(pool_rows).value
            candidates.append(safety.VenueFacts(
                slug=entry.llama_protocol,
                pool_id=pool_id,
                audited=base.audited if base else None,
                first_tvl_on=base.first_tvl_on if base else None,
                forked_from=base.forked_from if base else (),
                incidents=base.incidents if base else None,
                apy=value.get("apy"),
                apy_reward=value.get("apy_reward"),
                sigma=value.get("sigma"),
                count=value.get("count"),
                outlier=value.get("outlier"),
                stablecoin=value.get("stablecoin"),
            ))
    return tuple(candidates), all_rows
