import { app } from "../../scripts/app.js";
import { getWidget } from "./common.js";

app.registerExtension({
  name: "callsheet.embedded_image",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== "CallsheetEmbeddedImage") return;
    const SOFT_LIMIT = 512 * 1024;
    const HARD_LIMIT = 2 * 1024 * 1024;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onNodeCreated?.apply(this, arguments);
      const node = this;
      const el = document.createElement("div");
      el.style.cssText =
        "display:flex;flex-direction:column;align-items:center;gap:4px;" +
        "padding:8px;border:2px dashed #555;border-radius:6px;" +
        "font:11px sans-serif;color:#999;min-height:80px;" +
        "justify-content:center;cursor:pointer;";

      const render = () => {
        const w = getWidget(node, "image_data");
        el.innerHTML = "";
        if (w?.value) {
          const img = document.createElement("img");
          img.src = w.value;
          img.style.cssText = "max-width:100%;max-height:160px;";
          el.append(img, Object.assign(document.createElement("div"), {
            textContent:
              `embedded (${Math.round(w.value.length * 0.75 / 1024)} KB)`,
          }));
        } else {
          el.textContent = "Drop an image here (or click)";
        }
      };

      const setData = (dataUrl) => {
        getWidget(node, "image_data").value = dataUrl;
        render();
      };

      const ingest = (file) => {
        const reader = new FileReader();
        reader.onload = () => {
          if (reader.result.length * 0.75 <= SOFT_LIMIT)
            return setData(reader.result);
          const img = new Image();
          img.onload = () => {
            const canvas = document.createElement("canvas");
            canvas.width = img.width;
            canvas.height = img.height;
            canvas.getContext("2d").drawImage(img, 0, 0);
            const webp = canvas.toDataURL("image/webp", 0.85);
            if (webp.length * 0.75 > HARD_LIMIT) {
              alert("Image too large to embed even after compression — " +
                    "use a Load Image node instead.");
              return;
            }
            setData(webp);
          };
          img.src = reader.result;
        };
        reader.readAsDataURL(file);
      };

      el.addEventListener("dragover", (e) => {
        e.preventDefault();
        el.style.borderColor = "#6c6";
      });
      el.addEventListener("dragleave",
                          () => (el.style.borderColor = "#555"));
      el.addEventListener("drop", (e) => {
        e.preventDefault();
        e.stopPropagation();
        el.style.borderColor = "#555";
        const file = e.dataTransfer.files?.[0];
        if (file?.type.startsWith("image/")) ingest(file);
      });
      el.addEventListener("click", () => {
        const inp = document.createElement("input");
        inp.type = "file";
        inp.accept = "image/*";
        inp.onchange = () => inp.files?.[0] && ingest(inp.files[0]);
        inp.click();
      });

      node.addDOMWidget("dropzone", "cs_drop", el,
                        { getMinHeight: () => 120 });
      requestAnimationFrame(render);
      return r;
    };
  },
});