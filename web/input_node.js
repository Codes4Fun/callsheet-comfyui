import { app } from "/scripts/app.js";
import { getWidget } from "./common.js";

app.registerExtension({
  name: "callsheet.input",

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== "CallsheetTextInputB") return;

    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      const r = onExecuted?.apply(this, arguments);
      if (message?.cs_inputs) {
        console.log(message.cs_inputs);
        const w = getWidget(this, "cs_inputs");
        w.value = JSON.stringify(message.cs_inputs);
      }
      return r;
    };
  },

  async nodeCreated(node) {
    // Only target your specific node
    if (node.constructor.type !== "CallsheetTextInputB") {
      return;
    }
   
    const updateInputs = () => {
      // ADD: Create new slot when last one is connected
      const lastInput = node.inputs
        .slice()
        .reverse()
        .find(i => i.name.startsWith("input_"));

      if (lastInput && lastInput.link !== null) {
        const nextIndex = parseInt(lastInput.name.split("_")[1]) + 1;
        node.addInput(`input_${nextIndex}`, "*");
      }

      // REMOVE: Clean up empty trailing slots
      const inputSlots = node.inputs.filter(i => i.name.startsWith("input_"));
      if (inputSlots.length > 1) {
        const lastSlot = inputSlots[inputSlots.length - 1];
        const secondLastSlot = inputSlots[inputSlots.length - 2];
        if (lastSlot.link === null && secondLastSlot.link === null) {
          node.removeInput(node.inputs.indexOf(lastSlot));
        }
      }
    };

    // Hook into connection changes
    const originalOnConnectionsChange = node.onConnectionsChange;
    node.onConnectionsChange = function(type, index, connected, link_info) {
      originalOnConnectionsChange?.apply(this, arguments);
      updateInputs();
    };

    // Initial cleanup
    updateInputs();
  },
});
