import { app } from "../../scripts/app.js";
import { viewUrl, triggerDownload } from "./common.js";

app.registerExtension({
  name: "callsheet.concat_preview",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== "CallsheetConcatVideos") return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onNodeCreated?.apply(this, arguments);
      const el = document.createElement("div");
      el.style.cssText =
        "display:flex;flex-direction:column;gap:4px;padding:6px;" +
        "background:#1b1b1b;border-radius:6px;font:11px sans-serif;" +
        "color:#999;";
      el.textContent = "No output yet — queue the workflow.";
      this.csFinalEl = el;
      this.addDOMWidget("final_preview", "cs_final", el,
                        { getMinHeight: () => 280 });
      return r;
    };

    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      const r = onExecuted?.apply(this, arguments);
      const rec = message?.cs_final?.[0];
      if (rec && this.csFinalEl) {
        this.csFinalEl.innerHTML = "";
        if (rec.waiting || rec.problem) {
          const box = document.createElement("div");
          box.textContent = (rec.problem ? "⚠ " : "⏳ ") + rec.text;
          box.style.cssText = rec.problem
            ? "color:#f90;border:1px solid #f90;border-radius:4px;" +
              "padding:6px;background:#402a10;"
            : "color:#e0b050;padding:4px;";
          this.csFinalEl.append(box);
          return r;
        }
        const vid = document.createElement("video");
        vid.controls = true;
        vid.src = viewUrl(rec);
        vid.style.cssText =
          "max-width:100%;max-height:220px;align-self:center;";

        const row = document.createElement("div");
        row.style.cssText = "display:flex;gap:6px;align-items:center;";
        const dl = document.createElement("button");
        dl.textContent = "⬇ download";
        dl.onclick = () => triggerDownload(rec, rec.filename);
        row.append(
          dl,
          Object.assign(document.createElement("span"), {
            textContent: rec.labels?.length
              ? "clips: " + rec.labels.join(" → ") : "",
            style: "flex:1;",
          }),
        );
        this.csFinalEl.append(vid, row);
      }
      return r;
    };
  },
});