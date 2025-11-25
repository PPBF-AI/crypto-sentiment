import os
import json
from datetime import datetime
from typing import Dict, Any

import requests


STATE_FILE = "sentiment_state.json"   # for previous values (to compute deltas)
OUTPUT_FILE = "sentiment.json"        # consumed by the HTML dashboard


# -----------------------------
#  API HELPER FUNCTIONS
# -----------------------------

def get_fear_greed() -> int:
    """Get Crypto Fear & Greed Index (0–100)."""
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        r.raise_for_status()
        data = r.json()
        return int(data["data"][0]["value"])
    except Exception:
        return 50  # neutral fallback


def get_btc_price_change() -> float:
    """Get BTC 24h price change in %, e.g. -3.2 or +1.5."""
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/bitcoin"
            "?localization=false&tickers=false&market_data=true",
            timeout=10
        )
        r.raise_for_status()
        data = r.json()
        change = data["market_data"]["price_change_percentage_24h"]
        return float(change)
    except Exception:
        return 0.0  # neutral fallback


def get_news_sentiment() -> int:
    """
    Super simple news sentiment based on titles via GNews demo API.
    Returns 0–100 (50 = neutral).
    """
    positive_words = ["up", "bull", "rally", "gain", "positive", "growth", "strong"]
    negative_words = ["down", "bear", "crash", "drop", "negative", "weak"]

    try:
        # demo token – for production you’d want your own key
        r = requests.get(
            "https://gnews.io/api/v4/search?q=bitcoin&lang=en&token=demo",
            timeout=10
        )
        r.raise_for_status()
        data = r.json()

        score = 0
        count = 0

        for article in data.get("articles", []):
            title = (article.get("title") or "").lower()
            if not title:
                continue
            count += 1

            if any(w in title for w in positive_words):
                score += 1
            if any(w in title for w in negative_words):
                score -= 1

        if count == 0:
            return 50

        normalized = 50 + (score * 3)  # keep effect modest
        return max(0), min(100, int(normalized))
    except Exception:
        return 50


# -----------------------------
#  SIMPLE DERIVED INDICATORS
# -----------------------------

def price_action_sentiment(change_24h: float) -> int:
    """
    Map 24h % change to a 0–100 sentiment score.
    Example:
      +10% or more  -> ~80+
      0%            -> 50
      -10% or less  -> ~20
    """
    # clamp change to [-10, 10]
    ch = max(-10.0, min(10.0, change_24h))
    # map -10..10 to 20..80
    value = 50 + (ch * 3.0)
    return max(0, min(100, int(round(value))))


def get_social_sentiment() -> int:
    """
    Placeholder for future social sentiment (X/Reddit, etc).
    For now: neutral 50.
    """
    return 50


def get_onchain_sentiment() -> int:
    """
    Placeholder for future on-chain data (flows, addresses).
    For now: neutral 50.
    """
    return 50


# -----------------------------
#  STATE (FOR DELTAS)
# -----------------------------

def load_previous_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(driver_values: Dict[str, int]) -> None:
    state = {"drivers": driver_values, "timestamp": datetime.utcnow().isoformat()}
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def compute_deltas(
    current: Dict[str, int],
    previous: Dict[str, Any]
) -> Dict[str, int]:
    deltas: Dict[str, int] = {}
    prev_drivers = previous.get("drivers", {}) if isinstance(previous, dict) else {}

    for key, val in current.items():
        prev_val = prev_drivers.get(key)
        if isinstance(prev_val, (int, float)):
            deltas[key] = int(round(val - prev_val))
        else:
            deltas[key] = 0
    return deltas


# -----------------------------
#  MAIN CALCULATION
# -----------------------------

def build_sentiment_payload() -> Dict[str, Any]:
    # 1) collect raw indicators
    fg = get_fear_greed()
    change_24h = get_btc_price_change()
    news = get_news_sentiment()
    social = get_social_sentiment()
    price_sent = price_action_sentiment(change_24h)
    onchain = get_onchain_sentiment()

    # 2) current driver values (0–100)
    driver_values = {
        "fear_greed": fg,
        "news": news,
        "social": social,
        "price_action": price_sent,
        "on_chain": onchain,
    }

    # 3) load previous and compute deltas
    prev_state = load_previous_state()
    deltas = compute_deltas(driver_values, prev_state)

    # 4) compute total sentiment as weighted average (you can tune these weights)
    weights = {
        "fear_greed": 0.25,
        "news": 0.20,
        "social": 0.15,
        "price_action": 0.25,
        "on_chain": 0.15,
    }

    numerator = 0.0
    denom = 0.0
    for key, w in weights.items():
        numerator += driver_values[key] * w
        denom += w
    total_value = int(round(numerator / denom)) if denom > 0 else 50

    # 5) build output structure expected by the dashboard
    drivers_struct: Dict[str, Any] = {
        "fear_greed": {
            "label": "Fear & Greed",
            "value": driver_values["fear_greed"],
            "delta": deltas["fear_greed"],
        },
        "news": {
            "label": "News Sentiment",
            "value": driver_values["news"],
            "delta": deltas["news"],
        },
        "social": {
            "label": "Social Buzz",
            "value": driver_values["social"],
            "delta": deltas["social"],
        },
        "price_action": {
            "label": "Price Action",
            "value": driver_values["price_action"],
            "delta": deltas["price_action"],
        },
        "on_chain": {
            "label": "On-chain Activity",
            "value": driver_values["on_chain"],
            "delta": deltas["on_chain"],
        },
    }

    payload: Dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat(),
        "total": {"value": total_value},
        "drivers": drivers_struct,

        # legacy / convenience fields (optional)
        "value": total_value,
        "fear_greed": fg,
        "news_sentiment": news,
        "price_sentiment": price_sent,
        "price_change_24h": change_24h,
    }

    # 6) update state file for next run
    save_state(driver_values)

    return payload


def main() -> None:
    data = build_sentiment_payload()
    with open(OUTPUT_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print("Sentiment updated:")
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()