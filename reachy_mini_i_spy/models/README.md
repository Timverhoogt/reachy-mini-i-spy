# Local detector provenance

File: `ssdlite320_mobilenet_v3_large_coco.onnx`

- Architecture and weights: TorchVision `ssdlite320_mobilenet_v3_large`, default COCO weights
- Original weight URL: `https://download.pytorch.org/models/ssdlite320_mobilenet_v3_large_coco-a79551df.pth`
- Original TorchVision project: `https://github.com/pytorch/vision`
- TorchVision license: BSD 3-Clause
- Export environment: Torch 2.5.1 / TorchVision 0.20.1 on aarch64
- ONNX opset: 18
- Input: float32 RGB `[1, 3, 320, 320]`, range 0–1
- Outputs: `boxes`, `labels`, `scores`
- ONNX SHA-256: `ee09b0d9f02d938780280eea55794275d592782bdcfd1c1b88aa8308cddc120f`

The app permits only an explicit child-safe subset of the model's COCO labels. All other labels fail closed.
