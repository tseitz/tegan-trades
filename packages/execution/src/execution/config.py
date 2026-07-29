"""Settings from ``cfg/execution.yaml``, credentials from the environment. Never both.

The split is the whole point. ``cfg/`` is committed — it is the repo's source of truth for
the roster, the price routing and the canon registry, and everything in it is meant to be
read in a diff. A private key in there would be in the history permanently after one commit.
So the file carries *how much to risk* and the environment carries *what can sign*, and
neither can leak into the other by accident.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from execution import guards, venues
from execution.alpaca_broker import AlpacaCredentials
from execution.broker import MAINNET, TESTNET, Credentials

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_PATH = REPO_ROOT / "cfg" / "execution.yaml"

ADDRESS_VAR = "HYPERLIQUID_ACCOUNT_ADDRESS"
SECRET_VAR = "HYPERLIQUID_SECRET_KEY"

ALPACA_KEY_VAR = "ALPACA_API_KEY_ID"
ALPACA_SECRET_VAR = "ALPACA_API_SECRET_KEY"


class MissingCredentials(Exception):
    """No key is configured, so nothing can be signed."""


@dataclass(frozen=True)
class Config:
    """What to risk, and where.

    ``max_notional_frac`` defaults to 3.0 on measured grounds rather than taste: across the
    77 decisions recorded to 2026-07-27 the tightest stop sits 0.51% from its entry, which at
    a 1% risk budget implies 2.0x leverage — so a 3x ceiling is inert on every candidate the
    engine has ever produced and only engages on a zone narrower than anything yet seen.
    """
    network: str = TESTNET
    risk_pct: float = 0.01
    max_notional_frac: float | None = 3.0
    venue: str = "hyperliquid"
    # Liquidity floors. HIP-3 lets anyone deploy a market, so "the venue lists it" stopped
    # being evidence that it trades — see ``execution.liquidity``. Defaults measured against
    # mainnet, where they clear a market with no book at all and one doing $133k a day.
    min_day_volume: float = guards.MIN_DAY_VOLUME_USD
    min_open_interest: float = guards.MIN_OPEN_INTEREST_USD
    # None means "decide from the network". Testnet books are mock and mostly empty — every
    # HIP-3 market there fails ``no_book`` — so enforcing would make the rehearsal venue
    # unusable while protecting nothing. It stays *measured and reported* on testnet, just
    # not blocking, so a candidate that mainnet would refuse still says so out loud.
    enforce_liquidity: bool | None = None

    @property
    def liquidity_enforced(self) -> bool:
        if self.enforce_liquidity is not None:
            return self.enforce_liquidity
        # Off for equities, and not as a convenience. The gate measures 24h volume, open
        # interest and near-touch depth, because HIP-3 lets anyone deploy a perp market so
        # "the venue lists it" stopped being evidence that it trades. An equity has no open
        # interest at all and Alpaca's order-entry API publishes no book, so
        # ``AlpacaBroker.liquidity`` honestly reports "not measured" — and an unmeasured
        # market is a refusal. Enforcing here would refuse every equity, every time.
        if self.venue == venues.ALPACA:
            return False
        return self.network == MAINNET

    def validate(self) -> None:
        if self.venue not in venues.NETWORKS:
            raise ValueError(
                f"venue must be one of {sorted(venues.NETWORKS)}, got {self.venue!r}"
            )
        allowed = venues.NETWORKS[self.venue]
        if self.network not in allowed:
            raise ValueError(
                f"network for venue {self.venue!r} must be one of {sorted(allowed)}, "
                f"got {self.network!r}"
            )
        if not 0 < self.risk_pct <= 1:
            raise ValueError(f"risk_pct must be in (0, 1], got {self.risk_pct}")
        if self.max_notional_frac is not None and self.max_notional_frac <= 0:
            raise ValueError(
                f"max_notional_frac must be positive, got {self.max_notional_frac}"
            )


def load(path=DEFAULT_PATH) -> Config:
    """Read the config, falling back to the defaults when the file is absent.

    A missing file is not an error — the defaults are testnet at 1%, which is the safe
    configuration. A file that exists but is malformed *is* an error, because a typo in the
    risk budget must never be silently replaced by a default the user did not choose.
    """
    path = Path(path)
    if not path.exists():
        config = Config()
        config.validate()
        return config

    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    fields = ("network", "risk_pct", "max_notional_frac", "venue",
              "min_day_volume", "min_open_interest", "enforce_liquidity")
    known = {f: doc[f] for f in fields if f in doc}
    unknown = set(doc) - set(fields)
    if unknown:
        raise ValueError(
            f"{path} has unrecognised key(s): {', '.join(sorted(unknown))}. "
            f"A misspelled setting is silently ignored otherwise, which on this file means "
            f"trading at a risk level nobody chose."
        )
    # A venue named without a network resolves to that venue's *rehearsal*, never to the
    # inherited default of a different venue. Without this, `venue: alpaca` alone would keep
    # `network: testnet` and fail validation with a message about networks rather than about
    # the setting that was actually missing.
    venue = known.get("venue", Config.venue)
    if "network" not in known and venue in venues.NETWORKS:
        known["network"] = venues.default_network(venue)

    config = Config(**known)
    config.validate()
    return config


def credentials(env=None) -> Credentials:
    """The signing key, from the environment only.

    Raises rather than returning None: every caller needs a key to do anything, and an
    explicit message naming the two variables is more useful than a ``NoneType`` traceback
    three frames into the SDK.
    """
    env = os.environ if env is None else env
    address = (env.get(ADDRESS_VAR) or "").strip()
    secret = (env.get(SECRET_VAR) or "").strip()
    missing = [name for name, value in ((ADDRESS_VAR, address), (SECRET_VAR, secret))
               if not value]
    if missing:
        raise MissingCredentials(
            f"{' and '.join(missing)} not set. Add them to .env (gitignored) and load with "
            f"`set -a; source .env; set +a`. Use an API wallet generated under More -> API, "
            f"not your main wallet key — an agent wallet can trade but cannot withdraw."
        )
    return Credentials(account_address=address, secret_key=secret)


def alpaca_credentials(env=None) -> AlpacaCredentials:
    """The Alpaca API key pair, from the environment only — same rule as the signing key.

    Weaker than a wallet key in one specific way worth knowing: these cannot move money out
    of the account, because withdrawals are ACH to a bank on file and are not reachable from
    the trading API. They can still place and cancel orders, so they stay out of ``cfg/``.
    """
    env = os.environ if env is None else env
    key_id = (env.get(ALPACA_KEY_VAR) or "").strip()
    secret = (env.get(ALPACA_SECRET_VAR) or "").strip()
    missing = [name for name, value in ((ALPACA_KEY_VAR, key_id), (ALPACA_SECRET_VAR, secret))
               if not value]
    if missing:
        raise MissingCredentials(
            f"{' and '.join(missing)} not set. Add them to .env (gitignored) and load with "
            f"`set -a; source .env; set +a`. Generate a PAPER key first at "
            f"https://app.alpaca.markets/paper/dashboard/overview — paper and live keys are "
            f"different pairs and a live key will not authenticate against the paper host."
        )
    return AlpacaCredentials(key_id=key_id, secret_key=secret)


def credentials_for(venue: str, env=None):
    """The right credential type for a venue. Raises on an unknown venue rather than guessing."""
    if venue == venues.ALPACA:
        return alpaca_credentials(env)
    if venue == venues.HYPERLIQUID:
        return credentials(env)
    raise ValueError(f"unknown venue {venue!r}; expected one of {sorted(venues.NETWORKS)}")


def requires_typed_confirmation(network: str) -> bool:
    """Real money needs more than a flag, on every venue.

    ``--network mainnet`` is one keystroke away from ``--network testnet`` in shell history,
    and ``live`` is one word from ``paper``. A typed word is the cheapest barrier that cannot
    be reached by an arrow key.

    Written against the venue table rather than against ``MAINNET`` alone: the same check has
    to cover a second venue whose real-money network is spelled differently, and a comparison
    that only knows one spelling waves the other one through.
    """
    return venues.is_real_money(network)
