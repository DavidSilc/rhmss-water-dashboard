# INHGA cell recognition model

`ch_PP-OCRv4_rec_infer.onnx` is the PP-OCRv4 recognition model distributed
with RapidOCR's `rapidocr-onnxruntime==1.4.4` PyPI wheel.

- Upstream: https://github.com/RapidAI/RapidOCR and https://github.com/PaddlePaddle/PaddleOCR
- License: Apache License 2.0 (see LICENSE-PP-OCR.txt).
- SHA-256: `48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b`

The unmodified model is bundled so deployment needs no API key, GPU, external
OCR service, model download at request time, or OS-level OCR installation.
Only recognition runs; table geometry supplies the cell boundaries.
The inference adapter in `app/inhga_ocr.py` follows PP-OCR preprocessing and
CTC decoding, with validation specific to the INHGA water-level bulletin.
