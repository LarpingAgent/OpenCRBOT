from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .contracts import DetectedUnit, Evidence, Owner, PublicObservation

CALIBRATION_SCHEMA = "opencrbot.calibration.v1"
ANALYSIS_SCHEMA = "opencrbot.analysis.v1"
_TEMPLATE_SIZE = (56, 72)
_MODEL_UI = {
    "bar", "bar-level", "tower-bar", "king-tower-bar", "dagger-duchess-tower-bar",
    "skeleton-king-bar", "elixir", "clock", "emote", "evolution-symbol",
    "ice-spirit-evolution-symbol", "text", "selected",
}


class VisionError(RuntimeError):
    """Raised for missing vision packages, bad profiles or invalid model output."""


@dataclass(frozen=True)
class Calibration:
    name: str
    validated_for: str | None
    screen_orientation: str
    arena_crop: tuple[float, float, float, float]
    hand_boxes: tuple[tuple[float, float, float, float], ...]
    elixir_bar: tuple[float, float, float, float]
    battle_indicator: tuple[float, float, float, float]
    known_deck: tuple[str, ...]
    board_y_flipped: bool
    notes: str = ""
    schema: str = CALIBRATION_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != CALIBRATION_SCHEMA:
            raise ValueError(f"unsupported calibration schema: {self.schema}")
        if not self.name.strip():
            raise ValueError("calibration name is required")
        if self.screen_orientation not in ("portrait", "landscape"):
            raise ValueError("screen_orientation must be portrait or landscape")
        if len(self.hand_boxes) != 4:
            raise ValueError("calibration must define four hand boxes")
        if len(set(self.known_deck)) != len(self.known_deck):
            raise ValueError("known_deck cannot contain duplicate card names")
        for label, box in [("arena_crop", self.arena_crop), ("elixir_bar", self.elixir_bar),
                           ("battle_indicator", self.battle_indicator),
                           *((f"hand_boxes[{i}]", box) for i, box in enumerate(self.hand_boxes))]:
            if len(box) != 4 or not all(math.isfinite(v) for v in box):
                raise ValueError(f"{label} must be a finite [x0,y0,x1,y1] box")
            x0, y0, x1, y1 = box
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                raise ValueError(f"{label} must be ordered and normalized to [0, 1]")

    @property
    def is_domain_validated(self) -> bool:
        return self.validated_for == "google-play-games-pc/clash-royale"


def load_calibration(path: Path) -> Calibration:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Calibration(
            name=str(raw["name"]),
            validated_for=raw.get("validated_for"),
            screen_orientation=str(raw["screen_orientation"]),
            arena_crop=tuple(float(v) for v in raw["arena_crop"]),
            hand_boxes=tuple(tuple(float(v) for v in box) for box in raw["hand_boxes"]),
            elixir_bar=tuple(float(v) for v in raw["elixir_bar"]),
            battle_indicator=tuple(float(v) for v in raw["battle_indicator"]),
            known_deck=tuple(str(card) for card in raw.get("known_deck", [])),
            board_y_flipped=bool(raw.get("board_y_flipped", True)),
            notes=str(raw.get("notes", "")),
            schema=str(raw.get("schema", "")),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise VisionError(f"cannot load calibration {path}: {exc}") from exc


def _vision_packages():
    try:
        import cv2
        import numpy as np
        from ultralytics import YOLO
    except ImportError as exc:
        raise VisionError("Vision dependencies are missing. Install with `python -m pip install -e .[vision]`.") from exc
    return cv2, np, YOLO


def _box_pixels(box: tuple[float, float, float, float], width: int, height: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    return (max(0, int(x0 * width)), max(0, int(y0 * height)),
            min(width, int(math.ceil(x1 * width))), min(height, int(math.ceil(y1 * height))))


class VisionProvider:
    """One-frame screen-only detector. Confidence values are not GPGPC-calibrated."""

    def __init__(self, weights: Path, templates: Path, calibration: Calibration, device: str = "cpu") -> None:
        cv2, np, YOLO = _vision_packages()
        self.cv2, self.np = cv2, np
        if not weights.is_file():
            raise VisionError(f"YOLO weights not found: {weights}")
        if not templates.is_file():
            raise VisionError(f"card template archive not found: {templates}")
        self.calibration = calibration
        try:
            # Explicit, local file only: never use a model name that triggers a download.
            self.model = YOLO(str(weights), task="detect")
            archive = np.load(templates, allow_pickle=False)
            template_images = archive["images"]
            template_labels = archive["labels"].astype(str)
        except Exception as exc:
            raise VisionError(f"could not load local YOLO/template assets: {exc}") from exc
        if calibration.known_deck:
            keep = np.asarray([(label in calibration.known_deck) or label == "empty" for label in template_labels])
            template_images, template_labels = template_images[keep], template_labels[keep]
            if not any(label in calibration.known_deck for label in template_labels):
                raise VisionError("none of known_deck cards are present in the template archive")
        self.template_images = template_images
        self.template_labels = template_labels
        if len(self.template_images) != len(self.template_labels) or len(self.template_labels) == 0:
            raise VisionError("template archive has mismatched or empty images/labels")
        self.template_features = np.stack([self._feature(image) for image in self.template_images])
        self.device = device

    def _feature(self, crop):
        cv2, np = self.cv2, self.np
        resized = cv2.resize(crop, _TEMPLATE_SIZE)
        if resized.ndim == 2:
            gray = resized
        else:
            gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        feature = gray[:52].astype(np.float32)
        feature = (feature - feature.mean()) / (feature.std() + 1e-6)
        return feature.ravel() / np.sqrt(feature.size)

    def _identify_card(self, frame, box: tuple[float, float, float, float]) -> tuple[str | None, float]:
        x0, y0, x1, y1 = _box_pixels(box, frame.shape[1], frame.shape[0])
        crop = frame[y0:y1, x0:x1]
        if crop.size == 0:
            return None, 0.0
        # Ignore the card frame, where screen brightness and card state dominate the match.
        padx, pady = max(1, int(crop.shape[1] * 0.025)), max(1, int(crop.shape[0] * 0.02))
        inner = crop[pady:crop.shape[0] - pady, padx:crop.shape[1] - padx]
        if inner.size == 0:
            return None, 0.0
        scores = self.template_features @ self._feature(inner)
        index = int(scores.argmax())
        name, score = str(self.template_labels[index]), float(scores[index])
        if name == "empty" or score < 0.55:
            return None, max(0.0, min(score, 1.0))
        return name, max(0.0, min(score, 1.0))

    def _read_elixir(self, frame) -> tuple[float | None, float]:
        cv2, np = self.cv2, self.np
        x0, y0, x1, y1 = _box_pixels(self.calibration.elixir_bar, frame.shape[1], frame.shape[0])
        row = frame[y0:y1, x0:x1]
        if row.size == 0:
            return None, 0.0
        pixels = row.astype(np.int16)
        blue, green, red = pixels[..., 0], pixels[..., 1], pixels[..., 2]
        valid = (blue > 70) & (green < 150)
        if float(valid.mean()) < 0.5:
            return None, 0.0
        filled = valid & (red > 150) & (red > green * 1.3)
        # This bar heuristic comes from another device profile; keep its confidence low.
        return round(10.0 * float(filled.sum()) / max(1, valid.sum()), 1), 0.55

    def _read_battle_active(self, frame) -> tuple[bool | None, float]:
        _, np = self.cv2, self.np
        x0, y0, x1, y1 = _box_pixels(self.calibration.elixir_bar, frame.shape[1], frame.shape[0])
        bar = frame[y0:y1, x0:x1]
        ix0, iy0, ix1, iy1 = _box_pixels(self.calibration.battle_indicator, frame.shape[1], frame.shape[0])
        marker = frame[iy0:iy1, ix0:ix1]
        if bar.size == 0 or marker.size == 0:
            return None, 0.0
        bar = bar.astype(np.int16)
        blue_bar = ((bar[..., 0] > 90) & (bar[..., 1] < 100)).mean()
        bgr = marker.astype(np.float32).mean(axis=(0, 1))
        marker_visible = bgr[0] > 150 and bgr[1] < 90 and bgr[2] > 150
        active = bool(blue_bar >= 0.80 and marker_visible)
        # This UI-color check is adapted from a phone profile and is not GPGPC-calibrated.
        return active, 0.60

    def analyze(self, frame, *, serial: str, frame_id: int | None = None):
        cv2, np = self.cv2, self.np
        if frame is None or not hasattr(frame, "shape") or frame.ndim != 3 or frame.shape[2] != 3:
            raise VisionError("input frame must be a decoded BGR color image")
        height, width = frame.shape[:2]
        if min(width, height) < 100:
            raise VisionError("input image is too small to be a game screen")
        now_ms = time.monotonic_ns() // 1_000_000
        frame_id = now_ms if frame_id is None else frame_id
        x0, y0, x1, y1 = _box_pixels(self.calibration.arena_crop, width, height)
        arena = frame[y0:y1, x0:x1]
        if arena.size == 0:
            raise VisionError("arena crop is empty; check calibration")
        try:
            result = self.model.predict(arena, imgsz=1024, conf=0.25, iou=0.6, verbose=False, device=self.device)[0]
        except Exception as exc:
            raise VisionError(f"YOLO inference failed: {exc}") from exc

        units: list[DetectedUnit] = []
        overlay = frame.copy()
        names: dict[int, str] = result.names
        boxes = result.boxes
        if boxes is not None:
            xyxy = boxes.xyxy.tolist()
            classes = boxes.cls.int().tolist()
            confs = boxes.conf.tolist()
            arena_h, arena_w = arena.shape[:2]
            for index, (box, class_id, confidence) in enumerate(zip(xyxy, classes, confs)):
                raw_name = str(names[int(class_id)])
                base_name, sep, side = raw_name.rpartition("_")
                if sep and side in ("0", "1"):
                    unit_name = base_name
                    owner_value: Owner | None = "opponent" if side == "1" else "own"
                    owner_status = "inferred"
                else:
                    unit_name, owner_value, owner_status = raw_name, None, "unknown"
                if unit_name in _MODEL_UI or unit_name.startswith("padding"):
                    continue
                ax0, ay0, ax1, ay1 = (float(v) for v in box)
                cx, cy = (ax0 + ax1) / 2, (ay0 + ay1) / 2
                nx = min(1.0, max(0.0, cx / arena_w))
                ny = cy / arena_h
                board_y = 1.0 - ny if self.calibration.board_y_flipped else ny
                captured = now_ms
                owner_evidence = (Evidence(owner_value, owner_status, float(confidence), "yolo.class_suffix", captured)
                                  if owner_value is not None else Evidence.unknown("yolo.owner_unavailable", captured))
                units.append(DetectedUnit(
                    card=Evidence(unit_name, "inferred", float(confidence), "yolo.class", captured),
                    owner=owner_evidence,
                    x=Evidence(nx, "inferred", float(confidence), "yolo.box_center", captured),
                    y=Evidence(board_y, "inferred", float(confidence), "yolo.box_center", captured),
                    detection_id=f"f{frame_id}-d{index}",
                ))
                px0, py0 = int(x0 + ax0), int(y0 + ay0)
                px1, py1 = int(x0 + ax1), int(y0 + ay1)
                color = (255, 150, 30) if owner_value == "own" else (40, 40, 235) if owner_value == "opponent" else (0, 180, 255)
                cv2.rectangle(overlay, (px0, py0), (px1, py1), color, 2)
                cv2.putText(overlay, f"{unit_name} {confidence:.0%}", (px0, max(16, py0 - 4)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)

        hand: list[Evidence[str]] = []
        for slot, box in enumerate(self.calibration.hand_boxes):
            card, similarity = self._identify_card(frame, box)
            source = f"template_similarity.slot_{slot}"
            hand.append(Evidence(card, "inferred", similarity, source, now_ms) if card
                        else Evidence.unknown(source, now_ms))
            x0c, y0c, x1c, y1c = _box_pixels(box, width, height)
            cv2.rectangle(overlay, (x0c, y0c), (x1c, y1c), (0, 220, 220), 1)
            label = f"{slot + 1}: {card or '?'} {similarity:.0%}"
            cv2.putText(overlay, label, (x0c, max(14, y0c - 4)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.38, (0, 220, 220), 1, cv2.LINE_AA)

        elixir_value, elixir_confidence = self._read_elixir(frame)
        elixir = (Evidence(elixir_value, "inferred", elixir_confidence, "pink_bar_heuristic", now_ms)
                  if elixir_value is not None else Evidence.unknown("pink_bar_unreadable", now_ms))
        battle_value, battle_confidence = self._read_battle_active(frame)
        battle_active = (Evidence(battle_value, "inferred", battle_confidence, "elixir_ui_color_heuristic", now_ms)
                         if battle_value is not None else Evidence.unknown("battle_ui_unreadable", now_ms))
        cv2.putText(overlay, f"battle UI: {'yes' if battle_value else 'unknown/no'} (unverified)", (12, 48),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 220), 1, cv2.LINE_AA)
        if elixir_value is not None:
            cv2.putText(overlay, f"elixir ~{elixir_value:.1f}/10 (unverified)", (12, 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 220, 220), 2, cv2.LINE_AA)

        observation = PublicObservation(
            frame_id=int(frame_id), serial=serial, captured_at_ms=now_ms,
            screen_width=width, screen_height=height,
            battle_clock_s=Evidence.unknown("clock_not_implemented", now_ms),
            battle_active=battle_active,
            my_elixir=elixir,
            opponent_elixir=Evidence.unknown("hidden_game_state", now_ms),
            my_hand=tuple(hand), units=tuple(units), public_only=True,
        )
        metadata = {
            "schema": ANALYSIS_SCHEMA,
            "calibration": asdict(self.calibration),
            "calibration_validated_for_target": self.calibration.is_domain_validated,
            "warnings": [
                "YOLO detections, card similarities and elixir estimate have not been validated on Google Play Games on PC.",
                "This analysis uses visible screen pixels only; hidden opponent state remains unknown.",
            ] + ([] if self.calibration.known_deck else [
                "known_deck is empty; template matching can select any card label in the upstream archive. Set your current eight-card deck in the calibration JSON.",
            ]),
        }
        return observation, overlay, metadata


def decode_image(data: bytes):
    cv2, np, _ = _vision_packages()
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise VisionError("could not decode input PNG/image")
    return image
