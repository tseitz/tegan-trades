import textwrap
from ingestion.roster import ChannelTarget, active_targets, load_watchlist


WATCHLIST = textwrap.dedent("""
    people:
      - name: "Alice"
        status: active
        channels:
          - { platform: youtube, id: "@alice", access: ok }
          - { platform: x, id: "alice", access: grok }
      - name: "Bob"
        status: active
        backfill: { max_videos: 10, max_age_days: 365 }
        channels:
          - { platform: youtube, id: "UCbbbbbbbbbbbbbbbbbbbbbb", access: ok }
      - name: "Cara"
        status: candidate
        channels:
          - { platform: youtube, id: "@cara", access: ok }
      - name: "Dan"
        status: active
        channels:
          - { platform: youtube, id: "@danpaid", access: paid }
""")


def _write(tmp_path, text):
    p = tmp_path / "watchlist.yaml"
    p.write_text(text)
    return p


def test_active_targets_selects_active_youtube_ok_only(tmp_path):
    wl = load_watchlist(_write(tmp_path, WATCHLIST))
    targets = active_targets(wl)
    names = [(t.person, t.channel) for t in targets]
    assert names == [("Alice", "@alice"), ("Bob", "UCbbbbbbbbbbbbbbbbbbbbbb")]
    # Cara excluded (candidate); Dan excluded (access=paid); Alice's x channel excluded


def test_active_targets_applies_defaults_and_overrides(tmp_path):
    wl = load_watchlist(_write(tmp_path, WATCHLIST))
    by_person = {t.person: t for t in active_targets(wl)}
    assert by_person["Alice"] == ChannelTarget("Alice", "@alice", 50, 730)
    assert by_person["Bob"] == ChannelTarget("Bob", "UCbbbbbbbbbbbbbbbbbbbbbb", 10, 365)
