# TypeScript and React for the Dashboard, Not Server-Rendered HTML

The dashboard is a new Surface over the existing Views, starting with `review`. Until now this repo had no JavaScript at all: Python 3.12 everywhere, stdlib `argparse`, hand-rolled column padding, and the one HTML-producing file (`digest/htmlmail.py`) deliberately wraps already-rendered text in `<pre>` rather than being a second renderer. Adding a browser front end means either keeping that streak or breaking it.

## Decision

Settled 2026-09-19. **A JSON API in Python, and a TypeScript + React single-page app in the browser.** The repo takes on a Node toolchain.

The reason is what the dashboard is *for*. The pain it solves is readability, and the readability win over the terminal is not a prettier table — `rich` would win that, and this repo doesn't even use `rich`. It is sorting, filtering, collapsing, and eventually a price chart beside a Verdict. Those are client-side interactions. A 78-row Mandate that round-trips the server on every sort is worse than the terminal it replaces.

The Python half stays a Surface under [ADR-0004](0004-sensor-view-surface-layering.md): it imports Views and never a Sensor.

## Considered and rejected

- **FastAPI + Jinja + HTMX, keeping the repo single-language.** Genuinely tempting, and the right answer if the goal had been "make the existing output prettier" — no build step, one venv, one toolchain, trivially deployable to the droplet. Rejected because server round-trips per sort are the specific thing being escaped, and because the author is fluent in TypeScript and not in HTMX, which on a personal project decides how much actually gets built.
- **Wrapping the existing rendered text in HTML, as `digest/htmlmail.py` does.** Nearly free and correctly rejected: that is the email, and it is already the thing that proved a delivery channel alone doesn't solve this.

## Consequences

- Two toolchains and two dependency managers in a repo that had one. The uv workspace rule ("one `.venv`, one `uv.lock`, always from the root") now has a Node sibling that does not follow it.
- A serialization boundary appears where there was none. [ADR-0005](0005-per-view-result-objects.md) decided per-View result objects need **no schema versioning**, on the explicit grounds that "nothing outside it reads these files". A browser is now outside it. That tension is real and is settled separately.
