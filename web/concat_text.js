import { app } from "../../scripts/app.js";

const NODE_NAME = "CallsheetConcatText";
const PREFIX = "text";
const TYPE = "STRING";

function isDynamicInput(input) {
    return input.name.startsWith(PREFIX + "_");
}

function normalizeInputs(node) {
    // 1. Remove all unconnected dynamic inputs (iterate backwards so
    //    removal doesn't shift indices we haven't visited yet).
    for (let i = node.inputs.length - 1; i >= 0; i--) {
        const input = node.inputs[i];
        if (isDynamicInput(input) && input.link == null) {
            node.removeInput(i);
        }
    }

    // 2. Rename surviving dynamic inputs sequentially: text_1, text_2, ...
    let n = 1;
    for (const input of node.inputs) {
        if (isDynamicInput(input)) {
            input.name = `${PREFIX}_${n++}`;
        }
    }

    // 3. Always leave exactly one empty trailing socket to connect to.
    node.addInput(`${PREFIX}_${n}`, TYPE);

    // Keep the user's width, recompute height.
    const width = node.size[0];
    node.setSize([width, node.computeSize()[1]]);
}

app.registerExtension({
    name: "callsheet.concat_text",
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name !== NODE_NAME) return;

        const onConnectionsChange = nodeType.prototype.onConnectionsChange;
        nodeType.prototype.onConnectionsChange = function (type, index, connected, linkInfo) {
            const r = onConnectionsChange?.apply(this, arguments);
            if (type === LiteGraph.INPUT) {
                normalizeInputs(this);
            }
            return r;
        };

        // Normalize after a workflow loads, in case the saved state is stale.
        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const r = onConfigure?.apply(this, arguments);
            normalizeInputs(this);
            return r;
        };
    },
});