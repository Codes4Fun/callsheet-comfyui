import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { viewUrl, getWidget, findInputNode, updateJsonWidget,
         triggerDownload } from "./common.js";

const COLLECTOR = "CallsheetCollector";
const PREFIX = "items_";
const TYPE = "CS_ITEMS";

// ---- dynamic CS_ITEMS sockets ----------------------------------------------
function normalizeInputs(node) {
  if (!node.inputs) node.inputs = [];
  for (let i = node.inputs.length - 1; i >= 0; i--) {
    const inp = node.inputs[i];
    if (inp.name.startsWith(PREFIX) && inp.link == null) node.removeInput(i);
  }
  let n = 1;
  for (const inp of node.inputs)
    if (inp.name.startsWith(PREFIX)) inp.name = PREFIX + n++;
  node.addInput(PREFIX + n, TYPE);
  node.size[1] = Math.max(node.size[1], node.computeSize()[1]);
  node.graph?.setDirtyCanvas(true, true);
}

// ---- refresh without queueing -------------------------------------------------
async function refreshAssets(node, silent) {
  const inputNode = findInputNode(node);
  if (!inputNode) {
    if (!silent) alert("No Callsheet Text Input node found");
    return;
  }
  const val = (n, d) => {
    const w = getWidget(inputNode, n);
    return w == null || w.value == null ? d : w.value;
  };
  const body = {
    text: val("text", ""),
    allowed_pipelines: val("allowed_pipelines", ""),
    strict: !!val("strict", false),
    base_seed: val("base_seed", 0),
    fallback_width: val("fallback_width", 1024),
    fallback_height: val("fallback_height", 1024),
    fallback_fps: val("fallback_fps", 24),
    variation_requests: val("variation_requests", "{}"),
    selections: val("selections", "{}"),
    pipeline_filter: getWidget(node, "pipeline_filter")?.value ?? "",
  };
  try {
    const res = await api.fetchApi("/callsheet/assets", {
      method: "POST", body: JSON.stringify(body) });
    const data = await res.json();
    if (data.error) {
      if (!silent) alert("Callsheet: " + data.error);
      return;
    }
    node.csState.assets = data.assets;
    node.csRender();
  } catch (e) {
    if (!silent) alert("Callsheet refresh failed: " + e);
  }
}

// ---- focus helpers (state lives on the input node) --------------------------
function getFocus(node) {
  const inputNode = findInputNode(node);
  const w = inputNode && getWidget(inputNode, "focus");
  try {
    const f = JSON.parse(w?.value || "[]");
    return Array.isArray(f) ? f : [];
  } catch (e) { return []; }
}

function setFocus(node, list) {
  const inputNode = findInputNode(node);
  const w = inputNode && getWidget(inputNode, "focus");
  if (w) w.value = JSON.stringify(list);
  node.csRender();
}

// ---- state mutations ------------------------------------------------------------
function markPending(node) {
  node.csState.pending = true;
  node.csRender();
}

function requestVariation(node, label, queue) {
  const inputNode = findInputNode(node);
  if (!inputNode) { alert("No Callsheet Text Input node found"); return; }
  updateJsonWidget(inputNode, "variation_requests", (req) => {
    (req[label] = req[label] || []).push(
      Math.floor(Math.random() * 0xffffffff));
  });
  if (queue) app.queuePrompt(0);
  else markPending(node);
}

function selectVariation(node, asset, key) {
  const inputNode = findInputNode(node);
  if (!inputNode) { alert("No Callsheet Text Input node found"); return; }
  updateJsonWidget(inputNode, "selections", (s) => { s[asset.label] = key; });
  asset.selected = key;
  markPending(node);
}

async function clearStore(node, body, label, dropSeed) {
  await api.fetchApi("/callsheet/clear", {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (dropSeed != null) {
    const inputNode = findInputNode(node);
    if (inputNode) {
      updateJsonWidget(inputNode, "variation_requests", (req) => {
        if (req[label]) {
          req[label] = req[label].filter((s) => s !== dropSeed);
          if (!req[label].length) delete req[label];
        }
      });
    }
  }
  markPending(node);
}

// ---- UI -----------------------------------------------------------------------
function buildUI(node) {
  const el = document.createElement("div");
  el.style.cssText =
    "display:flex;flex-direction:column;gap:6px;padding:6px;" +
    "background:#1b1b1b;border-radius:6px;font:12px sans-serif;" +
    "color:#ccc;overflow:auto;";
  node.csState = { assets: [], current: 0, pending: false };

  node.csRender = () => {
    const { assets } = node.csState;
    el.innerHTML = "";

    const mkBtn = (txt, fn, title) => {
      const b = document.createElement("button");
      b.textContent = txt;
      if (title) b.title = title;
      b.onclick = fn;
      return b;
    };

    if (!assets.length) {
      const empty = document.createElement("div");
      empty.style.cssText =
        "display:flex;flex-direction:column;gap:6px;align-items:center;" +
        "padding:12px;";
      empty.append(
        Object.assign(document.createElement("span"), {
          textContent: "No assets loaded.",
        }),
        mkBtn("⟳ refresh", () => refreshAssets(node, false),
              "rebuild the browser from the callsheet without queueing"),
      );
      el.append(empty);
      return;
    }

    const current = Math.min(node.csState.current, assets.length - 1);
    node.csState.current = current;
    const asset = assets[current];
    const focus = getFocus(node);

    // focus banner
    if (focus.length) {
      const bar = document.createElement("div");
      bar.style.cssText =
        "display:flex;gap:6px;align-items:center;padding:2px 6px;" +
        "background:#402a10;border:1px solid #f90;border-radius:4px;" +
        "color:#f90;";
      bar.append(
        Object.assign(document.createElement("span"), {
          textContent: "🎯 focus: " + focus.join(", ") +
                       " (other items on hold)",
          style: "flex:1;",
        }),
        mkBtn("clear", () => setFocus(node, [])),
      );
      el.append(bar);
    }

    // pending banner
    if (node.csState.pending) {
      const bar = document.createElement("div");
      bar.style.cssText =
        "display:flex;gap:6px;align-items:center;padding:2px 6px;" +
        "background:#3a3020;border-radius:4px;color:#e0b050;";
      bar.append(
        Object.assign(document.createElement("span"), {
          textContent: "● pending changes",
          style: "flex:1;",
        }),
        mkBtn("▶ queue", () => app.queuePrompt(0)),
      );
      el.append(bar);
    }

    // navigation row — action buttons stay in place (disabled, not
    // hidden, for injected items) so arrow positions never shift
    const nav = document.createElement("div");
    nav.style.cssText = "display:flex;gap:6px;align-items:center;";

    const refreshBtn = mkBtn("⟳", () => refreshAssets(node, false),
      "rebuild from the callsheet without queueing");
    const focused = focus.includes(asset.label);
    const focusBtn = mkBtn("🎯",
      () => setFocus(node, focused
        ? focus.filter((x) => x !== asset.label)
        : [...focus, asset.label]),
      focused ? "remove from focus"
              : "focus: generate only this asset (and its dependencies)");
    if (focused) focusBtn.style.outline = "1px solid #f90";
    const dlBtn = mkBtn("⬇", () => {
      const v = asset.variations.find((x) => x.key === asset.selected);
      if (v) triggerDownload(v, v.filename);
    }, "download the selected variation");
    dlBtn.disabled = !asset.selected;
    if (!asset.selected) dlBtn.style.opacity = "0.4";

    const varBtn = mkBtn("+ variation",
      (e) => requestVariation(node, asset.label, !e.shiftKey),
      "click: generate now — shift-click: defer to next queue");
    const delSelBtn = mkBtn("🗑 sel", () => {
      const v = asset.variations.find((x) => x.key === asset.selected);
      if (v && confirm(`Delete this variation of ${asset.label}?`)) {
        clearStore(node, { keys: [v.key] }, asset.label, v.seed);
        asset.variations = asset.variations.filter(
          (x) => x.key !== v.key);
        asset.selected = asset.variations.at(-1)?.key ?? null;
        node.csRender();
      }
    });
    const delAssetBtn = mkBtn("🗑 asset", () => {
      if (confirm(`Delete ALL variations of ${asset.label}?`)) {
        clearStore(node, { label: asset.label }, asset.label, null);
        asset.variations = [];
        asset.selected = null;
        node.csRender();
      }
    });
    for (const b of [varBtn, delSelBtn, delAssetBtn, focusBtn]) {
      b.disabled = asset.injected;
      if (asset.injected) {
        b.style.opacity = "0.4";
        b.title = "not available for injected items";
      }
    }

    nav.append(
      mkBtn("◀", () => {
        node.csState.current =
          (current - 1 + assets.length) % assets.length;
        node.csRender();
      }),
      Object.assign(document.createElement("span"), {
        textContent: `${asset.label}  (${current + 1}/${assets.length}` +
                     `${asset.pipeline ? ", " + asset.pipeline : ""}` +
                     `${asset.injected ? ", injected" : ""})`,
        style: "flex:1;text-align:center;",
      }),
      mkBtn("▶", () => {
        node.csState.current = (current + 1) % assets.length;
        node.csRender();
      }),
      refreshBtn, focusBtn, dlBtn, varBtn, delSelBtn, delAssetBtn,
    );
    el.append(nav);

    // main preview
    const sel = asset.variations.find((v) => v.key === asset.selected);
    if (sel) {
      let big;
      if (sel.kind === "video") {
        big = document.createElement("video");
        big.controls = true;
        big.loop = true;
        big.src = viewUrl(sel.preview
          ? { ...sel, filename: sel.preview } : sel);
        if (sel.poster)
          big.poster = viewUrl({ ...sel, filename: sel.poster });
      } else if (sel.kind === "audio") {
        big = document.createElement("div");
        big.style.cssText =
          "display:flex;flex-direction:column;gap:4px;" +
          "align-items:center;";
        if (sel.poster) {
          const waveImg = document.createElement("img");
          waveImg.src = viewUrl({ ...sel, filename: sel.poster });
          waveImg.style.cssText = "max-width:100%;";
          big.append(waveImg);
        }
        const player = document.createElement("audio");
        player.controls = true;
        player.src = viewUrl(sel);
        player.style.cssText = "width:100%;";
        big.append(player);
      } else {
        big = document.createElement("img");
        big.src = viewUrl(sel);
      }
      big.style.maxWidth = "100%";
      big.style.maxHeight = "280px";
      big.style.alignSelf = "center";
      if (sel.kind !== "audio") big.style.objectFit = "contain";
      el.append(big);
    } else {
      el.append(Object.assign(document.createElement("div"),
        { textContent: "Not generated yet." }));
    }

    // variation strip
    const strip = document.createElement("div");
    strip.style.cssText = "display:flex;gap:4px;overflow-x:auto;";
    for (const v of asset.variations) {
      const t = document.createElement("img");
      t.src = viewUrl(v.poster && v.kind !== "image"
                      ? { ...v, filename: v.poster } : v);
      t.title = `seed ${v.seed}` +
                (v.kind !== "image" ? ` (${v.kind})` : "");
      t.style.cssText =
        "height:64px;cursor:pointer;border:2px solid " +
        (v.key === asset.selected ? "#6c6" : "transparent") + ";";
      t.onclick = () => selectVariation(node, asset, v.key);
      strip.append(t);
    }
    el.append(strip);
  };

  node.addDOMWidget("asset_browser", "cs_browser", el,
                    { getMinHeight: () => 420 });
  node.csRender();
}

app.registerExtension({
  name: "callsheet.collector",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== COLLECTOR) return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onNodeCreated?.apply(this, arguments);
      normalizeInputs(this);
      buildUI(this);
      return r;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = onConfigure?.apply(this, arguments);
      normalizeInputs(this);
      // links exist after graph configure completes; populate the
      // browser from the callsheet without queueing anything
      setTimeout(() => refreshAssets(this, true), 250);
      return r;
    };

    const onConnectionsChange = nodeType.prototype.onConnectionsChange;
    nodeType.prototype.onConnectionsChange = function (type) {
      const r = onConnectionsChange?.apply(this, arguments);
      if (type === LiteGraph.INPUT) normalizeInputs(this);
      return r;
    };

    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      const r = onExecuted?.apply(this, arguments);
      if (message?.cs_assets) {
        this.csState.assets = message.cs_assets;
        this.csState.pending = false;   // a run reconciles pending state
        this.csRender();
      }
      return r;
    };
  },
});