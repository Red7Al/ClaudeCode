"""app_config is read once per web request, never cached across requests (2026-10-09).

MEASURED: one Scanner page load issued 20 single-key app_config lookups. A cross-request cache would let an
exec kill switch go stale, so the memo lives on flask.g for exactly one request."""
import flask
import config_store


class _Db:
    def __init__(self, table):
        self.table, self.calls = table, []

    def run(self, sql, **k):
        self.calls.append(" ".join(sql.split()))
        if "select key, value from app_config" in sql:
            return list(self.table.items())
        if "select value from app_config where key" in sql:
            return [[self.table[k["k"]]]] if k["k"] in self.table else []
        return []

    def close(self):
        pass


def _patch(monkeypatch, db):
    monkeypatch.setattr("db_pool.get_db", lambda: db)
    monkeypatch.setattr(config_store, "_ensure", lambda d: None)


def test_many_keys_in_one_request_are_one_query(monkeypatch):
    db = _Db({"a": "1", "b": "2", "exec_WEB_BRIDGE": "true"})
    _patch(monkeypatch, db)
    with flask.Flask(__name__).test_request_context("/"):
        assert [config_store.get_value(k) for k in ("a", "b", "exec_WEB_BRIDGE", "missing")] == ["1", "2", "true", ""]
    assert len(db.calls) == 1


def test_the_next_request_reads_again(monkeypatch):
    """A kill switch flipped between two requests must apply on the second."""
    db = _Db({"exec_WEB_BRIDGE": "true"})
    _patch(monkeypatch, db)
    app = flask.Flask(__name__)
    with app.test_request_context("/"):
        assert config_store.get_value("exec_WEB_BRIDGE") == "true"
    db.table["exec_WEB_BRIDGE"] = "false"
    with app.test_request_context("/"):
        assert config_store.get_value("exec_WEB_BRIDGE") == "false"


def test_outside_a_request_every_read_goes_to_the_database(monkeypatch):
    """GitHub Actions jobs and the Order Bridge are unchanged."""
    db = _Db({"a": "1"})
    _patch(monkeypatch, db)
    config_store.get_value("a"); config_store.get_value("a")
    assert len(db.calls) == 2 and all("where key" in c for c in db.calls)


def test_a_write_is_seen_by_a_read_later_in_the_same_request(monkeypatch):
    db = _Db({"a": "1"})
    _patch(monkeypatch, db)
    with flask.Flask(__name__).test_request_context("/"):
        assert config_store.get_value("a") == "1"
        assert config_store.set_value("a", "9")
        assert config_store.get_value("a") == "9"
