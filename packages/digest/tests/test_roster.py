"""Where the roster moved overnight.

Two things carry most of the risk. First, the window: everything here is a *delta*, and a
delta computed on the wrong clock invents movement. Second, the abstention — a backfill of old
videos would read as the roster turning, and the only safe response is to withhold the section
and say why.
"""
from __future__ import annotations

from datetime import date

from core.canon import Registry
from core.stance import ExtractedStance, build_stance
from core.thesis import Source
from digest import roster

REGISTRY = Registry(
    people={"benjamin cowen": "Benjamin Cowen", "pierre": "Pierre",
            "tradermayne": "TraderMayne", "krillin": "krillin"},
    assets={"eth": "ETH", "ethereum": "ETH", "btc": "BTC", "hype": "HYPE"},
)


def stance(person, asset, lean, published, *, horizon="swing", extracted="2026-08-20T00:00:00+00:00"):
    src = Source(person=person, platform="youtube", url=f"https://youtu.be/{person}{published}",
                 published_at=published, transcript_ref=f"youtube/{person[:3]}{published}")
    return build_stance(
        ExtractedStance.model_validate({
            "asset": asset, "lean": lean, "horizon": horizon,
            "rationale": f"{person} on {asset} at {published}"}),
        source=src, model="m", extracted_at=extracted)


def _moved(stances, assets=("ETH",), on=date(2026, 8, 21), **kw):
    return roster.moved(stances, REGISTRY, assets=assets, on=on, **kw)


# ── what counts as movement ───────────────────────────────────────────────────

def test_a_new_voice_inside_the_window_is_movement():
    delta = _moved([stance("Benjamin Cowen", "ETH", "bullish", "2026-08-20")])
    assert [m.asset for m in delta.moved] == ["ETH"]
    assert "Benjamin Cowen" in delta.moved[0].fresh_people


def test_a_person_changing_their_lean_is_movement_and_is_named():
    """The single highest-signal event the corpus produces, and rare. ``FoldedStance.flipped``
    already computes it — this only has to notice it happened inside the window."""
    delta = _moved([stance("Pierre", "ETH", "bearish", "2026-06-01"),
                    stance("Pierre", "ETH", "bullish", "2026-08-20")])
    assert delta.moved[0].flips
    flip = delta.moved[0].flips[0]
    assert flip.person == "Pierre" and flip.was == "bearish" and flip.now == "bullish"


def test_a_roster_that_said_nothing_new_has_not_moved():
    """Everything predates the window. The views still exist; they are not news."""
    assert _moved([stance("Benjamin Cowen", "ETH", "bullish", "2026-01-01")]).moved == ()


def test_restating_the_same_lean_is_not_movement():
    """Someone repeating themselves is the most common thing in this corpus. Reporting it
    would make the section fire every night and mean nothing."""
    delta = _moved([stance("Pierre", "ETH", "bullish", "2026-06-01"),
                    stance("Pierre", "ETH", "bullish", "2026-08-20")])
    assert delta.moved == ()


def test_the_split_before_and_after_are_both_carried():
    """A count with nothing to compare against is not a delta. The narration needs both sides
    or it can only describe, not compare."""
    delta = _moved([stance("Pierre", "ETH", "bullish", "2026-06-01"),
                    stance("Benjamin Cowen", "ETH", "bearish", "2026-08-20")])
    move = delta.moved[0]
    assert move.before.counts["bullish"] == 1 and move.before.counts["bearish"] == 0
    assert move.after.counts["bearish"] == 1


# ── scope ─────────────────────────────────────────────────────────────────────

def test_only_the_assets_asked_for_are_considered():
    """Scope is candidates and open positions. A roster-wide sweep is a different feature and
    would make the section longer than the rest of the digest."""
    delta = _moved([stance("Pierre", "BTC", "bullish", "2026-08-20")], assets=("ETH",))
    assert delta.moved == ()


def test_asset_aliases_resolve_before_matching():
    """The corpus says "ethereum", the queue says "ETH". Matching raw labels would silently
    drop most of the movement on any asset with more than one spelling."""
    delta = _moved([stance("Pierre", "ethereum", "bullish", "2026-08-20")], assets=("ETH",))
    assert [m.asset for m in delta.moved] == ["ETH"]


def test_a_new_horizon_from_an_existing_voice_is_reported_but_is_not_a_flip():
    """Cowen has held BTC.D bullish on swing and bearish on macro in one video, so folding
    horizons together would invent a flip — ``_fold_key`` keeps horizon in the key for exactly
    that reason. But the statement is still real: someone who held only a swing view turning
    bearish on macro is worth reading, and it is neither a flip nor a new voice. It gets its
    own category rather than vanishing between the two."""
    delta = _moved([stance("Pierre", "ETH", "bullish", "2026-06-01", horizon="swing"),
                    stance("Pierre", "ETH", "bearish", "2026-08-20", horizon="macro")])
    move = delta.moved[0]
    assert not move.flips
    assert not move.fresh_people
    assert [(v.person, v.lean, v.horizon) for v in move.new_views] == [
        ("Pierre", "bearish", "macro")]


# ── the abstention ────────────────────────────────────────────────────────────

def test_a_backfill_withholds_the_section_rather_than_reporting_a_turn():
    """``brain-extract`` is capped at 12 a night, so on a normal night new extractions track
    new videos. Run a backfill and a batch of 2025 videos lands at once — which reads as the
    roster turning bearish overnight when nothing moved at all. Withholding is the only safe
    answer; a wrong story about sentiment is worse than none because it reads as a signal."""
    old = [stance("Pierre", "ETH", "bearish", "2025-01-05", extracted="2026-08-21T02:00:00+00:00"),
           stance("krillin", "ETH", "bearish", "2025-06-20", extracted="2026-08-21T02:00:00+00:00")]
    delta = _moved(old, extracted_since="2026-08-20T06:00:00+00:00")
    assert delta.withheld is not None
    assert "publication" in delta.withheld
    assert delta.moved == ()


def test_a_normal_night_of_fresh_videos_does_not_withhold():
    fresh = [stance("Pierre", "ETH", "bullish", "2026-08-20",
                    extracted="2026-08-21T02:00:00+00:00"),
             stance("krillin", "ETH", "bullish", "2026-08-19",
                    extracted="2026-08-21T02:00:00+00:00")]
    delta = _moved(fresh, extracted_since="2026-08-20T06:00:00+00:00")
    assert delta.withheld is None
    assert delta.moved


def test_no_extraction_cutoff_means_no_abstention_check():
    """The first digest has no previous run to bound extraction by. It reports what it can."""
    assert _moved([stance("Pierre", "ETH", "bullish", "2026-08-20")]).withheld is None


def test_a_single_backfilled_video_does_not_trip_the_guard():
    """The guard is about a *batch* spanning months. One old video re-extracted is normal
    catch-up and must not silence a night that also has real movement."""
    stances = [stance("Pierre", "ETH", "bullish", "2026-08-20",
                      extracted="2026-08-21T02:00:00+00:00")]
    assert _moved(stances, extracted_since="2026-08-20T06:00:00+00:00").withheld is None


# ── the payload handed to the narrator ────────────────────────────────────────

def test_the_narration_payload_is_counts_and_names_only():
    """The model writes prose over numbers Python already computed. It never sees a transcript,
    so a wrong number here is a rendering bug with a test behind it, not a hallucination."""
    delta = _moved([stance("Pierre", "ETH", "bearish", "2026-06-01"),
                    stance("Pierre", "ETH", "bullish", "2026-08-20")])
    payload = roster.payload(delta)
    assert payload[0]["asset"] == "ETH"
    assert payload[0]["flips"][0]["person"] == "Pierre"
    blob = str(payload)
    assert "rationale" not in blob and "transcript" not in blob
