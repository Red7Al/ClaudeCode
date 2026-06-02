"""
Install the pre-commit secret scanning hook.
Run once: python setup_hooks.py

Requires: pip install pre-commit
Also requires gitleaks binary on PATH.
  Windows: winget install gitleaks  (or download from github.com/gitleaks/gitleaks/releases)
  Mac:     brew install gitleaks
"""
import subprocess, sys, shutil

def run(cmd):
    print(f"  > {cmd}")
    r = subprocess.run(cmd, shell=True)
    return r.returncode == 0

print("=== EndToEndTrading — Secret Scan Hook Setup ===\n")

# Check pre-commit is installed
if not shutil.which("pre-commit"):
    print("Installing pre-commit...")
    if not run("pip install pre-commit"):
        print("ERROR: failed to install pre-commit"); sys.exit(1)

# Check gitleaks is available
if not shutil.which("gitleaks"):
    print("\nWARNING: gitleaks binary not found on PATH.")
    print("Install it:")
    print("  Windows: winget install gitleaks")
    print("  Mac:     brew install gitleaks")
    print("  Linux:   https://github.com/gitleaks/gitleaks/releases\n")
    print("The GitHub Actions scan will still protect the remote repo.")
    print("Local hook will not be active until gitleaks is installed.\n")

# Install the hook
print("Installing pre-commit hook...")
if run("pre-commit install"):
    print("\nDone. The hook will scan staged files on every git commit.")
    print("To run manually: pre-commit run --all-files")
else:
    print("ERROR: pre-commit install failed")
    sys.exit(1)
