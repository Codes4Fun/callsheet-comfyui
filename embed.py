import base64
import io

import numpy as np
import torch
from PIL import Image


class CallsheetEmbeddedImage:
    """An image stored as base64 inside the workflow JSON itself,
    for logos and small graphics."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "image_data": ("STRING", {"default": "", "multiline": True,
                                      "advanced": True}),
        }}

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "load"
    CATEGORY = "callsheet"

    def load(self, image_data):
        if not image_data.strip():
            raise ValueError("CallsheetEmbeddedImage: empty — drop an "
                             "image file onto the node")
        b64 = image_data.split(",", 1)[-1]
        img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
        a = np.array(img).astype(np.float32) / 255.0
        return (torch.from_numpy(a)[None,],)