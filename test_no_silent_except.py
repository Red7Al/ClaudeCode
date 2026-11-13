"""Ratchet (owner 2026-11-13: every error 100% clear). A broad `except Exception` that neither uses the error, logs it
nor re-raises hides the cause (AAPL was reported 'no IG epic' for a failed step). The count may only fall."""
import ast
import pathlib

BASELINE = {"ig_shim.py": 34, "notify.py": 4, "intraday_signals.py": 22}   # measured 2026-11-13; lower it as handlers are fixed


def silent_handlers(path):
    src = pathlib.Path(path).read_text(encoding="utf-8")
    out = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.ExceptHandler) and (n.type is None or getattr(n.type, "id", "") in ("Exception", "BaseException")):
            body = ast.Module(n.body, [])
            names = {x.id for x in ast.walk(body) if isinstance(x, ast.Name)}
            attrs = {x.attr for x in ast.walk(body) if isinstance(x, ast.Attribute)}
            if not ((n.name and n.name in names) or any(isinstance(x, ast.Raise) for x in ast.walk(body)) or "exception" in attrs):
                out.append(n.lineno)
    return out


def test_silent_handler_count_never_rises():
    root = pathlib.Path(__file__).parent
    for f, base in BASELINE.items():
        found = silent_handlers(root / f)
        assert len(found) <= base, f"{f}: {len(found)} silent broad handlers (baseline {base}); new at lines {found}. Log or use the error."
