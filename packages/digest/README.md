# digest

**The digest is a diff, not a report.**

The repo already has three good reports. `setups --list` is the queue, with ASCII ladders a
terminal renders and email does not. `book` is what the account holds. `scripts/nightly_report.py`
is what the cycle has been doing across runs. None of them answers *"what changed while I was
asleep"*, and that is the only question this package exists for.

So a line earns its place here only if it demands a decision today, or changed since the last
nightly run. Restating unchanged state is what trains a person to stop opening the email — the
digest's real failure mode is not being wrong, it is being ignorable.

## What it reads

Everything was already computed by the time this runs. `digest` does almost no work of its own:

- `data/setups/queue.jsonl` — the qualified population per run, written by `oracle.queue_snapshot`
- `data/setups/decisions.jsonl` — what a human ruled on, for classifying departures
- `cfg/exclusions.yaml` — standing "I don't trade this"
- the order log and reconcile results — fills, stops, targets, aged entries
- `data/stances/` — roster lean, for the one narrated section
- `data/logs/nightly/history.jsonl` — run health

## Layering

Sits above the pipeline and reads down. Nothing imports `digest`; an edge into it would put a
reporting surface underneath the thing it reports on.

`diff.py` and `render.py` are **pure** — no I/O, no clock, no network. All reading happens in
`cli.py`. That split is what makes the interesting logic testable against hand-written
snapshots rather than against a populated `data/`.
