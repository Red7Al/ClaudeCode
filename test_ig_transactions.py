"""IG transaction-history normalization regressions."""

import ig_shim


class _PagedSession:
    def __init__(self):
        self.pages = []

    def get(self, path, version, params):
        self.pages.append(dict(params))
        page = params["pageNumber"]
        return {
            "transactions": [{
                "date": "31/07/26", "dateUtc": f"2026-07-3{page}T14:30:00",
                "instrumentName": f"Trade {page}", "transactionType": "POSITION_CLOSED",
                "profitAndLoss": "£10.00", "openLevel": "100", "closeLevel": "110", "size": "1",
            }],
            "metadata": {"pageData": {"pageNumber": page, "totalPages": 2}},
        }


def test_transactions_use_iso_close_date_and_fetch_every_page(monkeypatch):
    fake = _PagedSession()
    monkeypatch.setattr(ig_shim, "session", fake)

    rows = ig_shim.get_transactions("2026-07-01T00:00:00")

    assert [row["date"] for row in rows] == ["2026-07-31T14:30:00", "2026-07-32T14:30:00"]
    assert all(row["kind"] == "TRADE" for row in rows)
    assert [page["pageNumber"] for page in fake.pages] == [1, 2]
