# TEMP probe — verify correct EIA v2 series IDs against the live API.
# Deleted after the fix is confirmed. Not part of the system.
import os, requests
key = os.environ.get("EIA_API_KEY", "")
print("EIA_API_KEY present:", bool(key))
base = "https://api.eia.gov/v2/seriesid"
candidates = [
    "PET.WCRSTUS1.W",   # crude stocks (known-good control)
    "PET.WPULEUS3.W",   # refinery % utilisation (candidate)
    "WPULEUS3",
    "PET.WPULEUS2.W",   # current (bad)
    "PET.WCRRIUS2.W",   # current (bad)
    "WCRRIUS2",
    "PET.WCRFPUS2.W",   # crude field production (candidate)
    "PET.WGTSTUS1.W",   # gasoline stocks
    "PET.WDISTUS1.W",   # distillate stocks
    "PET.WCESTUS1.W",   # crude stocks excl SPR (candidate)
]
for s in candidates:
    try:
        r = requests.get(f"{base}/{s}", params={"api_key": key, "num": 1, "out": "json"}, timeout=20)
        if r.status_code == 200:
            d = r.json().get("response", {}).get("data", [])
            if d:
                row = d[0]
                print(f"OK    {s:18s} {row.get('period')}={row.get('value')} units={row.get('units','')} | {row.get('series-description', row.get('seriesDescription',''))}")
            else:
                print(f"EMPTY {s:18s} (200 but no data)")
        else:
            print(f"{r.status_code}   {s:18s} {r.text[:90]}")
    except Exception as e:
        print(f"ERR   {s:18s} {e}")
