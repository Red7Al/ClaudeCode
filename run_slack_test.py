# =============================================================================
# File:         run_slack_test.py
# Author:       Alex Hind
# Created:      2026-06-03
#
# Description:
# -----------------------------------------------------------------------------
# Sends a test message to all four Slack channels and reports which webhooks
# are working. Run after updating webhook URLs in GitHub Secrets.
#
# Version History:
# -----------------------------------------------------------------------------
# 1.0.0   2026-06-03  Alex Hind   Initial build.
# 1.0.1   2026-06-05  Alex Hind   Removed non-existent SLACK_DAILY from webhook
#                                 map — only four channels exist in this system.
# =============================================================================

import os
from dotenv import load_dotenv; load_dotenv(override=True)
import logging
import requests
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("slack_test")

WEBHOOKS = {
    "SLACK_TRADES":  os.environ.get("SLACK_TRADES",  ""),
    "SLACK_SIGNALS": os.environ.get("SLACK_SIGNALS", ""),
    "SLACK_ALERTS":  os.environ.get("SLACK_ALERTS",  ""),
    "SLACK_WEEKLY":  os.environ.get("SLACK_WEEKLY",  ""),
    "SLACK_DAILY":   os.environ.get("SLACK_DAILY",   ""),
}

CHANNEL_NAMES = {
    "SLACK_TRADES":  "#claude-trading-trades",
    "SLACK_SIGNALS": "#claude-trading-signals",
    "SLACK_ALERTS":  "#claude-trading-alerts",
    "SLACK_WEEKLY":  "#claude-trading-weekly",
    "SLACK_DAILY":   "#claude-trading-daily",
}

ts = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

results = {}
for secret_name, url in WEBHOOKS.items():
    channel = CHANNEL_NAMES[secret_name]
    if not url:
        log.warning(f"{secret_name} not set — skipping")
        results[secret_name] = "NOT SET"
        continue
    try:
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"✅ *Webhook test — {channel}*\n"
                        f"Webhook `{secret_name}` is working correctly.\n"
                        f"_{ts}_"
                    )
                }
            }
        ]
        resp = requests.post(url, json={"blocks": blocks}, timeout=10)
        if resp.status_code == 200:
            log.info(f"{secret_name} → {channel}: OK")
            results[secret_name] = "OK"
        else:
            log.error(f"{secret_name} → {channel}: FAILED ({resp.status_code}) {resp.text}")
            results[secret_name] = f"FAILED {resp.status_code}"
    except Exception as e:
        log.error(f"{secret_name} → {channel}: ERROR — {e}")
        results[secret_name] = f"ERROR: {e}"

print("\n=== Slack Webhook Test Results ===")
all_ok = True
for name, result in results.items():
    status = "OK  " if result == "OK" else "FAIL"
    print(f"  {status}  {name:<20}  {CHANNEL_NAMES[name]:<30}  {result}")
    if result != "OK":
        all_ok = False

print()
if all_ok:
    print("All webhooks working. System is fully connected to Slack.")
else:
    print("Some webhooks failed. Update the failing secrets in GitHub and re-run.")

raise SystemExit(0 if all_ok else 1)
