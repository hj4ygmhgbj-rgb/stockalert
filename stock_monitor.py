#!/usr/bin/env python3
"""
StockAlert Monitor - Checks stock prices and sends notifications
via iMessage, Signal, or WhatsApp when price targets are hit.

Usage:
  python3 stock_monitor.py                  # Run once (for cron/launchd)
  python3 stock_monitor.py --loop           # Run continuously
  python3 stock_monitor.py --add AAPL below 150
  python3 stock_monitor.py --remove AAPL
  python3 stock_monitor.py --list
  python3 stock_monitor.py --set-channel imessage
  python3 stock_monitor.py --set-phone +31612345678
"""

import json
import subprocess
import sys
import time
import urllib.request
import urllib.parse
import ssl
from datetime import datetime
from pathlib import Path

ALERTS_FILE = Path(__file__).parent / "alerts.json"
ssl_ctx = ssl.create_default_context()


def load_config():
    with open(ALERTS_FILE) as f:
        return json.load(f)


def save_config(config):
    with open(ALERTS_FILE, "w") as f:
        json.dump(config, f, indent=2)
    print(f"Config saved to {ALERTS_FILE}")


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


# ── Notification channels ───────────────────────────────────────────


def send_macos_notification(title, message):
    """Show a macOS notification banner with sound."""
    escaped_msg = message.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    escaped_title = title.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display notification "{escaped_msg}" with title "{escaped_title}" sound name "Glass"'
    subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    print(f"  macOS notification shown")


def send_imessage(phone_number, message):
    """Send iMessage via AppleScript (macOS only)."""
    # Escape backslashes and quotes for AppleScript
    escaped = message.replace("\\", "\\\\").replace('"', '\\"')
    send_script = f'''
    tell application "Messages"
        set targetService to 1st account whose service type = iMessage
        set targetBuddy to participant "{phone_number}" of targetService
        send "{escaped}" to targetBuddy
    end tell
    '''
    result = subprocess.run(
        ["osascript", "-e", send_script],
        capture_output=True, text=True, timeout=15
    )
    if result.returncode != 0:
        print(f"  iMessage error: {result.stderr.strip()}")
        return False
    print(f"  iMessage sent to {phone_number}")

    # Close Messages windows after a short delay so iPhone treats it as unread
    try:
        time.sleep(2)
        hide_script = '''
        tell application "System Events"
            if exists process "Messages" then
                set visible of process "Messages" to false
            end if
        end tell
        '''
        subprocess.run(["osascript", "-e", hide_script], capture_output=True, text=True, timeout=10)
        print(f"  Messages window hidden")
    except Exception:
        print(f"  (Could not hide Messages window, but message was sent)")
    return True


def send_signal(phone_number, message, signal_cli_path="signal-cli"):
    """Send Signal message via signal-cli."""
    result = subprocess.run(
        [signal_cli_path, "send", "-m", message, phone_number],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"  Signal error: {result.stderr.strip()}")
        return False
    print(f"  Signal message sent to {phone_number}")
    return True


def send_whatsapp(phone_number, message, settings):
    """Send WhatsApp message via Twilio API."""
    account_sid = settings.get("twilio_account_sid", "")
    auth_token = settings.get("twilio_auth_token", "")
    from_number = settings.get("twilio_whatsapp_from", "")

    if not all([account_sid, auth_token, from_number]):
        print("  WhatsApp error: Twilio credentials not configured in alerts.json")
        return False

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    data = urllib.parse.urlencode({
        "From": from_number,
        "To": f"whatsapp:{phone_number}",
        "Body": message,
    }).encode()

    req = urllib.request.Request(url, data=data, method="POST")
    # Basic auth
    import base64
    credentials = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
    req.add_header("Authorization", f"Basic {credentials}")

    try:
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as resp:
            print(f"  WhatsApp message sent to {phone_number}")
            return True
    except Exception as e:
        print(f"  WhatsApp error: {e}")
        return False


def send_ntfy(topic, message):
    """Send push notification via ntfy.sh (free, no account needed)."""
    url = f"https://ntfy.sh/{topic}"
    # Use JSON mode to avoid header encoding issues with emojis
    import json as _json
    payload = _json.dumps({
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


def send_notification(settings, message):
    """Send notification via the configured channel."""
    channel = settings.get("channel", "imessage")
    phone = settings.get("phone_number", "")

    # Always fire a macOS notification with sound as well
    first_line = message.split("\n")[0]
    send_macos_notification("📈 StockAlert", first_line)

    if channel == "ntfy":
        topic = settings.get("ntfy_topic", "")
        if not topic:
            print("  ERROR: Set your ntfy topic first!")
            return False
        return send_ntfy(topic, message)

    if not phone or phone == "+31612345678":
        print("  ERROR: Set your phone number first!")
        print("  Run: python3 stock_monitor.py --set-phone +31612345678")
        return False

    if channel == "imessage":
        return send_imessage(phone, message)
    elif channel == "signal":
        return send_signal(phone, message, settings.get("signal_cli_path", "signal-cli"))
    elif channel == "whatsapp":
        return send_whatsapp(phone, message, settings)
    else:
        print(f"  Unknown channel: {channel}")
        return False


# ── Core logic ──────────────────────────────────────────────────────


def check_alerts():
    """Check all enabled alerts and send notifications for triggered ones."""
    config = load_config()
    settings = config["settings"]
    alerts = config["alerts"]
    now = datetime.now().strftime("%H:%M")

    active = [a for a in alerts if a.get("enabled") and not a.get("triggered")]
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
    triggered_any = False
    for alert in active:
        sym = alert["symbol"]
        if sym not in prices:
            continue

        current_price = prices[sym]["price"]
        target = alert["target_price"]
        condition = alert["condition"]
        name = alert.get("name", sym)

        hit = False
        if condition == "above" and current_price >= target:
            hit = True
        elif condition == "below" and current_price <= target:
            hit = True

        if hit:
            currency = prices[sym]["currency"]
            arrow = "↑" if condition == "above" else "↓"
            msg = (
                f"🚨 STOCK ALERT: {name} ({sym})\n"
                f"{arrow} Price {condition} target!\n"
                f"Current: {currency} {current_price:.2f}\n"
                f"Target: {currency} {target:.2f}"
            )
            print(f"  ** ALERT TRIGGERED: {sym} {condition} {target} **")
            if send_notification(settings, msg):
                alert["triggered"] = True
                triggered_any = True

    if triggered_any:
        save_config(config)


def reset_alert(symbol):
    """Reset a triggered alert so it can fire again."""
    config = load_config()
    for alert in config["alerts"]:
        if alert["symbol"].upper() == symbol.upper():
            alert["triggered"] = False
            save_config(config)
            print(f"Alert for {symbol} reset.")
            return
    print(f"No alert found for {symbol}")


# ── CLI ─────────────────────────────────────────────────────────────


def cmd_add(args):
    if len(args) < 3:
        print("Usage: --add SYMBOL above|below PRICE")
        sys.exit(1)
    symbol = args[0].upper()
    condition = args[1].lower()
    if condition not in ("above", "below"):
        print("Condition must be 'above' or 'below'")
        sys.exit(1)
    price = float(args[2])

    config = load_config()
    # Remove existing alert for same symbol+condition
    config["alerts"] = [
        a for a in config["alerts"]
        if not (a["symbol"] == symbol and a["condition"] == condition)
    ]
    config["alerts"].append({
        "symbol": symbol,
        "name": symbol,
        "condition": condition,
        "target_price": price,
        "enabled": True,
        "triggered": False,
    })
    save_config(config)
    print(f"Alert added: {symbol} {condition} {price}")


def cmd_remove(args):
    if not args:
        print("Usage: --remove SYMBOL")
        sys.exit(1)
    symbol = args[0].upper()
    config = load_config()
    before = len(config["alerts"])
    config["alerts"] = [a for a in config["alerts"] if a["symbol"] != symbol]
    if len(config["alerts"]) < before:
        save_config(config)
        print(f"Removed alerts for {symbol}")
    else:
        print(f"No alerts found for {symbol}")


def cmd_list():
    config = load_config()
    settings = config["settings"]
    print(f"\nChannel: {settings['channel']}")
    print(f"Phone:   {settings['phone_number']}")
    print(f"Check interval: {settings['check_interval_seconds']}s\n")

    if not config["alerts"]:
        print("No alerts configured.")
        return

    print(f"{'Symbol':<8} {'Condition':<8} {'Target':>10} {'Enabled':>8} {'Triggered':>10}")
    print("-" * 50)
    for a in config["alerts"]:
        print(
            f"{a['symbol']:<8} {a['condition']:<8} "
            f"{a['target_price']:>10.2f} "
            f"{'yes' if a.get('enabled') else 'no':>8} "
            f"{'YES' if a.get('triggered') else 'no':>10}"
        )
    print()


def cmd_set_channel(args):
    if not args or args[0] not in ("imessage", "signal", "whatsapp"):
        print("Usage: --set-channel imessage|signal|whatsapp")
        sys.exit(1)
    config = load_config()
    config["settings"]["channel"] = args[0]
    save_config(config)
    print(f"Channel set to: {args[0]}")


def cmd_set_phone(args):
    if not args:
        print("Usage: --set-phone +31612345678")
        sys.exit(1)
    config = load_config()
    config["settings"]["phone_number"] = args[0]
    save_config(config)
    print(f"Phone number set to: {args[0]}")


def cmd_test():
    """Send a test message to verify your setup works."""
    config = load_config()
    settings = config["settings"]
    msg = f"✅ StockAlert test message - {datetime.now().strftime('%H:%M:%S')}\nYour notifications are working!"
    print(f"Sending test message via {settings['channel']}...")
    send_notification(settings, msg)


def main():
    args = sys.argv[1:]

    if not args:
        check_alerts()
        return

    cmd = args[0]
    rest = args[1:]

    if cmd == "--loop":
        config = load_config()
        interval = config["settings"].get("check_interval_seconds", 60)
        print(f"Running in loop mode (every {interval}s). Press Ctrl+C to stop.\n")
        try:
            while True:
                check_alerts()
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nStopped.")

    elif cmd == "--add":
        cmd_add(rest)
    elif cmd == "--remove":
        cmd_remove(rest)
    elif cmd == "--list":
        cmd_list()
    elif cmd == "--set-channel":
        cmd_set_channel(rest)
    elif cmd == "--set-phone":
        cmd_set_phone(rest)
    elif cmd == "--reset":
        if rest:
            reset_alert(rest[0])
        else:
            print("Usage: --reset SYMBOL")
    elif cmd == "--test":
        cmd_test()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
