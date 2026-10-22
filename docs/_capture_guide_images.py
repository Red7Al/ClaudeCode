"""Screenshots for the in-app guides (owner 2026-10-22: "Add screen shots to documentation where it can be helpful
e.g. Adding IG credentials"), taken from the web app's REAL page code served by docs/_guide_stub_server.py with
placeholder data -- so they show the actual screens and nothing personal. Headless Chrome over its DevTools
protocol; one PNG per shot into docs/guide_images/, which docs/_build_guides.js embeds.

    ./.venv/Scripts/python.exe docs/_guide_stub_server.py 5071      # in another shell
    ./.venv/Scripts/python.exe docs/_capture_guide_images.py [http://127.0.0.1:5071]
    NODE_PATH=$(npm root -g) node docs/_build_guides.js

Re-run after a change to a screen a guide shows.
"""
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import websockets

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "guide_images")
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5071"
PORT = 9339
CHROME = next(p for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                          r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                          shutil.which("google-chrome") or "", shutil.which("chromium") or "") if p and os.path.exists(p))

_PROBE = ("(()=>{const d=document.createElement('div');d.id='guide-warn-probe';"
          "d.style.cssText='max-width:820px;margin:20px';document.querySelector('#view-config').prepend(d);})();")

# (file, steps before the shot, CSS selector to crop to). A step is the stub's state (a dict) or JavaScript run
# in the page, in order.
SHOTS = [
    ("config-nav.png", [{'ig_set': False}, "showTab('config')"], "#confnav"),
    ("ig-credentials-empty.png", [{'ig_set': False}, "openIgSettings()"],
     '#view-config .confpanel[data-panel="IG"]'),
    ("ig-credentials-saved.png", [{'ig_set': True}, "openIgSettings()"],
     '#view-config .confpanel[data-panel="IG"]'),
    # The warning renders into an element on the IG pages; the shot gives it one of its own.
    ("ig-no-credentials-warning.png", [{'ig_set': False}, "showTab('config');" + _PROBE + "renderIgCredWarn('guide-warn-probe')"],
     "#guide-warn-probe"),
    ("tab-visibility.png", [{'ig_set': False}, "showTab('config');confShow('tabvis')"],
     '#view-config .confpanel[data-panel="tabvis"]'),
    ("top-navigation.png", [{'ig_set': False}, "showTab('config')"], "nav.tabs, #tabs, .tabs"),
]


class Page:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    async def send(self, method, **params):
        self.n += 1
        mid = self.n
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def js(self, expr):
        r = await self.send("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(f"{expr}: {r['exceptionDetails'].get('exception', {}).get('description')}")
        return r.get("result", {}).get("value")


async def shoot(page, name, steps, selector):
    await page.send("Page.navigate", url=BASE + "/")
    await asyncio.sleep(3)
    for st in steps:
        if isinstance(st, dict):   # the stub's state for this shot, set before the page asks for it
            await page.js(f"fetch('/stub/state',{{method:'POST',headers:{{'Content-Type':'application/json'}},"
                          f"body:{json.dumps(json.dumps(st))}}}).then(r=>r.ok)")
            continue
        await page.js(st)
        await asyncio.sleep(2.5)
    rect = await page.js(f"""(()=>{{const e=[...document.querySelectorAll({json.dumps(selector)})]
        .find(x=>x.offsetParent!==null); if(!e) return null; e.scrollIntoView({{block:'start'}});
        const r=e.getBoundingClientRect(); return {{x:r.left+scrollX,y:r.top+scrollY,w:r.width,h:r.height}};}})()""")
    if not rect or rect["w"] < 10 or rect["h"] < 10:
        raise RuntimeError(f"{name}: {selector} is not on screen")
    pad = 8
    shot = await page.send("Page.captureScreenshot", format="png", captureBeyondViewport=True,
                           clip={"x": max(0, rect["x"] - pad), "y": max(0, rect["y"] - pad),
                                 "width": rect["w"] + 2 * pad, "height": rect["h"] + 2 * pad, "scale": 1})
    with open(os.path.join(OUT, name), "wb") as fh:
        fh.write(base64.b64decode(shot["data"]))
    print(f"WROTE guide_images/{name}  {int(rect['w'])}x{int(rect['h'])}")


async def main():
    os.makedirs(OUT, exist_ok=True)
    prof = tempfile.mkdtemp(prefix="guide-chrome-")
    proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={prof}",
                             "--window-size=1600,1000", "--hide-scrollbars", "--no-first-run", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(150):   # up to 30 s for Chrome to listen
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/json/new?about:blank", method="PUT")
        target = json.loads(urllib.request.urlopen(req, timeout=5).read())
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=50_000_000) as ws:
            page = Page(ws)
            await page.send("Page.enable")
            await page.send("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1,
                            mobile=False)
            # Signed in as the stub's placeholder user, in the light theme the guides are read against.
            await page.send("Page.addScriptToEvaluateOnNewDocument",
                            source="localStorage.setItem('sq_auth','guide-demo');")
            failed = []
            for name, steps, selector in SHOTS:
                try:
                    await shoot(page, name, steps, selector)
                except Exception as ex:
                    failed.append(name)
                    print(f"FAILED {name}: {ex}")
            if failed:
                sys.exit(1)
    finally:
        proc.terminate()
        shutil.rmtree(prof, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
