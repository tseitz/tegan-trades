"""distill-canon — report person/asset label drift across the thesis firehose, and
(--review) interactively map unmapped asset labels into cfg/assets.yaml. Deterministic,
no LLM. Person aliases are only *suggested* here (not auto-written) because watchlist.yaml
is heavily commented and safe_dump would destroy it — person drift is tiny, so it's manual."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from core.canon import load_registry, resolve_asset, resolve_person

from distill import store as store_mod

_REPO_ROOT = Path(__file__).resolve().parents[4]
CONFIG_DIR = _REPO_ROOT / "cfg"


@dataclass
class ReportStats:
    total: int = 0
    asset_resolved: int = 0
    person_resolved: int = 0
    unmapped_assets: Counter = field(default_factory=Counter)
    unmapped_persons: Counter = field(default_factory=Counter)
    seen_members: set = field(default_factory=set)        # multi-author feeds present


def scan(theses_root, registry) -> ReportStats:
    stats = ReportStats()
    for path in sorted(Path(theses_root).glob("*/*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for t in doc.get("theses", []):
            stats.total += 1
            person = (t.get("source") or {}).get("person", "")
            pc, presolved = resolve_person(person, registry)
            if presolved:
                stats.person_resolved += 1
                if pc in registry.members:
                    stats.seen_members.add(pc)
            else:
                stats.unmapped_persons[person] += 1

            _canon, aresolved, _rank = resolve_asset(t.get("asset", ""), registry)
            if aresolved:
                stats.asset_resolved += 1
            else:
                stats.unmapped_assets[t.get("asset", "")] += 1
    return stats


def _pct(n: int, d: int) -> str:
    return f"{(100 * n / d):.0f}%" if d else "—"


def format_report(stats: ReportStats, *, top: int = 25) -> str:
    lines = [
        f"theses: {stats.total}",
        f"asset coverage:  {stats.asset_resolved}/{stats.total} ({_pct(stats.asset_resolved, stats.total)})",
        f"person coverage: {stats.person_resolved}/{stats.total} ({_pct(stats.person_resolved, stats.total)})",
    ]
    if stats.unmapped_assets:
        lines.append("\nUNMAPPED assets (add to cfg/assets.yaml):")
        lines += [f"  {c:4d}  {label!r}" for label, c in stats.unmapped_assets.most_common(top)]
    if stats.unmapped_persons:
        lines.append("\nUNMAPPED persons (add aliases in watchlist.yaml):")
        lines += [f"  {c:4d}  {label!r}" for label, c in stats.unmapped_persons.most_common(top)]
    if stats.seen_members:
        lines.append("\nMulti-author feeds present (scores blended until 3.3 splits them):")
        lines += [f"  - {feed}" for feed in sorted(stats.seen_members)]
    return "\n".join(lines)


def apply_asset_mappings(mappings: dict[str, str], assets_path) -> None:
    """Merge {raw_label: canonical} into assets.yaml, preserving existing entries."""
    path = Path(assets_path)
    existing = {}
    if path.exists():
        existing = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for label, canonical in mappings.items():
        existing.setdefault(canonical, [])
        if label not in existing[canonical]:
            existing[canonical].append(label)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(existing, sort_keys=True, allow_unicode=True), encoding="utf-8")


def review(stats: ReportStats, *, assets_path, input_fn=input, out=print) -> dict[str, str]:
    mappings: dict[str, str] = {}
    for label, count in stats.unmapped_assets.most_common():
        ans = input_fn(f"[{count}x] {label!r} -> ticker (blank=skip, __basket__ for theme): ").strip()
        if ans:
            mappings[label] = ans
    if mappings:
        apply_asset_mappings(mappings, assets_path)
        out(f"wrote {len(mappings)} asset mappings -> {assets_path}")
    if stats.unmapped_persons:
        out("Unmapped persons — add aliases to watchlist.yaml manually:")
        for label, count in stats.unmapped_persons.most_common():
            out(f"  [{count}x] {label!r}")
    return mappings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="distill-canon",
        description="Report person/asset label drift across the thesis firehose.")
    parser.add_argument("--review", action="store_true",
                        help="interactively map unmapped asset labels into cfg/assets.yaml")
    args = parser.parse_args(argv)
    registry = load_registry(CONFIG_DIR)
    stats = scan(store_mod.DATA_ROOT, registry)
    print(format_report(stats))
    if args.review:  # pragma: no cover - interactive
        review(stats, assets_path=CONFIG_DIR / "assets.yaml")
    return 0
