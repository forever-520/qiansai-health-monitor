#!/usr/bin/env python3
"""YOLOv8 bed-state detector used by the Web UI."""

from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = ROOT / "bed_status_yolov8_bed_state_v3_send" / "model" / "best.pt"

CLASS_NAMES = {
    0: "occupied_bed",
    1: "empty_bed",
}

CLASS_TEXT = {
    "occupied_bed": "有人床位",
    "empty_bed": "空床",
}


class BedYoloDetector:
    def __init__(self, model_path: str | os.PathLike[str] | None = None) -> None:
        self.model_path = Path(model_path or os.environ.get("YOLO_MODEL_PATH") or DEFAULT_MODEL)
        self._model = None
        self._load_error = ""
        self._lock = threading.Lock()

    def detect(self, image_bytes: bytes, conf: float = 0.25, iou: float = 0.70, file_name: str = "upload.jpg") -> dict[str, Any]:
        started_at = time.perf_counter()
        if not image_bytes:
            return self._error("未收到图片", started_at)
        if not self.model_path.exists():
            return self._error(f"模型文件不存在: {self.model_path}", started_at)

        model = self._load_model()
        if model is None:
            return self._error(self._load_error or "YOLO模型加载失败", started_at)

        suffix = Path(file_name).suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
            suffix = ".jpg"

        temp_path = ""
        try:
            with tempfile.NamedTemporaryFile(prefix="bed_yolo_", suffix=suffix, delete=False) as tmp:
                tmp.write(image_bytes)
                temp_path = tmp.name

            with self._lock:
                results = model.predict(source=temp_path, conf=conf, iou=iou, verbose=False)
            detections = self._parse_results(results)
            max_conf = max((item["confidence"] for item in detections), default=0.0)
            bed_occupied = self._judge_bed_state(detections)
            return {
                "ok": True,
                "enabled": True,
                "bedOccupied": bed_occupied,
                "confidence": max_conf,
                "maxConfidence": max_conf,
                "elapsedMs": round((time.perf_counter() - started_at) * 1000),
                "detections": detections,
                "message": self._result_message(bed_occupied, detections),
                "model": self.model_path.name,
                "ts": int(time.time() * 1000),
            }
        except Exception as exc:
            return self._error(f"检测失败: {exc}", started_at)
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    def _load_model(self):
        if self._model is not None:
            return self._model
        try:
            from ultralytics import YOLO

            self._model = YOLO(str(self.model_path))
            self._load_error = ""
        except Exception as exc:
            self._load_error = f"YOLO依赖或模型不可用: {exc}"
            self._model = None
        return self._model

    def _parse_results(self, results) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        if not results:
            return parsed
        result = results[0]
        boxes = getattr(result, "boxes", None)
        names = getattr(result, "names", None) or CLASS_NAMES
        if boxes is None:
            return parsed

        xyxy = boxes.xyxy.cpu().tolist() if getattr(boxes, "xyxy", None) is not None else []
        confs = boxes.conf.cpu().tolist() if getattr(boxes, "conf", None) is not None else []
        classes = boxes.cls.cpu().tolist() if getattr(boxes, "cls", None) is not None else []

        for index, box in enumerate(xyxy):
            class_id = int(classes[index]) if index < len(classes) else -1
            label = str(names.get(class_id, CLASS_NAMES.get(class_id, f"class_{class_id}")))
            confidence = float(confs[index]) if index < len(confs) else 0.0
            parsed.append(
                {
                    "id": index,
                    "classId": class_id,
                    "label": label,
                    "className": CLASS_TEXT.get(label, label),
                    "confidence": round(confidence, 4),
                    "bbox": [round(float(value), 2) for value in box[:4]],
                }
            )
        parsed.sort(key=lambda item: item["confidence"], reverse=True)
        return parsed

    def _judge_bed_state(self, detections: list[dict[str, Any]]) -> bool | None:
        occupied = [item for item in detections if item["label"] == "occupied_bed"]
        empty = [item for item in detections if item["label"] == "empty_bed"]
        occupied_conf = max((item["confidence"] for item in occupied), default=0.0)
        empty_conf = max((item["confidence"] for item in empty), default=0.0)
        if occupied_conf <= 0 and empty_conf <= 0:
            return None
        return occupied_conf >= empty_conf

    def _result_message(self, bed_occupied: bool | None, detections: list[dict[str, Any]]) -> str:
        if bed_occupied is True:
            return "检测到有人床位"
        if bed_occupied is False:
            return "检测到空床"
        if detections:
            return "检测到目标"
        return "未检出床位目标"

    def _error(self, message: str, started_at: float) -> dict[str, Any]:
        return {
            "ok": False,
            "enabled": False,
            "bedOccupied": None,
            "confidence": 0,
            "maxConfidence": 0,
            "elapsedMs": round((time.perf_counter() - started_at) * 1000),
            "detections": [],
            "message": message,
            "ts": int(time.time() * 1000),
        }


_DETECTOR: BedYoloDetector | None = None
_DETECTOR_LOCK = threading.Lock()


def detect_bed_state(image_bytes: bytes, conf: float = 0.25, iou: float = 0.70, file_name: str = "upload.jpg") -> dict[str, Any]:
    global _DETECTOR
    with _DETECTOR_LOCK:
        if _DETECTOR is None:
            _DETECTOR = BedYoloDetector()
        detector = _DETECTOR
    return detector.detect(image_bytes=image_bytes, conf=conf, iou=iou, file_name=file_name)
