#!/usr/bin/env python
"""One reading of "when does this cron fire", for everything that needs to know.

WHY THIS IS A SEPARATE MODULE. Two places already interpret a cron expression and a third wanted to:
`setup_cronjobs._cron_to_schedule` expands the fields into cron-job.org's integer arrays,
`hvf_web.scheduled_jobs._cron_human` renders a cadence for the admin page, and the scheduled-job watcher
needed the firing INTERVAL. That is one fact -- when a cron fires -- with three consumers, which is the
defect CLAUDE.md records against this repository ("one fact, several pieces of code each deciding what it
means"). The expander lives here once.

IT HAD TO BE A NEW FILE RATHER THAN AN IMPORT OF setup_cronjobs. MEASURED 2026-09-25,
setup_cronjobs.py:116-118 raises SystemExit at module scope when CRONJOB_API_KEY is unset -- which is why
`scheduled_jobs._load_jobs` reads the JOBS registry by AST literal-eval instead of importing it. Anything
the web app or the watcher imports must be free of import-time side effects. This file imports only the
standard library and does nothing at import.

WHY max_gap_days EXISTS. The watcher flags a job that has "stopped running" by age, and used one flat
threshold of 3 days for every job. MEASURED 2026-09-25: 19 of the 38 registered jobs have a normal gap
between runs of 3 days or more, so half the registry could be reported stale while perfectly healthy --
and five weekly Sunday jobs were, every Thursday through Saturday, each false alarm sending an email and
turning the watcher's own run red. A weekly job is stale at 3 days by construction. The threshold has to
come from the job's own schedule.
"""

# How far to walk when measuring a gap. Nine weeks: enough for any weekly or weekday pattern with room to
# spare, and enough that a once-a-month schedule still fires twice and gets measured rather than refused.
_WINDOW_DAYS = 63
_REFERENCE = (2026, 1, 1)       # a Thursday, so the window cannot flatter a Monday-anchored schedule

# A job must miss its whole interval AND half of another before age is the better explanation. One
# skipped run is ordinary. On a weekly job this is 10.5 days; on a Mon-Fri daily job, 4.5.
_GRACE = 1.5
# Without a floor, the hourly watcher ('5 * * * *', gap 1 hour) would be called stale after 90 minutes
# and alert on one delayed run. Twelve hours of silence from an hourly job is an outage; one late run is not.
_FLOOR_DAYS = 0.5
# Used only when the schedule cannot be read. It is the old flat threshold, so an unreadable expression
# keeps exactly the behaviour it had rather than going quiet.
_FALLBACK_DAYS = 3.0


def expand_field(field: str, lo: int, hi: int) -> list:
    """Every integer a single cron field matches, sorted. '*' expands to the full lo..hi range.

    Supports '*', single values, comma lists, ranges (a-b) and steps (*/n, a-b/n). Raises on anything
    else -- a caller that cannot read a schedule must find out, not receive an empty set that reads as
    "never fires".

    NOTE FOR cron-job.org's API, which wants [-1] rather than a full range for "every": that sentinel is
    its own concern and it applies it itself. This function speaks cron.
    """
    if field == "*":
        return list(range(lo, hi + 1))
    vals = set()
    for part in field.split(","):
        step, base = 1, part
        if "/" in part:
            base, s = part.split("/")
            step = int(s)
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, b = base.split("-")
            start, end = int(a), int(b)
        else:
            start = end = int(base)
        vals.update(range(start, end + 1, step))
    return sorted(vals)


def _fires_on(cron: str):
    """A predicate over datetime.date: does this cron fire on that day at all?"""
    _minute, _hour, mday_f, month_f, wday_f = cron.split()
    mdays, months = set(expand_field(mday_f, 1, 31)), set(expand_field(month_f, 1, 12))
    # cron numbers Sunday 0 and also accepts 7 for it; date.weekday() numbers Monday 0.
    wdays = {0 if w == 7 else w for w in expand_field(wday_f, 0, 7)}
    mday_every, wday_every = mday_f == "*", wday_f == "*"

    def matches(d) -> bool:
        if d.month not in months:
            return False
        if mday_every and wday_every:
            return True
        if mday_every:
            return ((d.weekday() + 1) % 7) in wdays
        if wday_every:
            return d.day in mdays
        # POSIX: with BOTH restricted, a day matching EITHER fires. No registry entry does this
        # (measured 2026-09-25); it is here so adding one cannot silently mis-measure.
        return d.day in mdays or ((d.weekday() + 1) % 7) in wdays

    return matches


def max_gap_days(cron: str):
    """The longest stretch, in days, between two consecutive firings of this cron.

    None means "cannot be read" -- the caller must fall back to something rather than treat an
    unreadable schedule as fresh or as stale.

    Measured by walking days rather than reasoning about the fields: the answer for '0 9 * * 1,5'
    (Mon and Fri) is the Monday-to-Friday gap of 4 days, not the Friday-to-Monday 3, and getting that
    backwards is the bug this exists to fix. Window-edge gaps are excluded as artefacts of the start date.
    """
    try:
        import datetime as _dt
        minute, hour, _md, _mo, _wd = cron.split()
        times = sorted(h * 60 + m for h in expand_field(hour, 0, 23)
                       for m in expand_field(minute, 0, 59))
        if not times:
            return None
        fires = _fires_on(cron)
        start = _dt.date(*_REFERENCE)
        days = [d for d in (start + _dt.timedelta(days=i) for i in range(_WINDOW_DAYS)) if fires(d)]
        if len(days) < 2:
            return None                      # fires less than twice a quarter; age cannot judge it

        # Within a firing day the gaps are the differences inside `times`; across a day boundary it is
        # the tail of one day plus the head of the next, plus any whole days between.
        worst = max((b - a) for a, b in zip(times, times[1:])) if len(times) > 1 else 0
        for prev, nxt in zip(days, days[1:]):
            worst = max(worst, (nxt - prev).days * 1440 - times[-1] + times[0])
        return worst / 1440.0
    except Exception:
        return None


def stale_after_days(cron: str) -> float:
    """How old this job's last run may get before "it has stopped running" is the better explanation."""
    gap = max_gap_days(cron or "")
    return _FALLBACK_DAYS if gap is None else max(gap * _GRACE, _FLOOR_DAYS)
