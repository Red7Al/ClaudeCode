import fs from "node:fs";

const auth = process.env.SQ_AUTH || "";
const cdpPort = process.env.CDP_PORT || "9222";
const appUrl = process.env.APP_URL || "http://127.0.0.1:5057/";
if (!auth) throw new Error("SQ_AUTH is required");

const target = await fetch(`http://127.0.0.1:${cdpPort}/json/new?${encodeURIComponent(appUrl)}`, {method: "PUT"}).then(r => r.json());
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  ws.addEventListener("open", resolve, {once: true});
  ws.addEventListener("error", reject, {once: true});
});

let nextId = 1;
const waiting = new Map();
ws.addEventListener("message", event => {
  const message = JSON.parse(event.data);
  if (!message.id || !waiting.has(message.id)) return;
  const {resolve, reject} = waiting.get(message.id);
  waiting.delete(message.id);
  if (message.error) reject(new Error(message.error.message));
  else resolve(message.result || {});
});
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = nextId++;
  waiting.set(id, {resolve, reject});
  ws.send(JSON.stringify({id, method, params}));
});
const evaluate = async expression => {
  const result = await send("Runtime.evaluate", {expression, awaitPromise: true, returnByValue: true});
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "browser evaluation failed");
  return result.result?.value;
};

await send("Page.enable");
await send("Runtime.enable");
await send("Page.addScriptToEvaluateOnNewDocument", {
  source: `localStorage.setItem("sq_auth", ${JSON.stringify(auth)});`,
});
await send("Page.navigate", {url: appUrl});

const deadline = Date.now() + 120_000;
let result;
while (Date.now() < deadline) {
  await new Promise(resolve => setTimeout(resolve, 1000));
  result = await evaluate(`(() => {
    if (typeof showTab !== "function" || typeof pfPanel !== "function") return {ready:false, reason:"scripts"};
    showTab("performance"); pfPanel("settings");
    const panel=document.getElementById("pf-panel-settings");
    const cards=[...document.querySelectorAll("#ordp-bestcombo > .fgrid .fcard")];
    const labels=cards.map(c=>(c.querySelector("h3")||{}).textContent?.trim()||"");
    const settings=cards.map(c=>[...c.querySelectorAll(".body > div")].map(x=>x.textContent.trim()).join(" | "));
    const text=(document.getElementById("ordp-bestcombo")||{}).textContent||"";
    return {ready:cards.length>=3, labels, settings, distinct:new Set(settings).size, text:text.trim().slice(0,500),
      panelVisible:panel&&!panel.classList.contains("hidden")};
  })()`);
  if (result?.ready) break;
  if (result?.text?.includes("could not be loaded")) throw new Error(result.text);
}
if (!result?.ready) throw new Error(`recommendations did not render: ${JSON.stringify(result)}`);
if (!result.panelVisible) throw new Error("Best Settings panel is not visible");
if (!result.labels.includes("Balanced") || !result.labels.includes("Growth") || !result.labels.includes("Defensive")) {
  throw new Error(`missing decision options: ${result.labels.join(", ")}`);
}
if (result.distinct !== result.settings.length) throw new Error("recommendation configurations are duplicated");

await send("Emulation.setDeviceMetricsOverride", {width: 1440, height: 1100, deviceScaleFactor: 1, mobile: false});
const shot = await send("Page.captureScreenshot", {format: "png", captureBeyondViewport: false});
fs.writeFileSync("tests_fixtures/performance-best-settings.png", Buffer.from(shot.data, "base64"));
console.log(JSON.stringify({labels: result.labels, distinct: result.distinct, screenshot: "tests_fixtures/performance-best-settings.png"}));
ws.close();
