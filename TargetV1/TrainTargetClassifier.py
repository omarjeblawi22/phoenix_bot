#!/usr/bin/env python3
"""
Train a small binary image classifier for:
- target
- no_target

Outputs:
- model/best_model.keras
- model/target_classifier_float32.tflite
- model/target_classifier_int8.tflite
- model/metadata.json
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Tuple

import numpy as np
import tensorflow as tf
from tensorflow import keras
from tensorflow.keras import layers


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=str, default="dataset")
    parser.add_argument("--model-dir", type=str, default="model")
    parser.add_argument("--img-size", type=int, default=128)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def load_datasets(
    data_dir: Path,
    img_size: int,
    batch_size: int,
    seed: int,
) -> Tuple[tf.data.Dataset, tf.data.Dataset, list[str]]:
    train_dir = data_dir / "train"
    val_dir = data_dir / "val"

    if not train_dir.exists() or not val_dir.exists():
        raise FileNotFoundError(
            "Expected dataset/train/... and dataset/val/... directories."
        )

    train_ds = tf.keras.utils.image_dataset_from_directory(
        train_dir,
        labels="inferred",
        label_mode="binary",
        color_mode="grayscale",
        image_size=(img_size, img_size),
        batch_size=batch_size,
        shuffle=True,
        seed=seed,
    )

    val_ds = tf.keras.utils.image_dataset_from_directory(
        val_dir,
        labels="inferred",
        label_mode="binary",
        color_mode="grayscale",
        image_size=(img_size, img_size),
        batch_size=batch_size,
        shuffle=False,
    )

    class_names = list(train_ds.class_names)
    if len(class_names) != 2:
        raise ValueError(
            f"Expected exactly 2 classes, found {len(class_names)}: {class_names}"
        )

    autotune = tf.data.AUTOTUNE
    train_ds = train_ds.prefetch(autotune)
    val_ds = val_ds.prefetch(autotune)
    return train_ds, val_ds, class_names


def build_model(img_size: int, learning_rate: float) -> keras.Model:
    augmentation = keras.Sequential(
        [
            layers.RandomFlip("horizontal"),
            layers.RandomRotation(0.08),
            layers.RandomZoom(0.10),
            layers.RandomContrast(0.10),
        ],
        name="augmentation",
    )

    model = keras.Sequential(
        [
            layers.Input(shape=(img_size, img_size, 1)),
            augmentation,
            layers.Rescaling(1.0 / 255.0),

            layers.Conv2D(16, 3, padding="same", activation="relu"),
            layers.MaxPooling2D(),

            layers.Conv2D(32, 3, padding="same", activation="relu"),
            layers.MaxPooling2D(),

            layers.Conv2D(64, 3, padding="same", activation="relu"),
            layers.MaxPooling2D(),

            layers.Flatten(),
            layers.Dense(64, activation="relu"),
            layers.Dropout(0.30),
            layers.Dense(1, activation="sigmoid"),
        ],
        name="target_classifier",
    )

    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate),
        loss="binary_crossentropy",
        metrics=[
            keras.metrics.BinaryAccuracy(name="accuracy"),
            keras.metrics.Precision(name="precision"),
            keras.metrics.Recall(name="recall"),
            keras.metrics.AUC(name="auc"),
        ],
    )
    return model


def collect_validation_predictions(
    model: keras.Model, val_ds: tf.data.Dataset
) -> tuple[np.ndarray, np.ndarray]:
    y_true = []
    y_prob = []

    for images, labels in val_ds:
        probs = model.predict(images, verbose=0).reshape(-1)
        y_prob.extend(probs.tolist())
        y_true.extend(labels.numpy().astype(np.int32).reshape(-1).tolist())

    return np.array(y_true, dtype=np.int32), np.array(y_prob, dtype=np.float32)


def find_best_threshold(y_true: np.ndarray, y_prob: np.ndarray) -> dict:
    best = {
        "threshold": 0.5,
        "f1": -1.0,
        "precision": 0.0,
        "recall": 0.0,
        "accuracy": 0.0,
    }

    for threshold in np.linspace(0.10, 0.90, 17):
        y_pred = (y_prob >= threshold).astype(np.int32)

        tp = int(np.sum((y_pred == 1) & (y_true == 1)))
        tn = int(np.sum((y_pred == 0) & (y_true == 0)))
        fp = int(np.sum((y_pred == 1) & (y_true == 0)))
        fn = int(np.sum((y_pred == 0) & (y_true == 1)))

        precision = tp / (tp + fp + 1e-8)
        recall = tp / (tp + fn + 1e-8)
        accuracy = (tp + tn) / max(len(y_true), 1)
        f1 = 2.0 * precision * recall / (precision + recall + 1e-8)

        if f1 > best["f1"]:
            best = {
                "threshold": float(round(threshold, 3)),
                "f1": float(f1),
                "precision": float(precision),
                "recall": float(recall),
                "accuracy": float(accuracy),
            }

    return best


def save_float_tflite(model: keras.Model, output_path: Path) -> None:
    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    tflite_model = converter.convert()
    output_path.write_bytes(tflite_model)


def save_int8_tflite(
    model: keras.Model, train_ds: tf.data.Dataset, output_path: Path
) -> None:
    def representative_data_gen():
        for images, _ in train_ds.take(50):
            for i in range(images.shape[0]):
                # Match training input domain: grayscale values in [0,255]
                yield [tf.cast(images[i:i + 1], tf.float32)]

    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset = representative_data_gen
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    tflite_model = converter.convert()
    output_path.write_bytes(tflite_model)


def main() -> None:
    args = parse_args()
    tf.keras.utils.set_random_seed(args.seed)

    data_dir = Path(args.data_dir)
    model_dir = Path(args.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)

    train_ds, val_ds, class_names = load_datasets(
        data_dir=data_dir,
        img_size=args.img_size,
        batch_size=args.batch_size,
        seed=args.seed,
    )

    print(f"Class names: {class_names}")
    print("Note: with binary labels, class_names[1] is the positive class.")

    model = build_model(args.img_size, args.learning_rate)
    model.summary()

    best_model_path = model_dir / "best_model.keras"

    callbacks = [
        keras.callbacks.ModelCheckpoint(
            filepath=str(best_model_path),
            monitor="val_auc",
            mode="max",
            save_best_only=True,
        ),
        keras.callbacks.EarlyStopping(
            monitor="val_auc",
            mode="max",
            patience=5,
            restore_best_weights=True,
        ),
        keras.callbacks.ReduceLROnPlateau(
            monitor="val_loss",
            factor=0.5,
            patience=2,
            min_lr=1e-5,
        ),
    ]

    model.fit(
        train_ds,
        validation_data=val_ds,
        epochs=args.epochs,
        callbacks=callbacks,
        verbose=1,
    )

    eval_results = model.evaluate(val_ds, verbose=0)
    metric_names = model.metrics_names
    metrics = {name: float(value) for name, value in zip(metric_names, eval_results)}

    y_true, y_prob = collect_validation_predictions(model, val_ds)
    threshold_info = find_best_threshold(y_true, y_prob)

    keras_path = model_dir / "target_classifier.keras"
    model.save(keras_path)

    float_tflite_path = model_dir / "target_classifier_float32.tflite"
    int8_tflite_path = model_dir / "target_classifier_int8.tflite"

    save_float_tflite(model, float_tflite_path)
    save_int8_tflite(model, train_ds, int8_tflite_path)

    metadata = {
        "img_size": args.img_size,
        "color_mode": "grayscale",
        "class_names": class_names,
        "negative_class": class_names[0],
        "positive_class": class_names[1],
        "recommended_threshold": threshold_info["threshold"],
        "validation_metrics": metrics,
        "threshold_metrics": threshold_info,
        "default_smoothing_window": 5,
        "default_required_positives": 3,
        "artifacts": {
            "keras_model": keras_path.name,
            "float32_tflite": float_tflite_path.name,
            "int8_tflite": int8_tflite_path.name,
            "best_checkpoint": best_model_path.name,
        },
    }

    metadata_path = model_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    print("\nSaved artifacts:")
    print(f"  {keras_path}")
    print(f"  {float_tflite_path}")
    print(f"  {int8_tflite_path}")
    print(f"  {metadata_path}")
    print("\nRecommended threshold:", metadata["recommended_threshold"])
    print("Validation metrics:", json.dumps(metrics, indent=2))
    print("Threshold metrics:", json.dumps(threshold_info, indent=2))


if __name__ == "__main__":
    main()