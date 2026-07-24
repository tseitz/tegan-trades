import json

import yaml
from core.canon import Registry
from distill.canon_cli import (
    apply_asset_mappings,
    format_report,
    review,
    scan,
)

REGISTRY = Registry(
    people={"cowen": "Benjamin Cowen",
            "technical roundup (cryptocred + donalt)": "Technical Roundup (CryptoCred + DonAlt)"},
    members={"Technical Roundup (CryptoCred + DonAlt)": ["CryptoCred", "DonAlt"]},
    assets={"bitcoin": "BTC", "mitchi": "MITCHI"},
    tickers={"BTC": {"name": "Bitcoin", "market_cap_rank": 1}},
)


def _thesis(person, asset):
    return {"asset": asset, "source": {"person": person}}


def _write_doc(root, name, theses):
    d = root / "youtube"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps({"theses": theses}), encoding="utf-8")


def test_scan_counts_coverage_unmapped_and_members(tmp_path):
    _write_doc(tmp_path, "a.json", [
        _thesis("Cowen", "Bitcoin"),                 # both resolve; BTC has rank
        _thesis("Unknown Guest", "WeirdTheme"),      # both unmapped
        _thesis("Cowen", "MITCHI"),                  # curated non-crypto: resolved, no rank
        _thesis("Technical Roundup (CryptoCred + DonAlt)", "BTC"),  # multi-author feed
    ])
    stats = scan(tmp_path, REGISTRY)
    assert stats.total == 4
    assert stats.asset_resolved == 3           # Bitcoin, MITCHI, BTC
    assert stats.person_resolved == 3          # all but Unknown Guest
    assert stats.unmapped_assets["WeirdTheme"] == 1
    assert stats.unmapped_persons["Unknown Guest"] == 1
    assert "Technical Roundup (CryptoCred + DonAlt)" in stats.seen_members


def test_format_report_surfaces_sections(tmp_path):
    _write_doc(tmp_path, "a.json", [_thesis("Nobody", "Glorp"), _thesis("Cowen", "Bitcoin")])
    report = format_report(scan(tmp_path, REGISTRY))
    assert "coverage" in report
    assert "UNMAPPED assets" in report and "Glorp" in report
    assert "UNMAPPED persons" in report and "Nobody" in report


def test_apply_asset_mappings_preserves_existing(tmp_path):
    path = tmp_path / "assets.yaml"
    path.write_text("BTC: [Bitcoin]\n", encoding="utf-8")
    apply_asset_mappings({"btc": "BTC", "Ethereum": "ETH"}, path)
    data = yaml.safe_load(path.read_text())
    assert data["BTC"] == ["Bitcoin", "btc"]   # merged, not clobbered
    assert data["ETH"] == ["Ethereum"]


def test_review_gathers_input_and_writes(tmp_path):
    _write_doc(tmp_path, "a.json", [_thesis("Cowen", "Glorp"), _thesis("Cowen", "Zonk")])
    stats = scan(tmp_path, REGISTRY)
    answers = iter(["GLORP", ""])  # map Glorp, skip Zonk
    assets_path = tmp_path / "assets.yaml"
    out_lines = []
    mappings = review(stats, assets_path=assets_path,
                      input_fn=lambda _prompt: next(answers), out=out_lines.append)
    assert mappings == {"Glorp": "GLORP"}
    assert yaml.safe_load(assets_path.read_text())["GLORP"] == ["Glorp"]
