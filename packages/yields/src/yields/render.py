"""Turn a ``YieldsResult`` into the terminal card.

Pure — a ``YieldsResult`` in, one string out, the same split ``treasury.render`` and
``compare.render`` draw between gathering data and deciding how to print it.

**AC 4:** this module never imports ``gate``/``score`` from ``core.safety`` — every
``GateResult``/``SafetyScore`` it prints already rode in on the ``YieldOption`` it was attached
to by ``yields.scan``, so there is nothing here to recompute.

``safety_text``/``held_state_note`` are public wording helpers, on the same primitives
``treasury.render``'s were extracted to for #94 — the dashboard wire (#100) calls these instead
of holding a second copy of the same sentences.
"""
from __future__ import annotations

from yields.scan import NOT_FETCHED, AssetYield, YieldsResult


def row_apy_text(apy: float | None) -> str:
    return f"{apy:.2f}%" if apy is not None else "—"


def summary_line(result: YieldsResult) -> str:
    return f"{result.matched}/{result.configured} configured wrapper(s) matched a holding"


def safety_text(option) -> str | None:
    """The bare Safety sentence for one option — no leading separator, so a caller (the
    dashboard wire) can print it standalone. ``None`` only when there is nothing to say, which
    never happens today (every option carries either a gate verdict or ``NOT_FETCHED``) but
    keeps the type honest about what ``option_line`` composes from it."""
    if option.state == NOT_FETCHED:
        return "Safety: not fetched yet"
    if option.gate is not None and not option.gate.passed:
        return f"Safety FAILS: {'; '.join(option.gate.reasons)}"
    if option.gate is not None:
        return "Safety OK"
    return None


def held_state_note(asset: AssetYield) -> str | None:
    """The bare sentence for an asset whose held state cannot be read — no leading separator,
    for the same reason ``safety_text`` has none. ``None`` when the state is readable."""
    return None if asset.held_state_readable else "held-state unknown — no readable figi"


def option_line(option) -> str:
    apy = row_apy_text(option.facts.apy if option.facts is not None else None)
    pool = option.pool_id or ""
    line = f"  {option.wrapper:<10} {apy:>7}  {pool:<36}"

    text = safety_text(option)
    if text is not None:
        line += f"  · {text}"

    if option.held is not None:
        line += f"  · HELD ({option.held:g})"

    return line


def asset_block(asset: AssetYield) -> str:
    note = held_state_note(asset)
    suffix = f"  ({note})" if note is not None else ""
    head = f"{asset.asset} · held by {', '.join(asset.mandates)}{suffix}"
    return "\n".join([head, *(option_line(option) for option in asset.options)])


def readings_line(freshest, oldest) -> str:
    """A stale store reads as current unless this prints — copied from
    ``treasury.render.readings_line``, not imported (a view may not import a view)."""
    if freshest is None:
        return ""
    if oldest == freshest:
        return f"  safety readings as of {freshest:%Y-%m-%d}"
    return f"  safety readings {oldest:%Y-%m-%d} to {freshest:%Y-%m-%d}"


def render(result: YieldsResult) -> str:
    lines = [summary_line(result)]

    for asset in result.assets:
        lines += ["", asset_block(asset)]

    line = readings_line(result.readings_as_of.freshest, result.readings_as_of.oldest)
    if line:
        lines += ["", line]

    return "\n".join(lines)
