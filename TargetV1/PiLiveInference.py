#!/usr/bin/env python3
"""
Live Raspberry Pi inference using:
- Picamera2
- TFLite / LiteRT

Press:
- q to quit
"""

from __future__ import annotations

import argparse
import json
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np
#from picamera2 import Picamera2

try:
    import tflite_runtime.interpreter as tflite
except ImportError:
    import tensorflow.lite as tflite  # fallback if full TF is installed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, default="model/target_classifier_int8.tflite")
    parser.add_argument("--metadata", type=str, default="model/metadata.json")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--smoothing-window", type=int, default=None)
    parser.add_argument("--required-positives", type=int, default=None)
    parser.add_argument("--headless", action="store_true")
    return parser.parse_args()


def load_metadata(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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


def preprocess_frame(frame_rgb: np.ndarray, img_size: int) -> tuple[np.ndarray, np.ndarray]:
    gray = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2GRAY)
    resized = cv2.resize(gray, (img_size, img_size), interpolation=cv2.INTER_AREA)

    # Keep raw grayscale intensity domain [0,255], matching training input before Rescaling.
    x = resized.astype(np.float32)
    x = np.expand_dims(x, axis=-1)   # (H, W, 1)
    x = np.expand_dims(x, axis=0)    # (1, H, W, 1)
    return gray, x


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


def main() -> None:
    try:
        from picamera2 import Picamera2
    except ImportError as exc:
        raise RuntimeError(
            "Picamera2 is not installed. Run this script on a Raspberry Pi with "
            "python3-picamera2 installed."
        ) from exc
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
    smoothing_window = (
        int(args.smoothing_window)
        if args.smoothing_window is not None
        else int(metadata.get("default_smoothing_window", 5))
    )
    required_positives = (
        int(args.required_positives)
        if args.required_positives is not None
        else int(metadata.get("default_required_positives", 3))
    )

    if required_positives > smoothing_window:
        raise ValueError("required_positives cannot be greater than smoothing_window")

    interpreter, input_details, output_details = load_interpreter(args.model)

    picam2 = Picamera2()
    config = picam2.create_preview_configuration(
        main={"size": (args.width, args.height), "format": "RGB888"}
    )
    picam2.configure(config)
    picam2.start()
    time.sleep(1.0)

    recent_hits = deque(maxlen=smoothing_window)
    recent_probs = deque(maxlen=smoothing_window)

    previous_time = time.time()
    last_printed_state = None

    try:
        while True:
            frame_rgb = picam2.capture_array()
            frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)

            _, x = preprocess_frame(frame_rgb, img_size)
            prob = infer_probability(interpreter, input_details, output_details, x)

            instant_is_target = prob >= threshold
            recent_hits.append(1 if instant_is_target else 0)
            recent_probs.append(prob)

            stable_is_target = sum(recent_hits) >= required_positives
            display_label = positive_class if stable_is_target else negative_class
            display_color = (0, 255, 0) if stable_is_target else (0, 0, 255)

            now = time.time()
            fps = 1.0 / max(now - previous_time, 1e-6)
            previous_time = now

            mean_prob = float(np.mean(recent_probs)) if recent_probs else prob

            if args.headless:
                if stable_is_target != last_printed_state:
                    print(
                        f"[STATE] {display_label} | "
                        f"instant_prob={prob:.3f} | mean_prob={mean_prob:.3f} | "
                        f"window={list(recent_hits)}"
                    )
                    last_printed_state = stable_is_target
                continue

            cv2.putText(
                frame_bgr,
                f"{display_label}",
                (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                display_color,
                2,
            )
            cv2.putText(
                frame_bgr,
                f"instant_prob={prob:.3f}",
                (20, 80),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.75,
                (255, 255, 255),
                2,
            )
            cv2.putText(
                frame_bgr,
                f"mean_prob={mean_prob:.3f}",
                (20, 115),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.75,
                (255, 255, 255),
                2,
            )
            cv2.putText(
                frame_bgr,
                f"threshold={threshold:.2f}",
                (20, 150),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.75,
                (255, 255, 255),
                2,
            )
            cv2.putText(
                frame_bgr,
                f"window={list(recent_hits)}",
                (20, 185),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.75,
                (255, 255, 255),
                2,
            )
            cv2.putText(
                frame_bgr,
                f"fps={fps:.1f}",
                (20, 220),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.75,
                (255, 255, 255),
                2,
            )
            cv2.putText(
                frame_bgr,
                "Press q to quit",
                (20, args.height - 20),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2,
            )

            cv2.imshow("Target Detector V1", frame_bgr)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break

    finally:
        picam2.stop()
        if not args.headless:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()