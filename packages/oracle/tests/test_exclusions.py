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
    def __init__(self, asset, aliases=()):
        self.asset = asset
        self.aliases = tuple(aliases)


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


def test_a_standing_no_still_bites_when_its_label_was_folded_into_another():
    """`collapse` merges two spellings of one instrument and keeps the label with the wider
    venue coverage, so an exclusion written against the folded spelling would go inert without
    anything saying so — a gate that silently stops gating. "Not my market" is about the
    market, and RUT and IWM are one market."""
    kept, removed = exclusions.partition(
        [_Candidate("IWM", aliases=("RUT",)), _Candidate("BTC")],
        {"RUT": "I don't trade the Russell"},
    )
    assert [c.asset for c in kept] == ["BTC"]
    assert [c.asset for c in removed] == ["IWM"]


# ── appending ───────────────────────────────────────────────────────────────

def test_an_asset_is_appended_under_the_existing_assets_key(tmp_path):
    """Appended textually rather than re-dumped. `yaml.safe_dump` of the parsed file would
    round-trip the data correctly and destroy the ~35-line header, which is where the whole
    rejection-vs-exclusion distinction is written down — the part a future session needs most."""
    path = _write(tmp_path, """
        # a header that must survive
        assets:
          PNUT: "Zero interest in PNUT"
    """)
    assert exclusions.append(path, "PENDLE", "never trading this") is True
    assert exclusions.load(path) == {
        "PNUT": "Zero interest in PNUT",
        "PENDLE": "never trading this",
    }
    assert "# a header that must survive" in path.read_text()


def test_appending_an_asset_already_excluded_changes_nothing(tmp_path):
    """Returns False rather than raising or duplicating the key. Archiving the same asset twice
    is an ordinary thing to do across sessions, and a duplicate mapping key is the one edit that
    would make the file silently lose an entry when PyYAML keeps only the last."""
    path = _write(tmp_path, """
        assets:
          PNUT: "Zero interest in PNUT"
    """)
    before = path.read_text()
    assert exclusions.append(path, "PNUT", "a different reason") is False
    assert path.read_text() == before


def test_a_reason_with_yaml_metacharacters_survives_the_round_trip(tmp_path):
    """The reason is free text typed at a prompt, so it will eventually contain a colon, a
    quote or a leading `#`. Emitting it through the YAML dumper rather than f-stringing it is
    what keeps `load` able to read back exactly what was typed."""
    path = _write(tmp_path, """
        assets:
          PNUT: "Zero interest in PNUT"
    """)
    nasty = 'no: "never" — #1 on my do-not-trade list'
    assert exclusions.append(path, "MELANIA", nasty) is True
    assert exclusions.load(path)["MELANIA"] == nasty


def test_a_blank_reason_is_refused(tmp_path):
    """`load` raises on a reason-less entry, so writing one would produce a file the next run
    cannot read at all — turning a mistyped prompt into a broken queue rather than a bad row."""
    path = _write(tmp_path, "assets:\n  PNUT: \"no\"\n")
    with pytest.raises(ValueError, match="needs a reason"):
        exclusions.append(path, "PENDLE", "   ")


def test_an_absent_file_is_created_with_the_assets_key(tmp_path):
    """The gate is opt-in and the file may genuinely not exist yet. Creating it beats failing
    the archive, because the alternative is losing the judgement that prompted it."""
    path = tmp_path / "exclusions.yaml"
    assert exclusions.append(path, "PENDLE", "never trading this") is True
    assert exclusions.load(path) == {"PENDLE": "never trading this"}


def test_a_file_with_no_assets_key_gains_one(tmp_path):
    """A header-only file — what you get after removing the last entry by hand."""
    path = _write(tmp_path, "# just a header, no mapping yet\n")
    assert exclusions.append(path, "PENDLE", "never trading this") is True
    assert exclusions.load(path) == {"PENDLE": "never trading this"}


def test_the_symbol_is_upcased_to_match_how_candidates_are_compared(tmp_path):
    """`partition` compares `candidate.asset` against the keys `load` upcases. A lowercase
    entry would read as excluded in the file and suppress nothing — the same fails-open
    failure `unmatched_symbols` exists to catch."""
    path = _write(tmp_path, "assets:\n  PNUT: \"no\"\n")
    exclusions.append(path, "pendle", "never trading this")
    assert "PENDLE" in exclusions.load(path)


def test_a_file_that_would_not_parse_after_the_edit_is_left_untouched(tmp_path, monkeypatch):
    """The append is verified in memory before anything is written. This file is committed
    config that gates the whole queue, so a half-written edit costs more than a failed archive
    — and the caller can still record the decision in the sidecar."""
    path = _write(tmp_path, "assets:\n  PNUT: \"no\"\n")
    before = path.read_text()
    monkeypatch.setattr(exclusions.yaml, "safe_dump", lambda *a, **k: "]: [not yaml\n")
    with pytest.raises(ValueError):
        exclusions.append(path, "PENDLE", "never trading this")
    assert path.read_text() == before


def test_an_appended_reason_is_quoted_like_the_hand_written_entries(tmp_path):
    """Cosmetic, but this file is curated and reviewed by hand — an entry that renders unlike
    its neighbours reads as machine spoor in a file whose value is that a human vouched for
    every line. Style only; the dumper still owns escaping."""
    path = _write(tmp_path, 'assets:\n  PNUT: "Zero interest in PNUT"\n')
    exclusions.append(path, "PENDLE", "never trading this")
    assert '  PENDLE: "never trading this"' in path.read_text()
