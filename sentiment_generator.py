import os
import json
import random
from datetime import datetime
from typing import Dict, Any

import requests


STATE_FILE = "sentiment_state.json"   # stores previous driver values to compute deltas
OUTPUT_FILE = "sentiment.json"        # consumed by your dashboard
HISTORY_FILE = "history.json"         # time series for sparklines
MAX_HISTORY_POINTS = 200              # keep last N points


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
            "?localization=false&tickers=false&market_data=true"
            "&community_data=false&developer_data=false&sparkline=false",
            timeout=10
        )
        r.raise_for_status()
        data = r.json()
        change = data["market_data"]["price_change_percentage_24h"]
        return float(change)
    except Exception:
        return 0.0  # neutral fallback


def price_action_sentiment(change_24h: float) -> int:
    """
    Map 24h % change to a 0–100 sentiment score.
    Example:
      +10% or more  -> ~80+
      0%            -> 50
      -10% or less  -> ~20
    """
    ch = max(-10.0, min(10.0, change_24h))  # clamp
    value = 50 + ch * 3.0
    return max(0, min(100, int(round(value))))


def get_liquidity_sentiment() -> int:
    """
    Estimate liquidity sentiment based on BTC trading volume
    vs the last 7 days average. Returns 0–100, where 50 is neutral.
    """
    try:
        # current 24h volume
        r_now = requests.get(
            "https://api.coingecko.com/api/v3/coins/bitcoin"
            "?localization=false&tickers=false&market_data=true"
            "&community_data=false&developer_data=false&sparkline=false",
            timeout=10
        )
        r_now.raise_for_status()
        data_now = r_now.json()
        vol_now = float(data_now["market_data"]["total_volume"]["usd"])

        # 7-day volume history
        r_hist = requests.get(
            "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
            "?vs_currency=usd&days=7&interval=daily",
            timeout=10
        )
        r_hist.raise_for_status()
        hist = r_hist.json()
        vols = [float(v[1]) for v in hist.get("total_volumes", []) if v and v[1] is not None]

        if not vols:
            return 50

        avg_vol = sum(vols) / len(vols)
        if avg_vol <= 0:
            return 50

        ratio = vol_now / avg_vol  # >1 = above average liquidity
        ratio = max(0.25, min(2.5, ratio))  # clamp

        # ratio 1.0 -> 50, 2.5 -> ~85, 0.25 -> ~15
        score = 50 + (ratio - 1.0) * 30.0
        score_int = int(round(score))
        return max(0, min(100, score_int))

    except Exception:
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
#  HISTORY (FOR SPARKLINES)
# -----------------------------

def load_history() -> Dict[str, Any]:
    if not os.path.exists(HISTORY_FILE):
        return {"points": []}
    try:
        with open(HISTORY_FILE, "r") as f:
            data = json.load(f)
            if "points" not in data or not isinstance(data["points"], list):
                return {"points": []}
            return data
    except Exception:
        return {"points": []}


def update_history(total_value: int, driver_values: Dict[str, int]) -> None:
    history = load_history()
    points = history.get("points", [])
    print(f"[DEBUG] History before: {len(points)} points")
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "total": int(total_value),
        "drivers": {k: int(v) for k, v in driver_values.items()},
    }
    points.append(entry)
    if len(points) > MAX_HISTORY_POINTS:
        points = points[-MAX_HISTORY_POINTS:]
    history["points"] = points
    print(f"[DEBUG] History after: {len(points)} points")
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


# -----------------------------
#  MEAN-REVERTING SIMULATION
# -----------------------------

def _get_prev_driver_value(prev_state: Dict[str, Any], name: str, default: float = 50.0) -> float:
    if not isinstance(prev_state, dict):
        return default
    drivers = prev_state.get("drivers", {})
    if not isinstance(drivers, dict):
        return default
    val = drivers.get(name)
    if isinstance(val, (int, float)):
        return float(val)
    return default


def simulate_driver(
    name: str,
    prev_state: Dict[str, Any],
    base: float = 50.0,
    volatility: float = 7.0,
    mean_reversion: float = 0.4
) -> int:
    """
    Simple mean-reverting random walk around `base` (0–100).
    - `volatility`: grootte van de willekeurige stap per run
    - `mean_reversion`: hoe sterk de waarde teruggetrokken wordt naar `base`
    """
    prev_val = _get_prev_driver_value(prev_state, name, default=base)

    # willekeurige stap
    noise = random.uniform(-volatility, volatility)
    # mean reversion component
    reversion = mean_reversion * (base - prev_val)

    new_val = prev_val + noise + reversion
    new_val = max(0, min(100, round(new_val)))
    return int(new_val)


def get_news_sentiment(prev_state: Dict[str, Any]) -> int:
    """
    Simulated news sentiment (0–100) around neutral (50).
    Later kun je dit vervangen door een echte news-API.
    """
    return simulate_driver(
        name="news",
        prev_state=prev_state,
        base=50.0,
        volatility=6.0,
        mean_reversion=0.4,
    )


def get_social_sentiment(prev_state: Dict[str, Any]) -> int:
    """
    Simulated social sentiment (X/Reddit, etc.), mean-reverting around 50.
    """
    return simulate_driver(
        name="social",
        prev_state=prev_state,
        base=50.0,
        volatility=8.0,
        mean_reversion=0.45,
    )


def get_onchain_sentiment(prev_state: Dict[str, Any]) -> int:
    """
    Simulated on-chain sentiment, mean-reverting around 50.
    """
    return simulate_driver(
        name="on_chain",
        prev_state=prev_state,
        base=50.0,
        volatility=5.0,
        mean_reversion=0.35,
    )


# -----------------------------
#  MAIN CALCULATION
# -----------------------------

def build_sentiment_payload() -> Dict[str, Any]:
    # 0) load previous state first (for deltas & simulation)
    prev_state = load_previous_state()

    # 1) collect raw indicators
    fg = get_fear_greed()
    change_24h = get_btc_price_change()
    news = get_news_sentiment(prev_state)
    social = get_social_sentiment(prev_state)
    onchain = get_onchain_sentiment(prev_state)
    price_sent = price_action_sentiment(change_24h)
    liquidity = get_liquidity_sentiment()

    # 2) current driver values (0–100)
    driver_values: Dict[str, int] = {
        "fear_greed": fg,
        "news": news,
        "social": social,
        "price_action": price_sent,
        "on_chain": onchain,
        "liquidity": liquidity,
    }

    # 3) compute deltas vs. previous state
    deltas = compute_deltas(driver_values, prev_state)

    # 4) compute total sentiment as weighted average
    weights = {
        "fear_greed": 0.20,
        "news": 0.15,
        "social": 0.15,
        "price_action": 0.20,
        "on_chain": 0.15,
        "liquidity": 0.15,
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
        "liquidity": {
            "label": "Liquidity",
            "value": driver_values["liquidity"],
            "delta": deltas["liquidity"],
        },
    }

    payload: Dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat(),
        "total": {"value": total_value},
        "drivers": drivers_struct,

        # legacy / convenience fields
        "value": total_value,
        "fear_greed": fg,
        "news_sentiment": news,
        "price_sentiment": price_sent,
        "price_change_24h": change_24h,
        "liquidity_sentiment": liquidity,
    }

    # 6) update state & history for next runs
    save_state(driver_values)
    update_history(total_value, driver_values)

    return payload


def main() -> None:
    data = build_sentiment_payload()
    with open(OUTPUT_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print("Sentiment updated:")
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()