# Yield Venue Safety Gate

The map's framing was "mostly blue chip, but open to a validated fork with Lindy behind its parent and real TVL" — an intent, not a rule a program can run. Prior research (issue #49) confirmed which signals are free to read (audit tier, fork lineage, on-chain age, TVL/APY history, incentive-vs-interest split, incident records) and which aren't (audit findings/severity, fork-diff correctness, governance risk, team track record). This ADR turns the free signals into a concrete gate.

## Hard gate, not a score

Whether a venue is safe enough to suggest is a gate a venue must clear entirely, never a weighted total. This follows the repo's existing rule for this fork in the road: gate on a rule you wrote or a fact that's simply missing, score anything that sits on a continuum. Audit existence and on-chain age are missing-or-not facts; incentive mix and history smoothness are continuums. A weighted score would let a pristine incentive mix paper over "nobody has ever audited this," which is exactly the outcome the gate exists to prevent.

A venue that clears the gate still gets a **safety score** surfaced alongside it — incentive-vs-interest split, TVL/APY history stability, incident record — matching this rescope's "flag, don't size" principle: present the risk, never hide it behind a single pass/fail.

## The gate

A yield venue must pass both:

1. **At least one audit on record.** Any firm, any findings. Audit findings and severity aren't available from free sources, so grading auditor quality would be fake precision; a reputation dial on *which* firm did the audit is a plausible future refinement to this check, not built now.
2. **Old enough on-chain**, where "old enough" depends on whether the venue is a validated fork:
   - **Validated fork** — its lineage names a specific parent protocol, and that parent protocol *itself* already clears this same gate. This is a mechanical rule, not a hand-kept allowlist: the parent's own already-computed pass/fail supplies the "Lindy behind its parent" check for free, so there's no second list to keep in step with the first. Minimum age: **4 months**.
   - **Everything else** (a standalone protocol, or a fork whose lineage can't be resolved to a passing parent) has to prove itself on its own history. Minimum age: **9 months**.

Both numbers are first-pass constants, not derived — picked from the ranges Tegan gave (3–6 months for a validated fork, 6–12 months otherwise) and expected to move once real candidates are run through the gate, the same way `STOP_PAD_ATR` gets tuned elsewhere in this repo.

## Naming: Safety, not Trust

`CONTEXT.md` already had a "Trust" entry for this exact concept, telling readers to avoid the word "Safety." That's now reversed: this concept is named **Safety**, and "Trust" is freed up rather than reused, because issue #1 already uses "trust" for a completely unrelated concept — the crypto roster's per-voice credibility grade (accuracy, alpha, signal). The two ideas share no computation and shouldn't share a word.
