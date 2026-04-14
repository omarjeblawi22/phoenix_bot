#!/usr/bin/env python3
"""
Run inference on:
- a single image file
- or a directory of images

Works with either:
- model/target_classifier_float32.tflite
- model/target_classifier_int8.tflite
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np

try:
    import tflite_runtime.interpreter as tflite
except ImportError:
    import tensorflow.lite as tflite  # fallback for desktop environments


VALID_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=str, required=True, help="Image file or folder")
    parser.add_argument("--model", type=str, default="model/target_classifier_int8.tflite")
    parser.add_argument("--metadata", type=str, default="model/metadata.json")
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--output-dir", type=str, default=None)
    parser.add_argument("--recursive", action="store_true")
    return parser.parse_args()


def load_metadata(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_images(path: Path, recursive: bool) -> Iterable[Path]:
    if path.is_file():
        if path.suffix.lower() in VALID_EXTS:
            yield path
        return

    pattern = "**/*" if recursive else "*"
    for p in sorted(path.glob(pattern)):
        if p.is_file() and p.suffix.lower() in VALID_EXTS:
            yield p


def load_interpreter(model_path: str):
    interpreter = tflite.Interpreter(model_path=model_path)
    interpreter.allocate_tensors()
    input_details = interpreter.get_input_details()[0]
    output_details = interpreter.get_output_details()[0]
    return interpreter, input_details, output_details


def quantize_if_needed(x: np.ndarray, tensor_details: dict) -> np.ndarray:
    dtype = tensor_details["dtype"]
    if dtype == np.float32:
        return x.astype(np.float32)

    scale, zero_point = tensor_details["quantization"]
    if scale == 0:
        return x.astype(dtype)

    q = np.round(x / scale + zero_point)
    if np.issubdtype(dtype, np.integer):
        info = np.iinfo(dtype)
        q = np.clip(q, info.min, info.max)
    return q.astype(dtype)


def dequantize_output(y: np.ndarray, tensor_details: dict) -> float:
    value = float(np.squeeze(y))
    dtype = tensor_details["dtype"]

    if dtype == np.float32:
        return value

    scale, zero_point = tensor_details["quantization"]
    if scale == 0:
        return value

    return float((value - zero_point) * scale)


def preprocess_image(image_path: Path, img_size: int) -> tuple[np.ndarray, np.ndarray]:
    bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError(f"Could not read image: {image_path}")

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (img_size, img_size), interpolation=cv2.INTER_AREA)

    # Keep raw grayscale intensity domain [0,255], matching training input before Rescaling.
    x = resized.astype(np.float32)
    x = np.expand_dims(x, axis=-1)   # (H, W, 1)
    x = np.expand_dims(x, axis=0)    # (1, H, W, 1)
    return bgr, x


def infer_probability(
    interpreter,
    input_details: dict,
    output_details: dict,
    x: np.ndarray,
) -> float:
    x_in = quantize_if_needed(x, input_details)
    interpreter.set_tensor(input_details["index"], x_in)
    interpreter.invoke()
    y = interpreter.get_tensor(output_details["index"])
    prob = dequantize_output(y, output_details)
    return float(np.clip(prob, 0.0, 1.0))


def annotate_image(
    bgr: np.ndarray,
    label: str,
    probability: float,
    color: tuple[int, int, int],
) -> np.ndarray:
    out = bgr.copy()
    cv2.putText(out, label, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)
    cv2.putText(
        out,
        f"prob={probability:.3f}",
        (20, 80),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        color,
        2,
    )
    return out


def main() -> None:
    args = parse_args()
    metadata = load_metadata(Path(args.metadata))

    img_size = int(metadata["img_size"])
    positive_class = metadata["positive_class"]
    negative_class = metadata["negative_class"]
    threshold = (
        float(args.threshold)
        if args.threshold is not None
        else float(metadata.get("recommended_threshold", 0.5))
    )

    interpreter, input_details, output_details = load_interpreter(args.model)

    output_dir = Path(args.output_dir) if args.output_dir else None
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    input_path = Path(args.input)
    image_paths = list(iter_images(input_path, args.recursive))
    if not image_paths:
        raise FileNotFoundError(f"No images found at: {input_path}")

    target_count = 0
    no_target_count = 0

    for image_path in image_paths:
        try:
            bgr, x = preprocess_image(image_path, img_size)
            prob = infer_probability(interpreter, input_details, output_details, x)
            is_target = prob >= threshold
            label = positive_class if is_target else negative_class
            color = (0, 255, 0) if is_target else (0, 0, 255)

            if is_target:
                target_count += 1
            else:
                no_target_count += 1

            print(f"{image_path}: label={label} prob={prob:.4f}")

            if output_dir:
                annotated = annotate_image(bgr, label, prob, color)
                out_path = output_dir / image_path.name
                cv2.imwrite(str(out_path), annotated)

        except Exception as exc:
            print(f"{image_path}: ERROR - {exc}")

    total = target_count + no_target_count
    print("\nSummary")
    print(f"  Total images : {total}")
    print(f"  {positive_class:12s}: {target_count}")
    print(f"  {negative_class:12s}: {no_target_count}")
    print(f"  Threshold    : {threshold}")


if __name__ == "__main__":
    main()