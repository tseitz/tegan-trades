"""``stake_solana.read``/``staked_sol`` against fixture JSON, no network.

Fixtures are trimmed from `scripts/probe_solana_stake.py`'s live run against
`DEs2iLbuF34RpeLaSXyt2s5EGq5Htdaw4und4CXUQNEM` on 2026-09-16 — a normal delegated account and
the one that came back with `stake: null` (probe finding 3).
"""

from __future__ import annotations

import json

import pytest
from oracle import stake_solana, wallet


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


def _stub(monkeypatch, payload: dict):
    monkeypatch.setattr(
        stake_solana.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(payload)
    )


DELEGATED = {
    "pubkey": "27kF6BQ2CEFEgvawMGRSqwoYRHu3axspjPbQdYcn1cYV",
    "account": {
        "lamports": 201957056,
        "data": {
            "parsed": {
                "info": {
                    "meta": {
                        "rentExemptReserve": "2282880",
                        "authorized": {"staker": "stW...", "withdrawer": "DEs2..."},
                    },
                    "stake": {
                        "delegation": {
                            "stake": "199674176",
                            "activationEpoch": "946",
                            "deactivationEpoch": "18446744073709551615",
                            "voter": "Ec55...",
                        }
                    },
                }
            }
        },
    },
}

NULL_STAKE = {
    "pubkey": "HhB9exSdL5asL9WF5XQAMKooJcrSww2KjTzgrPnaEK2s",
    "account": {
        "lamports": 2282880,
        "data": {
            "parsed": {
                "info": {
                    "meta": {
                        "rentExemptReserve": "2282880",
                        "authorized": {"staker": "stW...", "withdrawer": "DEs2..."},
                    },
                    "stake": None,
                }
            }
        },
    },
}

ADDRESS = "DEs2iLbuF34RpeLaSXyt2s5EGq5Htdaw4und4CXUQNEM"


def test_lamports_are_summed_across_every_account_including_a_null_stake_one(
    monkeypatch,
):
    """Finding 1-2 from the probe: `account.lamports`, not `delegation.stake`, is the staked
    total, and a fully-deactivated account with `stake: null` still counts — it sits at
    exactly its `rentExemptReserve` until withdrawn."""
    _stub(monkeypatch, {"jsonrpc": "2.0", "id": 1, "result": [DELEGATED, NULL_STAKE]})
    total = stake_solana.staked_sol(stake_solana.read(ADDRESS))
    assert total == pytest.approx((201957056 + 2282880) / 1e9)


def test_no_stake_accounts_is_none_not_zero(monkeypatch):
    _stub(monkeypatch, {"jsonrpc": "2.0", "id": 1, "result": []})
    assert stake_solana.staked_sol(stake_solana.read(ADDRESS)) is None


def test_a_200_carrying_a_json_rpc_error_raises_rather_than_reading_as_zero(
    monkeypatch,
):
    """The silent-failure mode the probe found: a disabled or rate-limited endpoint answers
    `200 {"error": {...}}`, and a reader that only checked the HTTP status would treat a
    refusal as an empty, valid answer."""
    _stub(
        monkeypatch,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": 429, "message": "exceeded compute units"},
        },
    )
    with pytest.raises(wallet.WalletError, match="exceeded compute units"):
        stake_solana.read(ADDRESS)


def test_a_200_with_no_result_raises(monkeypatch):
    """`result: []` is a valid "nothing staked" answer; a missing `result` key entirely is a
    malformed response and must not be read as the same thing."""
    _stub(monkeypatch, {"jsonrpc": "2.0", "id": 1})
    with pytest.raises(wallet.WalletError, match="no `result`"):
        stake_solana.read(ADDRESS)
