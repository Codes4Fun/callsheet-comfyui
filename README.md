# Callsheet for ComfyUI

ComfyUI Nodes that allow you to go from a callsheet (a prompt of prompts) which is a single large document to a long video, building all the reference assets necessary to achieve that, and able to utilize your custom workflows.

## Status

This is an Alpha release, aka, "It works on my computers", the main dependency is ffmpeg and ffprobe and may be resolved with imageio-ffmpeg python package. I primarily want to put this out there, so it doesn't die on my computer and for those curious enough to try it and to maybe get some feedback.

## Diving In

You can grab an example workflow and start generating and/or follow a prompt development guide.

TODO

## Overview

One of the main goals of this is to ease making long form videos when existing models and hardware are good at making small clips, but also to make it easy for workflow developers to quickly integrate new models and nodes as they come out.

The first thing to understand is that it is a multipass system (like [Video Helper Suite](https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite)) that requeues the workflow automatically with a cap of 16 `max_passes` (that can be increased), it also utilizes lists so multiple generations can be done

In the future I'd like to add a rich text editor for callsheets, system prompts so that AI can generate the callsheet and/or translate simple prompts into more elaborate prompts for models that need them, and to have a video editor node instead of the simple concat node.

There are 5 types of important nodes and a few utility nodes.

At the front is the `Callsheet Text Input` node. It takes text input of your callsheet, it parses the callsheet and validates its syntax and references. The node also retains certain modifiable state information such as variation generation and selection that is modifable by the next important node the `Callsheet Collector`.

At the end are two nodes, `Callsheet Collector` and `Callsheet Concat Videos`. The Collector allows you to browse the assets that are generated, the images, audio, and video. It also allows you to create variations, delete, and regenerate, focus on an asset. The Concat Videos node allows you to cut and splice videos together to create a longer video.

Callsheet items have a reference `label` and specify a target `pipeline`. Pipelines are workflows that take inputs like prompts and optional reference data like images, audio, video. The node that provides an input to a pipeline workflow is called `Callsheet Pipeline`. The output of a workflow pipeline goes into an appropriate store node: `Callsheet Store Images` `Callsheet Store Videos` and `Callsheet Store Audio`.

It's possible to inject existing image, video, and audio items for a specific reference `label`, using an `Callsheet Inject Image`, `Callsheet Inject Video` and `Callsheet Inject Audio` nodes, and specifying their `source` is `injected` in the callsheet.

## Callsheet Syntax and Specs

A callsheet is plain text: a sequence of **blocks** separated by lines
containing only `===`. Each block is either an **item** (something to
generate) or a **defaults block** (settings applied to following items).

### Block structure

```
key: value          <- headers (optional)
key: value
---                 <- header/body separator
prompt text         <- the prompt body (required for items)
```

A block with headers, a `---`, and an **empty body** is a *defaults
block*: its values merge into the running defaults for all following
items. A later defaults block can override individual keys, and an
empty value (`negative:`) clears a default. `label`, `source`, and all
reference properties cannot be defaulted.

Always include the `---` separator, even with no headers — a body whose
first line looks like `key: value` without a separator is rejected to
catch mistakes.

### Properties

| Property | Applies to | Description |
|---|---|---|
| `label` | all | Unique name for the item: letters, digits, `_`, `-`, spaces. Used by references and shown in the Collector. Defaults to `item_N`. |
| `pipeline` | all | Which Pipeline node handles this item. Declared on the Text Input node as `name:type` (types: `image`, `video`, `audio`). |
| `size` | image, video | `832x1216`, or megapixels + aspect: `1.2mp 2:3`, optionally `1.2mp 2:3 step 32` (dimensions snap to the step, default 64). |
| `width` / `height` | image, video | Explicit dimensions; mutually exclusive with `size` in the same item. |
| `length` | video, audio | Duration in frames (`121`) or seconds (`5s`; requires `fps`). Required for video items. |
| `fps` | video | Frames per second. Required for video items. |
| `seed` | all | Sampling seed. Defaults to the Text Input node's `base_seed`. |
| `negative` | all | Negative prompt. |
| `ref` | all | Comma list of **image** item labels (max 4) → Pipeline `ref_1..ref_4`. |
| `audio_ref` | all | Comma list of **audio or video** item labels (max 3) → `audio_ref_1..audio_ref_3`. |
| `video_ref` | all | Comma list of **video** item labels (max 3), each with an optional trim (see below) → `video_ref_N` frames and `video_audio_ref_N` audio. |
| `continue_frame` | video | Single label; the target's last frame (or the image itself, for image items) → `continue_frame` output. Use for first-frame continuation. |
| `continue_video` | video | Single label with optional trim (e.g. `shot_01:-1s` = last second) → `continue_video` frames and `continue_video_audio`. Mutually exclusive with `continue_frame`. |
| `source` | all | `injected`: content is supplied by an Inject node with the same label instead of being generated. Injected items cannot have refs or variations. |
| `flags` | all | Comma list of custom flags (letters, digits, `_`, `-`), read by `Callsheet Flag` nodes to gate parts of a pipeline. |

Unknown property names are errors (with a did-you-mean suggestion).
References must name existing items, cannot self-reference, and cannot
form cycles. When pipelines are typed, reference kinds are validated at
parse time (e.g. `ref` targets must be image items).

### Trim specs

Used by `video_ref`, `continue_video`, and the Concat node's `labels`
widget. Appended to a label with `:`; values are source frames, or
seconds with an `s` suffix:

| Spec | Meaning |
|---|---|
| *(none)* | whole clip |
| `label:4:8` | drop 4 frames from the head, 8 from the tail |
| `label:40-90` | keep frames 40–90 (end-exclusive) |
| `label:-1s` | keep the last second |

In the Concat node the same label may appear multiple times with
different trims to splice cuts from one longer shot.

### Automatic flags

In addition to your own `flags:`, each item automatically carries: its
pipeline name, `ref_1..ref_4`, `audio_ref_1..audio_ref_3`,
`video_ref_1..video_ref_3`, `video_audio_ref_N` (when that video ref
has audio), `continue_frame`, `continue_video`, and
`continue_video_audio` — set only when the slot is populated. Use them
with `Callsheet Flag` + lazy switches to make optional inputs safe.

### Regeneration rules

Every generated variation is identified by a hash of the item's full
definition (prompt, dimensions, seed, flags, resolved references, ...).
Changing **anything** about an item — or about anything it references,
transitively — causes it to regenerate on the next queue. Unchanged
items are never regenerated. Deleting a variation in the Collector (or
its file on disk) regenerates just that variation.

### Example

```
pipeline: character
size: 832x1216
negative: blurry, low quality
---
===
label: knight_sheet
---
knight character sheet, front and side view, neutral pose, white background
===
label: knight_photo
source: injected
---
reference photo of the knight actor
===
pipeline: shot
size: 1280x720
fps: 24
length: 4s
---
===
label: shot_01
ref: knight_sheet
---
the knight walks toward the cathedral door, dusk light
===
label: shot_02
continue_video: shot_01:-1s
flags: turbo
---
the door opens, camera follows inside
```

## Tutorials & Examples

TODO

## Credits

The heavy lifting was primarily done by Claude Fable 5, with my role being general design, review, and testing.
