# =============================================================================
# File:         db_pool.py
# Author:       Alex Hind
# Created:      2026-06-10
#
# Description:
# -----------------------------------------------------------------------------
# Single source of truth for Supabase connections. Every script connects through
# get_db() here — never inline pg8000 Connection() calls.
#
# Why the SESSION pooler (port 5432), never 6543:
#   The 6543 TRANSACTION pooler is incompatible with pg8000's extended protocol —
#   unnamed prepared statements collide across queries on a shared backend, raising
#   26000 / 22007 / 08P01 (crashed social_monitor, watchdog and run_daily_report on
#   2026-06-09). The 5432 SESSION pooler gives a dedicated backend per connection,
#   so statements never collide.
#
# Why retry:
#   The session pooler has a LOWER connection limit than 6543 and can transiently
#   time out under the aggressive */5 monitor schedule (watchdog crashed 2026-06-10
#   07:50 with "TimeoutError [Errno 110] Connection timed out … port 5432"). So set
#   an explicit connect timeout and retry with backoff — a brief pool-exhaustion
#   spike self-recovers instead of failing the job.
#
# Environment Variables Required:
#   SUPABASE_USER, SUPABASE_DB_PASSWORD
#
# Version History:
# -----------------------------------------------------------------------------
# 1.0.0   2026-06-10  Alex Hind   Initial build — resilient session-pooler connect
#                                 (timeout + retry/backoff), extracted from the
#                                 watchdog fix and shared across the whole codebase.
# =============================================================================

import os
from dotenv import load_dotenv; load_dotenv(override=True)
import time
import logging

import pg8000.native

log = logging.getLogger("db_pool")

SUPABASE_HOST       = "aws-0-eu-west-1.pooler.supabase.com"
SESSION_POOLER_PORT = 5432   # session pooler — NEVER 6543 (pg8000 statement collisions)


def get_db(timeout: int = 15, attempts: int = 3):
    """
    Connect to Supabase via the SESSION pooler (5432) with an explicit timeout and
    retry/backoff. Raises the last exception only after all attempts are exhausted.
    """
    last = None
    for i in range(attempts):
        try:
            return pg8000.native.Connection(
                host=SUPABASE_HOST, port=SESSION_POOLER_PORT, database="postgres",
                user=os.environ["SUPABASE_USER"],
                password=os.environ["SUPABASE_DB_PASSWORD"],
                ssl_context=True, timeout=timeout,
            )
        except Exception as e:
            last = e
            log.warning(f"DB connect attempt {i + 1}/{attempts} failed: {e}")
            if i < attempts - 1:
                time.sleep(3 * (i + 1))   # 3s, 6s — let a session-pool slot free up
    raise last
