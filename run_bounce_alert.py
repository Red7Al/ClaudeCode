# ======================================================================================================================
# File:         run_bounce_alert.py
# Author:       Alex Hind
# Created:      2026-06-26
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Scheduled entrypoint for backlog E — checks the shared IG account for a bounce in any instrument SOLD in the last
# BOUNCE_LOOKBACK_HOURS and fires one URGENT email per sell per bounce. Triggered by the trading-bounce-alert workflow
# (workflow_dispatch + external cron-job.org schedule, same model as the other run_*.py jobs).
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-06-26  Alex Hind   Initial build (backlog E).
# ======================================================================================================================

import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("run_bounce_alert")


def main():
    from bounce_monitor import check_bounces
    alerted = check_bounces()
    if alerted:
        log.info(f"bounce alerts fired for: {', '.join(s.epic for s in alerted)}")
    else:
        log.info("no bounce alerts this run")


if __name__ == "__main__":
    main()
