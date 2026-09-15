import { app } from "../../scripts/app.js";

export function viewUrl(v) {
  return `/view?filename=${encodeURIComponent(v.filename)}` +
         `&subfolder=${encodeURIComponent(v.subfolder ?? "")}` +
         `&type=${v.type}&rand=${Math.random()}`;
}

export function getWidget(node, name) {
  return node.widgets?.find((w) => w.name === name);
}

export function getSlot(node, name) {
  return node.slots?.find((w) => w.name === name);
}

// Follow the 'parsed' link to the CallsheetTextInput node, so cloned
// text nodes and multiple batches per graph resolve correctly.
export function findInputNode(node) {
  const parsedInput = node.inputs?.find((i) => i.name === "parsed");
  const link = node.graph?.links[parsedInput?.link];
  if (!link) return null;
  if (link.origin_id < 0) {
    alert("subgraphs are not supported");
    return null;
  }
  return app.graph.getNodeById(link.origin_id);
}

export function updateJsonWidget(node, name, fn, def = "{}") {
  const w = getWidget(node, name);
  if (!w) return false;
  let obj = {};
  try {
    const json = w.value || def;
    obj = JSON.parse(json);
  } catch (e) {
    alert('input widget state failed to parse: ' + e);
    throw e;
  }
  fn(obj);
  w.value = JSON.stringify(obj);
  return true;
}

export function triggerDownload(rec, filename) {
  const a = document.createElement("a");
  a.href = viewUrl(rec);
  a.download = filename || rec.filename;   // same-origin: browser honors it
  document.body.appendChild(a);
  a.click();
  a.remove();
}