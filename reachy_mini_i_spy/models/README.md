# Local detector provenance

File: `yolox_nano.onnx`

- Architecture and weights: official Megvii YOLOX-Nano COCO release
- Original asset URL: `https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx`
- Original project: `https://github.com/Megvii-BaseDetection/YOLOX`
- License: Apache-2.0
- SHA-256: `c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d`
- Input: float32 BGR `[1, 3, 416, 416]`, range 0–255, aspect-preserving letterbox filled with 114
- Output: decoded in-app from `[1, 3549, 85]`, followed by class-aware NMS
The app permits only an explicit child-safe subset of the model's COCO labels. All other labels fail closed.
