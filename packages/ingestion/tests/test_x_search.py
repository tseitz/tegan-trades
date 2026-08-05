from __future__ import annotations

import json

import pytest
from ingestion import x_search
from ingestion.x_search import SearchNotRun, XPost

# ── fixtures shaped like the real API, taken from the 2026-07-26 spike ──────────

def _response(*, text, x_search_calls=4, annotations=None):
    return {
        "status": "completed",
        "usage": {
            "server_side_tool_usage_details": {
                "web_search_calls": 0,
                "x_search_calls": x_search_calls,
            },
        },
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [
                {"type": "output_text", "text": text,
                 "annotations": annotations if annotations is not None else []},
            ]},
        ],
    }


def _annotation(url):
    return {"type": "url_citation", "url": url, "start_index": 0, "end_index": 0, "title": "1"}


def _post_json(handle="Tradermayne", post_id="123", posted_at="2026-07-24",
               text="BTC looking heavy", chart=""):
    return {
        "handle": handle,
        "post_id": post_id,
        "url": f"https://x.com/{handle}/status/{post_id}",
        "posted_at": posted_at,
        "text": text,
        "chart": chart,
    }


ALLOWED = ("Tradermayne", "trader1sz", "DonAlt")


# ── the silent-failure guard ────────────────────────────────────────────────────

def test_a_response_where_the_search_never_ran_is_an_error_not_an_empty_day():
    """Measured 2026-07-26: attaching a strict json_schema takes ``x_search_calls`` to 0 while
    still returning HTTP 200 and a well-formed body. That is indistinguishable from a genuine
    quiet day unless we refuse it here — the whole point of the ingestion boundary."""
    resp = _response(text='```json\n[]\n```', x_search_calls=0)
    with pytest.raises(SearchNotRun):
        x_search.harvest(resp, allowed=ALLOWED)


def test_a_genuinely_empty_window_is_a_normal_empty_result():
    """The honest case: the tool ran, found nothing, and said so. Zero posts, no exception."""
    resp = _response(text="No posts found.", x_search_calls=1)
    result = x_search.harvest(resp, allowed=ALLOWED)
    assert result.posts == ()
    assert result.tool_calls == 1


def test_tool_calls_reads_zero_when_usage_details_are_absent():
    assert x_search.tool_calls({"usage": {}}) == 0
    assert x_search.tool_calls({}) == 0


# ── pulling the payload out of prose ────────────────────────────────────────────

def test_a_fenced_json_block_is_extracted_from_surrounding_prose():
    """Grok narrates around its output. We cannot use the API's structured-output mode — it
    disables the search — so the payload is parsed out of the text instead."""
    text = 'Here is what I found:\n```json\n[{"a": 1}]\n```\nHope that helps!'
    assert x_search.extract_payload(text) == [{"a": 1}]


def test_a_bare_json_array_with_no_fence_is_still_extracted():
    assert x_search.extract_payload('  [{"a": 1}]  ') == [{"a": 1}]


def test_text_with_no_json_at_all_yields_nothing_rather_than_raising():
    assert x_search.extract_payload("No posts found.") == []


# ── the two integrity rules ─────────────────────────────────────────────────────

def test_a_post_whose_url_is_not_in_the_annotations_is_dropped():
    """Annotations are the only evidence a post is real. A URL the response never cited is an
    unverifiable claim, and unverifiable claims do not enter the corpus."""
    cited = _post_json(post_id="111")
    invented = _post_json(post_id="222")
    resp = _response(text=json.dumps([cited, invented]),
                     annotations=[_annotation(cited["url"])])
    result = x_search.harvest(resp, allowed=ALLOWED)
    assert [p.post_id for p in result.posts] == ["111"]
    assert result.dropped["uncited"] == 1


def test_a_post_is_matched_to_its_citation_by_status_id_not_by_url_string():
    """The bug that rejected 100% of a real window. Annotations come back in the handle-less
    ``/i/status/<id>`` form while extracted posts carry ``/<handle>/status/<id>`` — the same
    167 posts, spelled two ways. Matching on the id is the only stable join."""
    post = _post_json(handle="DonAlt", post_id="2080555730839769573")
    resp = _response(
        text=json.dumps([post]),
        annotations=[_annotation("https://x.com/i/status/2080555730839769573")],
    )
    result = x_search.harvest(resp, allowed=ALLOWED)
    assert [p.post_id for p in result.posts] == ["2080555730839769573"]
    assert not result.dropped


def test_a_url_with_no_status_id_yields_no_id_rather_than_a_bad_match():
    assert x_search.status_id("https://x.com/DonAlt") == ""
    assert x_search.status_id("") == ""


def test_a_post_from_a_handle_outside_the_allowed_list_is_dropped():
    """``allowed_x_handles`` is applied server-side and we cannot verify it from the response —
    in the spike the model narrated searching unrestricted. So we re-apply it ourselves."""
    stranger = _post_json(handle="elonmusk", post_id="333")
    resp = _response(text=json.dumps([stranger]),
                     annotations=[_annotation(stranger["url"])])
    result = x_search.harvest(resp, allowed=ALLOWED)
    assert result.posts == ()
    assert result.dropped["handle_not_allowed"] == 1


def test_handle_matching_is_case_insensitive():
    post = _post_json(handle="TRADERMAYNE", post_id="444")
    resp = _response(text=json.dumps([post]), annotations=[_annotation(post["url"])])
    result = x_search.harvest(resp, allowed=ALLOWED)
    assert len(result.posts) == 1


def test_an_image_only_post_read_without_image_understanding_is_dropped():
    """A post whose entire content was a chart comes back blank when images are off. Keeping
    it pads the document with nothing and still costs a distill call — 79 of 167 posts in one
    real window were under 20 characters, because this roster's chartists post charts."""
    blank = _post_json(post_id="666", text="", chart="")
    resp = _response(text=json.dumps([blank]), annotations=[_annotation(blank["url"])])
    result = x_search.harvest(resp, allowed=ALLOWED)
    assert result.posts == ()
    assert result.dropped["empty"] == 1


def test_a_blank_post_that_has_a_chart_transcription_is_kept():
    """With images on, the chart *is* the content — that post is the signal, not noise."""
    charted = _post_json(post_id="777", text="", chart="SOLUSD 1H - 73.558 monthly open")
    resp = _response(text=json.dumps([charted]), annotations=[_annotation(charted["url"])])
    result = x_search.harvest(resp, allowed=ALLOWED)
    assert len(result.posts) == 1
    assert result.posts[0].chart.startswith("SOLUSD")


def test_a_post_with_no_date_is_dropped_rather_than_assumed():
    """Mirrors ``core.setups``' ``undated`` refusal — a post that cannot be placed in time
    cannot be graded, aged, or agreed with."""
    undated = _post_json(post_id="555", posted_at="")
    resp = _response(text=json.dumps([undated]), annotations=[_annotation(undated["url"])])
    result = x_search.harvest(resp, allowed=ALLOWED)
    assert result.posts == ()
    assert result.dropped["undated"] == 1


def test_a_post_missing_required_fields_is_dropped_and_counted():
    resp = _response(text=json.dumps([{"handle": "Tradermayne"}]))
    result = x_search.harvest(resp, allowed=ALLOWED)
    assert result.posts == ()
    assert result.dropped["malformed"] == 1


def test_drops_are_counted_rather_than_silently_swallowed():
    """A harvest that quietly discarded half its posts would look identical to a quiet day."""
    good = _post_json(post_id="1")
    resp = _response(
        text=json.dumps([good, _post_json(post_id="2"), _post_json(handle="nobody", post_id="3")]),
        annotations=[_annotation(good["url"]), _annotation("https://x.com/nobody/status/3")],
    )
    result = x_search.harvest(resp, allowed=ALLOWED)
    assert len(result.posts) == 1
    assert dict(result.dropped) == {"uncited": 1, "handle_not_allowed": 1}


# ── grouping into one document per author per day ───────────────────────────────

def test_posts_group_into_one_document_per_handle_per_day():
    """The distill layer builds one Source per document, so a document must have exactly one
    author. A whole-window file would attribute 17 people's views to whoever the sidecar
    names."""
    posts = [
        XPost("Tradermayne", "1", "u1", "2026-07-24", "a", ""),
        XPost("Tradermayne", "2", "u2", "2026-07-24", "b", ""),
        XPost("Tradermayne", "3", "u3", "2026-07-25", "c", ""),
        XPost("DonAlt", "4", "u4", "2026-07-24", "d", ""),
    ]
    groups = x_search.group_by_author_day(posts)
    assert set(groups) == {("Tradermayne", "2026-07-24"), ("Tradermayne", "2026-07-25"),
                           ("DonAlt", "2026-07-24")}
    assert [p.post_id for p in groups[("Tradermayne", "2026-07-24")]] == ["1", "2"]


def test_a_documents_source_id_is_stable_and_filesystem_safe():
    assert x_search.source_id_for("Tradermayne", "2026-07-24") == "Tradermayne-2026-07-24"


# ── the document itself ─────────────────────────────────────────────────────────

def test_the_document_carries_verbatim_text_and_the_post_url():
    """This file is the ore. X posts get deleted and accounts go private, so if the verbatim
    text is not captured now it is unrecoverable — and re-distillation under a new schema is
    the entire living-schema bet."""
    posts = [XPost("Tradermayne", "1", "https://x.com/Tradermayne/status/1",
                   "2026-07-24", "BTC looking heavy here", "")]
    doc = x_search.render_document("Tradermayne", "2026-07-24", posts)
    assert "BTC looking heavy here" in doc
    assert "https://x.com/Tradermayne/status/1" in doc


def test_a_chart_transcription_is_labelled_so_it_is_never_mistaken_for_the_post_text():
    posts = [XPost("trader1sz", "1", "u", "2026-07-24", "$SOL",
                   "SOLUSD 1H - 83.609 last week high, 73.558 monthly open")]
    doc = x_search.render_document("trader1sz", "2026-07-24", posts)
    assert "83.609" in doc
    assert "CHART" in doc
    body = doc.split("CHART")[0]
    assert "83.609" not in body


# ── request construction ────────────────────────────────────────────────────────

def test_the_request_restates_both_the_window_and_the_handles_in_the_prompt():
    """The model cannot see its own tool config. Given only the dates it answered "No accounts
    were provided in the query" and returned an empty array **without searching** — a
    well-formed, entirely fictional quiet day. Both halves have to be in the prompt text."""
    body = x_search.build_request(["a", "b"], "2026-07-24", "2026-07-26", images=False)
    assert "2026-07-24" in body["input"] and "2026-07-26" in body["input"]
    assert "@a" in body["input"] and "@b" in body["input"]


def test_the_request_never_sets_a_response_schema():
    """A strict json_schema silently disables the search — measured, 4 tool calls to 0."""
    body = x_search.build_request(["a"], "2026-07-24", "2026-07-26", images=False)
    assert "text" not in body

def test_image_understanding_is_off_unless_asked_for():
    """~10x the cost of a text call ($0.40 vs $0.04), so it is opt-in per run."""
    off = x_search.build_request(["a"], "2026-07-24", "2026-07-26", images=False)
    on = x_search.build_request(["a"], "2026-07-24", "2026-07-26", images=True)
    assert off["tools"][0]["enable_image_understanding"] is False
    assert on["tools"][0]["enable_image_understanding"] is True


def test_more_handles_than_the_api_allows_is_refused_before_spending_a_call():
    with pytest.raises(ValueError, match="20"):
        x_search.build_request([f"h{i}" for i in range(21)], "2026-07-24", "2026-07-26",
                               images=False)
