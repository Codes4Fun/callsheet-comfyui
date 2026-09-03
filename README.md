# Callsheet for ComfyUI

ComfyUI Nodes that allow you to go from a callsheet (a prompt of prompts) which is a single large document to a long video, building all the reference assets necessary to achieve that, and able to utilize your custom workflows.

## Status

This is an Alpha release, aka, "It works on my computers", so it needs ffmpeg on your computer and it may have other dependencies I am unaware of. I primarily want to put his out there, so it doesn't die on my computer and for those curious enough to try it and maybe get some feedback.

## Overview

One of the main goals of this is to ease making long form videos when existing models and hardware are good at making small clips, but also to make it easy for workflow developers to quickly integrate new models and nodes as they come out.

The future I would consider making a rich text editor for callsheets, system prompts so that AI can generate the callsheet and/or translate simple prompts into more elaborate prompts for models that need them, and to have a video editor node instead of the simple concat node.

There are 5 types of important nodes and a bunch of utility nodes.

At the front is the `Callsheet Text Input` node. It takes text input of your callsheet, it parses the callsheet and validates it's syntax and references.

At the end are two nodes, `Callsheet Collector Node` and `Callsheet Concat Videos`. The Collector allows you to browse the assets that are generated, the images, audio, and video. It also allows you to create variations, delete, and regenerate, focus on an asset. The Concat Videos node allows you to cut and splice videos together to create a longer video.

Callsheet items have a reference `label` and specify a target `pipeline`. Pipelines are workflows that take inputs like prompts and optional reference data like images, audio, video. The node that provides an input to a pipeline workflow is called `Callsheet Pipeline Node`. The output of a workflow pipeline goes into an appropriate store node: `Callsheet Store Images` `Callsheet Store Videos` and `Callsheet Store Audio`.

It's possible to inject existing image, video, and audio items for a specific reference `label`, using an `Callsheet Inject Image`, `Callsheet Inject Video` and `Callsheet Inject Audio` nodes, and specifying their `source` is `inject` in the callsheet.

## Callsheet Syntax and Specs

TODO

## Tutorials & Examples

TODO

## Credits

The heavy lifting was primarily done by Claude Fable 5, with my role being general design, review, and testing.
