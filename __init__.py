from .input_node import CallsheetTextInput
from .pipeline import CallsheetPipeline
from .flags import CallsheetFlag
from .store_nodes import (CallsheetStoreImages, CallsheetStoreVideos,
                          CallsheetStoreAudio)
from .inject_nodes import (CallsheetInjectImage, CallsheetInjectVideo,
                           CallsheetInjectAudio)
from .embed import CallsheetEmbeddedImage
from .collector import CallsheetCollector
from .concat import CallsheetConcatVideos
from .util_nodes import (CallsheetAudioShift, CallsheetAudioCrossfade,
                         CallsheetAudioResample, CallsheetAudioInfo,
                         CallsheetAudioTrim, CallsheetAudioPad,
                         CallsheetJobRouter)
from . import routes  # noqa: F401  (registers /callsheet/clear)

WEB_DIRECTORY = "./web"

NODE_CLASS_MAPPINGS = {
    "CallsheetTextInput": CallsheetTextInput,
    "CallsheetPipeline": CallsheetPipeline,
    "CallsheetFlag": CallsheetFlag,
    "CallsheetJobRouter": CallsheetJobRouter,
    "CallsheetAudioShift": CallsheetAudioShift,
    "CallsheetStoreImages": CallsheetStoreImages,
    "CallsheetStoreVideos": CallsheetStoreVideos,
    "CallsheetStoreAudio": CallsheetStoreAudio,
    "CallsheetInjectImage": CallsheetInjectImage,
    "CallsheetInjectVideo": CallsheetInjectVideo,
    "CallsheetInjectAudio": CallsheetInjectAudio,
    "CallsheetEmbeddedImage": CallsheetEmbeddedImage,
    "CallsheetCollector": CallsheetCollector,
    "CallsheetConcatVideos": CallsheetConcatVideos,
    "CallsheetAudioCrossfade": CallsheetAudioCrossfade,
    "CallsheetAudioTrim": CallsheetAudioTrim,
    "CallsheetAudioPad": CallsheetAudioPad,
    "CallsheetAudioResample": CallsheetAudioResample,
    "CallsheetAudioInfo": CallsheetAudioInfo,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CallsheetTextInput": "Callsheet Text Input",
    "CallsheetPipeline": "Callsheet Pipeline",
    "CallsheetFlag": "Callsheet Flag",
    "CallsheetJobRouter": "Callsheet Job Router",
    "CallsheetAudioShift": "Callsheet Audio Shift",
    "CallsheetStoreImages": "Callsheet Store Images",
    "CallsheetStoreVideos": "Callsheet Store Videos",
    "CallsheetStoreAudio": "Callsheet Store Audio",
    "CallsheetInjectImage": "Callsheet Inject Image",
    "CallsheetInjectVideo": "Callsheet Inject Video",
    "CallsheetInjectAudio": "Callsheet Inject Audio",
    "CallsheetEmbeddedImage": "Callsheet Embedded Image",
    "CallsheetCollector": "Callsheet Collector",
    "CallsheetConcatVideos": "Callsheet Concat Videos",
    "CallsheetAudioCrossfade": "Callsheet Audio Crossfade",
    "CallsheetAudioTrim": "Callsheet Audio Trim",
    "CallsheetAudioPad": "Callsheet Audio Pad",
    "CallsheetAudioResample": "Callsheet Audio Resample",
    "CallsheetAudioInfo": "Callsheet Audio Info",
}
