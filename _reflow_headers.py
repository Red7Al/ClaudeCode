# One-off retrofit (2026-06-11): reflow wrapped version-history entries in the
# header comment block of every .py file to a 120-character max line width.
# Only touches groups that match the strict "# <ver>   <date>  <author>   text"
# pattern plus their deep-indent continuation lines — tables, bullets and
# section breaks are left untouched.
import re
import sys
import textwrap
from pathlib import Path

MAX_WIDTH = 120

ENTRY_RE = re.compile(
    r"^(#\s+\d+\.\d+(?:\.\d+)?\s+\d{4}-\d{2}-\d{2}\s+\S+(?:\s\S+)*?\s{2,})(\S.*)$"
)
CONT_RE = re.compile(r"^#(\s{10,})(\S.*)$")


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
