import textwrap

import pytest

from oracle import exclusions


def _write(tmp_path, text):
    p = tmp_path / "exclusions.yaml"
    p.write_text(textwrap.dedent(text))
    return p


def test_an_absent_file_excludes_nothing(tmp_path):
    """The queue must work with no exclusions configured — this is opt-in."""
    assert exclusions.load(tmp_path / "exclusions.yaml") == {}


def test_assets_load_with_their_reasons(tmp_path):
    path = _write(tmp_path, """
        assets:
          PNUT: "Zero interest in PNUT"
          DOGE: "don't trade memecoins"
    """)
    assert exclusions.load(path) == {
        "PNUT": "Zero interest in PNUT",
        "DOGE": "don't trade memecoins",
    }


def test_a_reason_is_required(tmp_path):
    """The note is the whole audit trail. A bare list would leave "why is BTC missing from my
    queue" answerable only by `git log`, which is how a gate becomes folklore."""
    path = _write(tmp_path, """
        assets:
          PNUT:
    """)
    with pytest.raises(ValueError, match="needs a reason"):
        exclusions.load(path)


def test_symbols_are_matched_case_insensitively_but_stored_canonically(tmp_path):
    path = _write(tmp_path, """
        assets:
          pnut: "lowercase in the file"
    """)
    assert exclusions.load(path) == {"PNUT": "lowercase in the file"}


# ── the typo guard ──────────────────────────────────────────────────────────

def test_a_symbol_no_thesis_mentions_is_reported_rather_than_silently_excluding_nothing():
    """The §6h failure class: a hand-recorded config fact with no verification. `PNUTT` matches
    no asset, so it silently protects nothing, and the queue looks correctly filtered while
    the excluded asset keeps appearing."""
    assert exclusions.unmatched_symbols({"PNUTT": "typo"}, ["PNUT", "BTC"]) == ("PNUTT",)


def test_a_symbol_the_corpus_mentions_raises_no_complaint():
    assert exclusions.unmatched_symbols({"PNUT": "x"}, ["PNUT", "BTC"]) == ()


def test_an_asset_the_canon_registry_cannot_resolve_is_still_matched():
    """The false positive the first version shipped with. `CL` is in neither `registry.assets`
    nor `registry.tickers` — it resolves as *unresolved* — yet it produces real candidates, so
    checking the registry flagged a legitimately-excluded asset as a typo while correctly
    excluding it. A warning that fires on correct config teaches you to skip the line where
    the real typo will eventually show up."""
    assert exclusions.unmatched_symbols({"CL": "no oil"}, ["CL", "BTC"]) == ()


# ── filtering ───────────────────────────────────────────────────────────────

class _Candidate:
    def __init__(self, asset):
        self.asset = asset


def test_excluded_assets_are_partitioned_out_not_dropped():
    """Returned rather than discarded so the caller can say how many and which — a filter
    nobody can see is indistinguishable from a corpus that went quiet."""
    kept, removed = exclusions.partition(
        [_Candidate("BTC"), _Candidate("PNUT"), _Candidate("ETH")],
        {"PNUT": "Zero interest in PNUT"},
    )
    assert [c.asset for c in kept] == ["BTC", "ETH"]
    assert [c.asset for c in removed] == ["PNUT"]


def test_every_zone_and_direction_on_an_excluded_asset_goes():
    """The reason this is a gate and not a rejection: rejecting buries one zone, and the next
    block that forms on the same instrument asks again. OIL and CL came back on both daily and
    weekly zones after being rejected for the asset."""
    kept, removed = exclusions.partition(
        [_Candidate("OIL"), _Candidate("OIL"), _Candidate("CL"), _Candidate("BTC")],
        {"OIL": "no", "CL": "no"},
    )
    assert [c.asset for c in kept] == ["BTC"]
    assert len(removed) == 3
