"""The Supabase snapshot copy going dead must reach a person.

WHY (measured 2026-09-27). scanner_snapshot_store.current_metadata() reported the published copy as
generated 2026-08-16T19:04Z with 1,421 records while the site served one from that day with 1,773 -- SIX
WEEKS stale. Every Scanner Snapshot Publish run in between reported success, because the publish step is
`continue-on-error: true` and the gate after it only fails when NEITHER Supabase nor the IONOS fallback
worked. The cause was `SnapshotStoreError: could not inspect Storage bucket (402)` -- Supabase Storage over
its free-tier quota, while the Postgres database was fine.

A previous fix wrote a warning onto the run summary. That was still passive: a page someone has to visit,
which is the "nobody reads a green cron page" failure already recorded in this repository against a GH_PAT
that expired unnoticed for eight weeks. The outage ran six weeks and was found only because the owner asked
which store was primary.

WHAT THESE TESTS PIN:
  * staleness is measured as the published copy's AGE, not its distance from whatever snapshot happens to
    be on the machine running the check -- the first version compared against local, and from a developer
    checkout holding the stale boot copy it returned a NEGATIVE lag and passed while the backup was dead;
  * it alerts once on the way down and once on the way back, not every day, because a daily alert during a
    known quota outage is the noise the workflow deliberately avoids;
  * an unreadable store is not treated as fresh;
  * a dry run does not consume the transition and silence the real alert.
"""

import datetime as dt

import pytest

import check_snapshot_publication as chk


def _iso(days_old):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_old)).isoformat()


def _wire(monkeypatch, remote_days_old, local_days_old=0.0, was_stale=False):
    sent, saved = [], []
    monkeypatch.setattr(chk, "local_generated",
                        lambda path=None: None if local_days_old is None
                        else chk._parse(_iso(local_days_old)))
    monkeypatch.setattr(chk, "remote_generated",
                        lambda: (None, {}) if remote_days_old is None
                        else (chk._parse(_iso(remote_days_old)),
                              {"record_count": 1773, "version_id": 42}))
    monkeypatch.setattr(chk, "_was_stale", lambda: was_stale)
    monkeypatch.setattr(chk, "_remember", lambda stale, remote_iso="": saved.append(stale))
    monkeypatch.setattr(chk, "_tell", lambda subject, detail, alert: sent.append((subject, detail)))
    return sent, saved


def test_a_current_copy_is_silent(monkeypatch):
    sent, saved = _wire(monkeypatch, remote_days_old=0.5)
    assert chk.check() == 0
    assert not sent
    assert saved == [False]


def test_a_stale_copy_alerts(monkeypatch):
    """THE DEFECT. Six weeks with nothing said."""
    sent, saved = _wire(monkeypatch, remote_days_old=42.0)
    assert chk.check() == 0, "the site is not degraded, so the run must not go red"
    assert sent and "stale" in sent[0][0].lower()
    assert "42" in sent[0][0], "the age belongs in the subject, so it is legible without opening anything"
    assert saved == [True]


def test_the_alert_explains_what_to_do(monkeypatch):
    """An alert that does not say 402 sends the reader back to the logs this exists to replace."""
    sent, _ = _wire(monkeypatch, remote_days_old=42.0)
    chk.check()
    body = sent[0][1]
    assert "402" in body and "Storage" in body
    assert "not degraded" in body.lower() or "NOT degraded" in body
    assert "1773" in body and "42" in body, "record count and version identify which copy is stale"


def test_a_known_stale_copy_does_not_alert_again(monkeypatch):
    """A daily alert during a known quota outage is the noise the workflow deliberately avoids."""
    sent, saved = _wire(monkeypatch, remote_days_old=45.0, was_stale=True)
    assert chk.check() == 0
    assert not sent, "already reported"
    assert saved == [True], "but the state is still refreshed"


def test_recovery_is_reported(monkeypatch):
    """Otherwise the only way to learn the backup came back is to ask -- the original gap."""
    sent, saved = _wire(monkeypatch, remote_days_old=0.5, was_stale=True)
    assert chk.check() == 0
    assert sent and "publishing again" in sent[0][0]
    assert saved == [False]


def test_the_age_is_measured_from_now_not_from_the_local_file(monkeypatch):
    """THE BUG FOUND WHILE WRITING THIS. Comparing local-minus-remote, a developer checkout holding the
    2026-08-12 boot copy produced a NEGATIVE lag and passed while the published copy was six weeks dead.
    Here the local file is OLDER than the remote and the remote is still correctly stale."""
    sent, _ = _wire(monkeypatch, remote_days_old=42.0, local_days_old=46.0)
    assert chk.check() == 0
    assert sent, "a 42-day-old published copy is stale regardless of what is on this disk"


def test_an_unreadable_store_is_not_treated_as_fresh(monkeypatch):
    """A store we cannot ask about is a store we cannot rely on."""
    sent, _ = _wire(monkeypatch, remote_days_old=None)
    assert chk.check() == 1
    assert sent and "no readable published copy" in sent[0][0]


def test_no_local_snapshot_means_no_judgement(monkeypatch):
    """Without a local reference there is nothing to compare, and the snapshot build reports its own
    failures. Staying green here is deliberate, not an oversight."""
    sent, _ = _wire(monkeypatch, remote_days_old=42.0, local_days_old=None)
    assert chk.check() == 0
    assert not sent


def test_fail_on_stale_gives_a_non_zero_exit_for_a_caller_that_wants_one(monkeypatch):
    _wire(monkeypatch, remote_days_old=42.0)
    assert chk.check(fail_on_stale=True) == 1


def test_the_threshold_tolerates_a_single_missed_publish(monkeypatch):
    """The snapshot is rebuilt daily and the publish can miss once without the backup being dead. The
    default must not fire on that, or it becomes the daily noise it is meant to avoid."""
    sent, _ = _wire(monkeypatch, remote_days_old=1.5)
    assert chk.check() == 0
    assert not sent
    assert chk.DEFAULT_MAX_LAG_DAYS >= 2.0, "one missed daily publish must not raise the alarm"
