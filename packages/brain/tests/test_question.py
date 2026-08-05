import pytest
from brain.question import parse_assets
from core.canon import Registry

REGISTRY = Registry(
    assets={
        "eth": "ETH", "ethereum": "ETH", "ether": "ETH",
        "btc": "BTC", "bitcoin": "BTC",
        "sol": "SOL", "solana": "SOL",
        "gold": "GOLD",
        "btc.d": "BTC.D", "bitcoin dominance": "BTC.D",
        "s&p": "SPX", "s&p 500": "SPX",
    },
    # The CoinGecko snapshot is full of tickers that collide with ordinary English.
    tickers={"ETH": {}, "BTC": {}, "ALL": {}, "FOR": {}, "ONE": {}, "ME": {}, "UP": {}},
)


def test_finds_a_ticker_in_a_natural_question():
    assert parse_assets("where is my roster on ETH", REGISTRY) == ["ETH"]


def test_finds_an_asset_by_its_spoken_name():
    assert parse_assets("what does everyone think about bitcoin", REGISTRY) == ["BTC"]


def test_is_case_insensitive_for_curated_aliases():
    assert parse_assets("thoughts on Ethereum?", REGISTRY) == ["ETH"]


def test_finds_multiple_assets_in_order_of_appearance():
    assert parse_assets("compare bitcoin and solana", REGISTRY) == ["BTC", "SOL"]


def test_deduplicates_aliases_of_the_same_asset():
    assert parse_assets("is ETH or ethereum better", REGISTRY) == ["ETH"]


def test_matches_multi_word_aliases():
    assert parse_assets("where are we on bitcoin dominance", REGISTRY) == ["BTC.D"]


def test_prefers_the_longest_alias_match():
    """`bitcoin dominance` must not degrade to `bitcoin`."""
    assert parse_assets("bitcoin dominance outlook", REGISTRY) == ["BTC.D"]


def test_handles_punctuation_around_tickers():
    assert parse_assets("what about ETH, SOL, and gold?", REGISTRY) == ["ETH", "SOL", "GOLD"]


# ── the false-positive hazard ───────────────────────────────────────────────

@pytest.mark.parametrize("question", [
    "what are they all saying",
    "is it time for a change",
    "which one is best",
    "tell me what is up",
    "give me the roster view",
])
def test_ordinary_english_words_are_not_matched_as_tickers(question):
    """The CoinGecko snapshot contains ALL, FOR, ONE, UP, ME. Matching bare lowercase
    words against it would make almost every question resolve to a random memecoin, so
    only the curated alias map is consulted for lowercase text."""
    assert parse_assets(question, REGISTRY) == []


def test_an_uppercase_ticker_not_in_the_curated_map_is_still_accepted():
    """Writing it in caps is an explicit signal it's a ticker, not a word."""
    assert parse_assets("what about ONE", REGISTRY) == ["ONE"]


def test_a_question_with_no_asset_returns_empty():
    assert parse_assets("what changed this week", REGISTRY) == []


def test_empty_question_returns_empty():
    assert parse_assets("", REGISTRY) == []
