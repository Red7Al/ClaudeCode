# Comment-width retrofit tool (user directive 2026-06-11): max comment line width is 120 characters.
#   pass 1 (2026-06-11): reflowed wrapped version-history entries ("# <ver>   <date>  <author>   text" groups)
#   pass 2 (2026-06-11): spacer/banner lines widened to 120 — "# ===...", "# ---...", "# ──...",
#                        and "# ── Section title ──────" trailing runs (indented banners keep their indent).
# Only strict patterns are touched — tables, bullets and prose comments are left untouched.
# Re-run any time: idempotent.
import re
import sys
import textwrap
from pathlib import Path

MAX_WIDTH = 120

ENTRY_RE = re.compile(
    r"^(#\s+\d+\.\d+(?:\.\d+)?\s+\d{4}-\d{2}-\d{2}\s+\S+(?:\s\S+)*?\s{2,})(\S.*)$"
)
CONT_RE = re.compile(r"^#(\s{10,})(\S.*)$")

# Pure spacer banners: "# ====", "# ----", "# ────" (optionally indented)
SPACER_RE = re.compile(r"^(\s*)# ?([=\-─])\2{2,}\s*$")
# Titled section banners: "# ── Title text ───────" (optionally indented)
TITLED_RE = re.compile(r"^(\s*# ── .*?\S)\s*(─{3,})\s*$")


def fix_spacers(lines: list) -> tuple[list, int]:
    """Widen spacer/banner comment lines to exactly MAX_WIDTH columns."""
    out, changed = [], 0
    for line in lines:
        m = SPACER_RE.match(line)
        if m:
            indent, ch = m.group(1), m.group(2)
            new = f"{indent}# " + ch * (MAX_WIDTH - len(indent) - 2)
            if new != line:
                changed += 1
            out.append(new)
            continue
        t = TITLED_RE.match(line)
        if t:
            head = t.group(1)
            pad = MAX_WIDTH - len(head) - 1
            new = f"{head} " + "─" * max(pad, 3)
            if new != line:
                changed += 1
            out.append(new)
            continue
        out.append(line)
    return out, changed


def reflow_file(path: Path) -> int:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=False)

    # Header block = leading consecutive '#' lines (allowing a shebang first)
    end = 0
    while end < len(lines) and (lines[end].startswith("#") or lines[end].strip() == ""):
        if lines[end].strip() == "" and end + 1 < len(lines) and not lines[end + 1].startswith("#"):
            break
        end += 1

    out = lines[:0]
    i = 0
    changed = 0
    result = []
    while i < len(lines):
        if i < end:
            m = ENTRY_RE.match(lines[i])
            if m:
                prefix, text = m.group(1), m.group(2)
                text_col = len(prefix)
                parts = [text]
                j = i + 1
                while j < end:
                    cm = CONT_RE.match(lines[j])
                    if cm and not ENTRY_RE.match(lines[j]):
                        parts.append(cm.group(2))
                        j += 1
                    else:
                        break
                merged = " ".join(parts)
                cont_prefix = "#" + " " * (text_col - 1)
                wrapped = textwrap.wrap(
                    merged,
                    width=MAX_WIDTH,
                    initial_indent=prefix,
                    subsequent_indent=cont_prefix,
                    break_long_words=False,
                    break_on_hyphens=False,
                )
                if wrapped != lines[i:j]:
                    changed += 1
                result.extend(wrapped)
                i = j
                continue
        result.append(lines[i])
        i += 1

    result, spacer_changed = fix_spacers(result)
    changed += spacer_changed
    if changed:
        path.write_text("\n".join(result) + "\n", encoding="utf-8")
    return changed


def main():
    total_files = total_entries = 0
    for path in sorted(Path(".").glob("*.py")):
        if path.name == "_reflow_headers.py":
            continue
        n = reflow_file(path)
        if n:
            total_files += 1
            total_entries += n
            print(f"{path.name}: {n} entries reflowed")
    print(f"\nDone: {total_entries} entries across {total_files} files")


if __name__ == "__main__":
    main()
