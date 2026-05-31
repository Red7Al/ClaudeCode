import os
import shutil
import time
from pathlib import Path

SOURCE = Path(os.environ["USERPROFILE"]) / r"OneDrive\ClaudeCode"
DEST = Path(os.environ["USERPROFILE"]) / r"OneDrive\Documents\GitHub\ClaudeCode"

IGNORE_DIRS = {"archive", "venv"}


def should_ignore(path: Path):
    """Return True if path is inside ignored directories."""
    for part in path.parts:
        if part.lower() in IGNORE_DIRS:
            return True
    return False


def sync_python_files():
    print("======================================")
    print("Starting Python file sync")
    print("Ignoring archive and venv directories")
    print("======================================")

    if not SOURCE.exists():
        raise FileNotFoundError(f"Source folder not found: {SOURCE}")

    copied = skipped = errors = 0

    try:
        for file_path in SOURCE.rglob("*.py"):

            # Skip ignored directories
            if should_ignore(file_path):
                continue

            try:
                relative_path = file_path.relative_to(SOURCE)
                dest_file = DEST / relative_path

                dest_file.parent.mkdir(parents=True, exist_ok=True)

                # Copy only if new or not exists
                if (
                    not dest_file.exists()
                    or file_path.stat().st_mtime > dest_file.stat().st_mtime
                ):
                    shutil.copy2(file_path, dest_file)
                    print(f"Copied: {relative_path}")
                    copied += 1
                else:
                    skipped += 1

            except Exception as e:
                print(f"Error copying {file_path}: {e}")
                errors += 1

        print("\n======================================")
        print(f"Copy complete")
        print(f"Files copied : {copied}")
        print(f"Files skipped: {skipped}")
        print(f"Errors       : {errors}")
        print("======================================")

    except Exception as e:
        print(f"Fatal sync error: {e}")


if __name__ == "__main__":
    try:
        sync_python_files()
    except Exception as e:
        print(f"Script failed: {e}")

    print("\nWaiting 5 seconds before exit...")
    time.sleep(5)