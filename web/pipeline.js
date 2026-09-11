import { app } from "../../scripts/app.js";

function toggleSlots(node, display) {
    const nodeElement = document.querySelector(`[data-node-id="${node.id}"]`);
    if (!nodeElement) return false;

    // map slot name to element
    const inputSlots = {};
    nodeElement.querySelectorAll('.lg-slot--input')
        .forEach((e) => {inputSlots[e.textContent] = e});
    const outputSlots = {};
    nodeElement.querySelectorAll('.lg-slot--output')
        .forEach((e) => {outputSlots[e.textContent] = e});
    
    node.inputs.forEach((slot) => {
        if (slot.widget) return;
        const e = inputSlots[slot.name];
        if (!e) {
            console.log('failed to find: '+slot.name);
            return;
        }
        if (!display && slot.link == null) {
            e.style.display = 'none';
            return;
        }
        e.style.display = '';
    });
    node.outputs.forEach((slot) => {
        const e = outputSlots[slot.name];
        if (!e) {
            console.log('failed to find: '+slot.name);
            return;
        }
        if (!display && !slot.links?.length) {
            e.style.display = 'none';
            return;
        }
        e.style.display = '';
    });

    if (!display) {
        nodeElement.style.setProperty('--node-height', '0');
    }

    return true;
}

app.registerExtension({
    name: "callsheet.pipeline",

    nodeCreated(node) {
        // Limit to your node type(s); remove this check to apply everywhere
        if (node.comfyClass !== "CallsheetPipeline") return;

        // Serialized automatically with the workflow
        if (node.properties.hideUnlinkedSlots === undefined) {
            node.properties.hideUnlinkedSlots = false;
        }

        // --- make LiteGraph's size math aware of hidden slots ---
        const origComputeSize = node.computeSize;
        node.computeSize = function (out) {
            const size = origComputeSize.call(this, out);
            if (this.properties?.hideUnlinkedSlots) {
                // input/output slots share rows side by side, so height is
                // driven by max(inputs, outputs)
                const fullRows = Math.max(
                    this.inputs.filter((s) => !s.widget).length,
                    this.outputs.length
                );
                const visibleRows = Math.max(
                    this.inputs.filter((s) => !s.widget && s.link != null).length,
                    this.outputs.filter((s) => s.links?.length).length
                );
                size[1] -= (fullRows - visibleRows) * LiteGraph.NODE_SLOT_HEIGHT;
            }
            if (out) { out[0] = size[0]; out[1] = size[1]; }
            return size;
        };

        const applyState = (attempts = 20) => {
            const hidden = !!node.properties.hideUnlinkedSlots;
            if (toggleSlots(node, !hidden) === false && attempts > 0) {
                requestAnimationFrame(() => applyState(attempts - 1));
                return;
            }
            btn.name = hidden ? "Show unlinked slots" : "Hide unlinked slots";
            // Resize to the new logical height. This is also what triggers the
            // frontend's slot re-measure, so link endpoints snap into place.
            node.setSize([node.size[0], node.computeSize()[1]]);
            node.setDirtyCanvas(true, true);
        };

        const btn = node.addWidget("button", "Hide unlinked slots", null, () => {
            node.properties.hideUnlinkedSlots = !node.properties.hideUnlinkedSlots;
            applyState();
        });
        btn.serialize = false;

        // Re-apply when links change so a newly connected slot becomes visible
        // (and a freshly disconnected one hides again)
        const origConnChange = node.onConnectionsChange;
        node.onConnectionsChange = function (...args) {
            origConnChange?.apply(this, args);
            requestAnimationFrame(() => applyState());
        };

        // Restore after workflow load — fires after properties are populated
        const origConfigure = node.onConfigure;
        node.onConfigure = function (...args) {
            origConfigure?.apply(this, args);
            requestAnimationFrame(() => applyState());
        };

        // Initial apply for freshly-created nodes
        requestAnimationFrame(() => applyState());
    },
});