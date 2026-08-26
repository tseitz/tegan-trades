"""``digest`` — print what changed overnight.

``diff``, ``render``, ``book``, ``roster`` and ``fmt`` are pure. This module is the only one
that **reads** pipeline state; ``vault`` and ``mail`` only **write** the finished string. That
split is what lets the interesting logic be tested against hand-written snapshots instead of a
populated ``data/``.

``state`` is the one exception, and it reads and writes the same small file — the digest's own
memory of what it has already said. It is not pipeline state: nothing else in the repo writes
it and nothing else reads it.

## Two rules this module exists to hold

**A dropped section must say it was dropped.** Warnings used to go only to stderr, which the
nightly redirects into a rotated log the design assumes nobody opens — and ``main`` returns 0 on
every degraded path, so the nightly's own ``WARN`` branch never fired either. Every warning was
therefore, as deployed, equivalent to ``pass``. They now travel in ``problems`` and print in the
body and the subject line, which are the surfaces a person actually sees.

**Wrong is worse than missing.** Reading a stale snapshot yields a real, internally consistent
diff of the wrong night, which the vault note then stamps with today's date. So the snapshot's
``as_of`` is checked against today and a mismatch is shouted at the top rather than rendered
silently.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml
from brain.stance_store import load_all_stances
from core.canon import load_registry
from core.env import load_env
from execution import store
from ingestion import spend
from oracle import exclusions, queue_snapshot
from oracle.decisions import load_decisions

from digest import book as book_mod
from digest import diff, mail, narrate, render, roster, state, vault

REPO_ROOT = Path(__file__).resolve().parents[4]
CONFIG_DIR = REPO_ROOT / "cfg"
DECISIONS = REPO_ROOT / "data" / "setups" / "decisions.jsonl"
HISTORY = REPO_ROOT / "data" / "logs" / "nightly" / "history.jsonl"
STATE = REPO_ROOT / "data" / "digest" / "state.json"

# Most book events one digest will print. A normal night produces a handful, so this only ever
# binds on the first run, where there is no previous run to bound the window and ``since``
# returns the whole order log. The cap says so out loud when it fires; see ``book.lines``.
BOOK_EVENT_LIMIT = 12

# Config that arrives as text and must not take the process down at import.
_CONFIG_ERRORS = (OSError, ValueError, yaml.YAMLError)


def _xai_cap() -> float:
    """The monthly real-money ceiling the digest warns against.

    **Read at call time, after ``load_env``, and NOT claimed to track ``scripts/nightly.sh``.**
    That claim was in here and was false: the nightly sets ``XAI_MONTHLY_CAP`` as a plain shell
    variable and never exports it, so this child process has never once seen it. Raising the cap
    there still does not raise it here — set it in ``.env`` or the environment for that.
    """
    raw = os.environ.get("XAI_MONTHLY_CAP", "20.00")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 20.00


class _Warnings:
    """Collects what broke, so it can reach the reader instead of only the log."""

    def __init__(self) -> None:
        self.items: list[str] = []

    def __call__(self, message: str) -> None:
        self.items.append(message)
        print(message, file=sys.stderr)


def _last_json_line(path: Path, warn) -> dict | None:
    """The last row of a JSONL file, or ``None``.

    Reads the whole file rather than seeking: these are hundreds of lines, and a seek-to-end
    that lands mid-row is a bug that only shows up once the file is big.
    """
    if not path.exists():
        return None
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            # Warned, not silently skipped. A quietly dropped history row makes the digest
            # report the PREVIOUS night's health as tonight's.
            warn(f"warning: unreadable history row at {path.name}:{number}: {exc}")
    return rows[-1] if rows else None


def _run_row(today: str, warn) -> dict | None:
    """Tonight's nightly history row, or ``None`` if the newest one is not tonight's.

    ``nightly.sh`` writes this row before invoking the digest, so ordering *should* guarantee
    it is tonight's — but nothing enforced that, and the writer is invoked with stderr sent to
    ``/dev/null`` and its exit forced to 0. On the one night the run-health machinery breaks,
    an unchecked read prints ``RUN clean`` about yesterday.
    """
    row = _last_json_line(HISTORY, warn)
    if row is None:
        return None
    if not str(row.get("run", "")).startswith(today):
        warn(f"warning: newest nightly history row is {row.get('run')!r}, not today — run "
             f"health omitted rather than reported from an older run")
        return None
    return row


def _orders(path, warn) -> list[dict]:
    """The order log, or an empty list.

    ``execution.store.load`` parses each line with no guard, so one truncated row — the exact
    shape an interrupted append leaves, and this file is appended during the nightly — used to
    raise straight through ``build`` and cost the entire digest.
    """
    try:
        return store.load(path)
    except (OSError, ValueError) as exc:
        warn(f"warning: could not read the order log, the BOOK section is missing: {exc}")
        return []


def _excluded(warn) -> dict:
    """Standing "I don't trade this" entries, or empty.

    ``yaml.YAMLError`` is caught explicitly because it subclasses ``Exception`` directly, **not**
    ``ValueError`` — so an ``(OSError, ValueError)`` handler misses the one failure this reader
    actually has, and a stray tab in the file took down the whole digest.
    """
    try:
        return exclusions.load(CONFIG_DIR / exclusions.DEFAULT_EXCLUSIONS)
    except _CONFIG_ERRORS as exc:
        warn(f"warning: could not read exclusions — every departure below is unattributed, and "
             f"some may simply be assets you excluded: {exc}")
        return {}


def _decided(warn) -> dict:
    try:
        return load_decisions(DECISIONS)
    except _CONFIG_ERRORS as exc:
        warn(f"warning: could not read decisions, departures will be unattributed: {exc}")
        return {}


def _open_assets(orders, orders_path, warn) -> set[str]:
    """Assets the book is still holding, by candidate key against the order log.

    Takes the path rather than reaching for ``store.DEFAULT_PATH``. It did reach, which made
    ``--orders`` half-apply: the BOOK section honoured the override while roster scope silently
    read the real production log. A flag that overrides some of its callers is worse than one
    that does not exist.
    """
    names = book_mod.asset_names(orders)
    try:
        keys = store.unsettled_keys(orders_path) | store.awaiting_exit_keys(orders_path)
    except (OSError, ValueError) as exc:
        warn(f"warning: could not read open positions, roster scope is candidates only: {exc}")
        return set()
    return {names[key] for key in keys if key in names}


def _roster_section(current: dict, orders, orders_path, *, previous, with_llm: bool, seen: dict,
                    warn) -> tuple[str | None, str | None, tuple[str, ...]]:
    """``(narration, withheld, reported)`` — at most one of the first two is set.

    Every outcome gets its own words. This returned a bare ``None`` for five different things —
    nothing in scope, corpus unreadable, roster genuinely still, narration broken, LLM disabled
    — and ``render`` printed nothing for all of them. "The roster is quiet" and "the narrator is
    broken" were byte-identical in the inbox, so a permanent break would read as a quiet roster
    every night, forever.

    Scope is what you could act on: tonight's qualified candidates plus anything still held. A
    roster-wide sweep is a different feature and would be longer than the rest of the digest.

    ``seen`` is what previous nights already said; see ``roster.unreported``. ``reported`` comes
    back **non-empty only on the path where the events actually reached prose**. Every other
    outcome shows a count or nothing, and marking events as told on those nights would drop
    them silently — which is the one failure this section cannot surface.
    """
    assets = {row.get("asset") for row in current.get("rows", []) if row.get("asset")}
    assets |= _open_assets(orders, orders_path, warn)
    if not assets:
        return None, None, ()

    try:
        delta = roster.moved(
            load_all_stances(), load_registry(CONFIG_DIR), assets=sorted(assets),
            on=datetime.now(UTC).date(),
            # Bounded by the previous nightly run so "arrived tonight" means what it says.
            extracted_since=previous.get("run") if previous else None)
    except _CONFIG_ERRORS as exc:
        warn(f"warning: could not read the stance corpus: {exc}")
        return "  unavailable — the stance corpus could not be read", None, ()

    # An abstention is the deliverable on a backfill night. Returned on its own channel so it
    # cannot be printed under a heading reading "ROSTER MOVED", which asserts the opposite.
    if delta.withheld is not None:
        return None, f"  {delta.withheld}", ()

    # The window is seven days wide, so this is where a week of repeats gets cancelled.
    in_window = len(delta.moved)
    delta = roster.unreported(delta, seen)
    if not delta.moved:
        # "Nobody spoke" and "everybody who spoke was in an earlier digest" are different facts
        # about the night, and this package keeps re-learning that collapsing two outcomes into
        # one message is how a section stops being trusted. The second one also tells you the
        # memory is working, which is otherwise invisible from the outside.
        if in_window:
            return (f"  nothing new — the {in_window} asset(s) moving this week were in an "
                    f"earlier digest", None, ())
        return "  no movement on the assets in play", None, ()
    if not with_llm:
        return (f"  {len(delta.moved)} asset(s) moved — re-run without --no-llm to summarise",
                None, ())

    try:
        return narrate.narrate(roster.payload(delta)), None, roster.event_keys(delta)
    except narrate.NarrationFailed as exc:
        warn(f"warning: roster narration failed: {exc}")
        # The count survives even though the prose did not, so you still know to go and look.
        return (f"  {len(delta.moved)} asset(s) moved; the summary could not be generated",
                None, ())


def build(*, snapshots_path=None, orders_path=None, with_llm: bool = True,
          state_path=None) -> tuple[str, str, dict | None]:
    """The digest, as ``(subject, body, memory)``. Never raises on a missing or malformed input.

    ``memory`` is what to write back once the digest has been delivered, or ``None`` when
    ``state_path`` was not given. It is **returned rather than written here** so the caller can
    persist it last. Writing it mid-build would mark tonight's roster movement as told before
    the email had been attempted, and a night the mail failed would lose that movement for
    good — the section would simply never mention it again.
    """
    warn = _Warnings()
    today = datetime.now(UTC).date().isoformat()

    snapshots = queue_snapshot.load(snapshots_path or queue_snapshot.DEFAULT_PATH, warn=warn)
    current = diff.current(snapshots)
    if current is None:
        return ("no queue snapshot yet",
                "No queue snapshot on disk. Run `uv run setups --list` at least once — the "
                "digest diffs against what that records.\n", None)

    previous = diff.previous_nightly(snapshots)
    # Bootstrap is a legitimate state, so a schema regression that hides every prior row would
    # otherwise print "first run" every night forever, indistinguishable from a fresh install.
    #
    # **But several snapshots all stamped TODAY is not that.** It is a first day with a few
    # manual `setups` runs on it, which is the normal way this starts — and warning there put a
    # `!!` on the subject line every morning until the second night ever happened.
    # The tell is a *different* day present but none earlier: that means out-of-order or future
    # stamps, which is the corruption worth naming.
    if previous is None and len({s.get("as_of") for s in snapshots}) > 1:
        warn(f"warning: {len(snapshots)} snapshots span more than one day but none is earlier "
             f"than the newest — check `as_of` in data/setups/queue.jsonl for an out-of-order "
             f"or future-dated row")

    delta = diff.compare(previous, current, decided=_decided(warn), excluded=_excluded(warn))

    orders = _orders(orders_path or store.DEFAULT_PATH, warn)
    window = previous.get("run") if previous else None
    events = book_mod.lines(
        book_mod.since(orders, after=window),
        # Built from the whole log, not the window: the placement naming an asset is usually
        # much older than the reconciliation that settles it. See ``book.asset_names``.
        names=book_mod.asset_names(orders),
        limit=BOOK_EVENT_LIMIT,
        window_unknown=window is None and previous is not None)

    memory = state.load(state_path, warn=warn) if state_path else {}
    narration, withheld, reported = _roster_section(
        current, orders, orders_path or store.DEFAULT_PATH, previous=previous,
        with_llm=with_llm, seen=state.roster_seen(memory), warn=warn)

    # The one comparison that decides whether anything else can be believed.
    stale = current.get("as_of") if current.get("as_of") != today else None

    xai_month = spend.total()
    body = render.markdown(delta, run=_run_row(today, warn), book=events, roster=narration,
                           roster_withheld=withheld, xai_month=xai_month,
                           xai_cap=_xai_cap(),
                           xai_changed=state.xai_changed(memory, xai_month),
                           stale_as_of=stale, problems=warn.items)
    subject = render.subject(delta, book=events, stale_as_of=stale, problems=len(warn.items),
                             repeat=state.is_repeat(memory, delta.previous_run))
    return (subject, body,
            _next_memory(memory, reported, xai_month, delta.previous_run) if state_path else None)


def _next_memory(memory: dict, reported, xai_month: float, window_start: str | None) -> dict:
    """Tonight's memory. Pruned every run, not only on runs that reported something, so a quiet
    week still clears keys that have aged out."""
    return {
        **memory,
        state.ROSTER: roster.remember(state.roster_seen(memory), reported,
                                      on=datetime.now(UTC).date()),
        state.XAI: xai_month,
        state.WINDOW: window_start,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="digest",
        description="What changed overnight: arrivals at the trigger, queue movement, the "
                    "book, and run health.")
    parser.add_argument("--snapshots", default=None,
                        help=f"queue snapshot log (default: {queue_snapshot.DEFAULT_PATH})")
    parser.add_argument("--orders", default=None,
                        help=f"order log (default: {store.DEFAULT_PATH})")
    parser.add_argument("--subject-only", action="store_true",
                        help="print just the subject line — what the email would say")
    parser.add_argument("--vault", action="store_true",
                        help=f"file the note under {vault.DEFAULT_ANCHOR / vault.FOLDER}")
    parser.add_argument("--vault-anchor", default=None,
                        help=f"override the vault location (default: {vault.DEFAULT_ANCHOR})")
    parser.add_argument("--email", action="store_true",
                        help="also mail it — needs DIGEST_SMTP_USER, DIGEST_SMTP_APP_PASSWORD "
                             "and DIGEST_TO in .env")
    parser.add_argument("--no-llm", action="store_true",
                        help="skip the roster narration — the only part that spends anything")
    args = parser.parse_args(argv)

    # `.env` before anything reads the environment: the cap and the mail settings both live
    # there, and reading them first made both silently inert.
    load_env()

    # ``--subject-only`` forces it off. The subject carries nothing from the roster section, so
    # narrating for it spends a `claude -p` call and can block for minutes on a flag whose whole
    # promise is one quick line.
    #
    # It also gets **no memory**, for the same reason: a run that never renders the roster
    # section must not record its events as told. `--no-llm` is safe to give a path to, because
    # the section it prints is a count and ``_roster_section`` reports nothing on that path.
    subject, body, memory = build(
        snapshots_path=args.snapshots, orders_path=args.orders,
        with_llm=not args.no_llm and not args.subject_only,
        state_path=None if args.subject_only else STATE)
    if args.subject_only:
        print(subject)
        return 0

    # stdout always, whatever else happens. It is the one surface that cannot fail, so it is
    # what makes a broken vault or a broken mailer survivable rather than silent.
    print(subject)
    print("─" * len(subject))
    print()
    print(body, end="")

    if args.vault:
        # Written before the email is attempted: the note is the primary copy and must not
        # depend on the mirror succeeding. See ``digest.vault``.
        filed = vault.write(args.vault_anchor or vault.DEFAULT_ANCHOR, body,
                            on=datetime.now(UTC).date(), warn=lambda m: print(m, file=sys.stderr))
        if filed is not None:
            print(f"\nfiled to {filed}")

    if args.email:
        try:
            config = mail.configure(os.environ)
        except mail.NotConfigured as exc:
            # A warning, not a failure: the digest was still printed and still filed, and the
            # nightly should not go red over a missing optional setting.
            print(f"warning: {exc}", file=sys.stderr)
        else:
            if mail.send(config, subject, body, warn=lambda m: print(m, file=sys.stderr)):
                print(f"emailed to {', '.join(config.to)}")

    # Last, once the digest has actually reached its surfaces. Recording earlier would mark
    # tonight's roster movement as told even on a night the delivery failed, and this section
    # never repeats itself — so that movement would be lost rather than delayed.
    if memory is not None:
        state.save(STATE, memory, warn=lambda m: print(m, file=sys.stderr))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
