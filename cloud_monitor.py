#!/usr/bin/env python3
"""
Cloud-based stock monitor for GitHub Actions.
Reads alerts from ALERTS_CONFIG secret, checks prices, sends ntfy notifications.
Triggered alerts are tracked via a GitHub Actions artifact.
"""

import json
import os
import ssl
import urllib.request
import urllib.parse
from datetime import datetime
from pathlib import Path

ssl_ctx = ssl.create_default_context()

# File to track which alerts have already been triggered (persisted as artifact)
TRIGGERED_FILE = Path("triggered_alerts.json")


def fetch_price(symbol):
    """Fetch current price from Yahoo Finance."""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{urllib.parse.quote(symbol)}?interval=1d&range=1d"
    )
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    })
    try:
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            chart = data.get("chart", {}).get("result", [])
            if not chart:
                return None
            meta = chart[0].get("meta", {})
            return {
                "price": meta.get("regularMarketPrice"),
                "name": meta.get("shortName", symbol),
                "currency": meta.get("currency", "USD"),
            }
    except Exception as e:
        print(f"  Error fetching {symbol}: {e}")
        return None


def send_ntfy(topic, message):
    """Send push notification via ntfy.sh."""
    url = f"https://ntfy.sh/{topic}"
    payload = json.dumps({
        "topic": topic,
        "title": message.split("\n")[0],
        "message": "\n".join(message.split("\n")[1:]).strip() or message.split("\n")[0],
        "priority": 4,
        "tags": ["chart_with_upwards_trend"],
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as resp:
            print(f"  ntfy push sent to topic: {topic}")
            return True
    except Exception as e:
        print(f"  ntfy error: {e}")
        return False


def load_triggered():
    """Load set of already-triggered alert keys."""
    if TRIGGERED_FILE.exists():
        return set(json.loads(TRIGGERED_FILE.read_text()))
    return set()


def save_triggered(triggered):
    """Save set of triggered alert keys."""
    TRIGGERED_FILE.write_text(json.dumps(list(triggered)))


def main():
    # Load config from environment
    config_json = os.environ.get("ALERTS_CONFIG", "")
    ntfy_topic = os.environ.get("NTFY_TOPIC", "")

    if not config_json:
        print("ERROR: ALERTS_CONFIG secret not set")
        return
    if not ntfy_topic:
        print("ERROR: NTFY_TOPIC secret not set")
        return

    config = json.loads(config_json)
    alerts = config.get("alerts", [])
    now = datetime.utcnow().strftime("%H:%M UTC")

    # Load previously triggered alerts
    triggered = load_triggered()

    active = [
        a for a in alerts
        if a.get("enabled") and f"{a['symbol']}_{a['condition']}_{a['target_price']}" not in triggered
    ]

    if not active:
        print(f"[{now}] No active alerts.")
        return

    # Collect unique symbols
    symbols = list(set(a["symbol"] for a in active))
    print(f"[{now}] Checking {len(symbols)} symbol(s): {', '.join(symbols)}")

    # Fetch prices
    prices = {}
    for sym in symbols:
        result = fetch_price(sym)
        if result:
            prices[sym] = result
            print(f"  {sym}: {result['currency']} {result['price']:.2f}")

    # Evaluate alerts
    for alert in active:
        sym = alert["symbol"]
        if sym not in prices:
            continue

        current_price = prices[sym]["price"]
        target = alert["target_price"]
        condition = alert["condition"]
        name = alert.get("name", sym)
        alert_key = f"{sym}_{condition}_{target}"

        hit = False
        if condition == "above" and current_price >= target:
            hit = True
        elif condition == "below" and current_price <= target:
            hit = True

        if hit:
            currency = prices[sym]["currency"]
            arrow = "\u2191" if condition == "above" else "\u2193"
            msg = (
                f"STOCK ALERT: {name} ({sym})\n"
                f"{arrow} Price {condition} target!\n"
                f"Current: {currency} {current_price:.2f}\n"
                f"Target: {currency} {target:.2f}"
            )
            print(f"  ** ALERT TRIGGERED: {sym} {condition} {target} **")
            if send_ntfy(ntfy_topic, msg):
                triggered.add(alert_key)

    save_triggered(triggered)


if __name__ == "__main__":
    main()
