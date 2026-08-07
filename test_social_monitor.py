"""Regressions for instrument-matched tracked X posts."""

import social_monitor


def test_requested_visual_accounts_are_tracked():
    handles = {row["handle"].lower() for row in social_monitor.TRACKED_ACCOUNTS}

    assert "ratedmarkets" in handles
    assert "investingvisual" in handles


def test_existing_pick_keeps_newest_feed_link(monkeypatch):
    monkeypatch.setattr(social_monitor, "TRACKED_ACCOUNTS", [
        {"handle": "ratedmarkets", "name": "Rated Markets", "source": "X/@ratedmarkets"}
    ])
    monkeypatch.setattr(social_monitor, "fetch_rss", lambda *args, **kwargs: [
        {"title": "$NVDA newest", "content": "", "url": "https://x.com/ratedmarkets/status/222"},
        {"title": "$NVDA older", "content": "", "url": "https://x.com/ratedmarkets/status/111"},
    ])
    monkeypatch.setattr(social_monitor, "is_new_ticker", lambda *args: False)
    refreshed = []
    monkeypatch.setattr(social_monitor, "refresh_pick_link", lambda *args: refreshed.append(args))

    assert social_monitor.scan_social_feeds() == []
    assert refreshed == [("NVDA", "Rated Markets", "X/@ratedmarkets",
                          "https://x.com/ratedmarkets/status/222")]
