from __future__ import annotations

import json
from hashlib import file_digest, sha256
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .clock import read_clock
from .contracts import DetectedUnit, Evidence, Owner, PublicObservation, TowerObservation

CALIBRATION_SCHEMA = "opencrbot.calibration.v1"
ANALYSIS_SCHEMA = "opencrbot.analysis.v1"
_TEMPLATE_SIZE = (56, 72)
_CARD_ORB_MIN_MATCHES = 12
_CARD_ORB_MIN_MARGIN = 6
_CARD_ORB_MATCH_RATIO = 0.75
_CARD_ORB_SCALE = 3
_NEXT_CARD_MIN_SIMILARITY = 0.72
_NEXT_CARD_MIN_MARGIN = 0.10
_TRACKER_REID_MAX_GAP = 2
_TRACKER_REID_MAX_DISTANCE_TILES = 1.25
_TRACKER_REID_AMBIGUITY_MARGIN_TILES = 0.25
_TOWER_NAMES = {"king-tower", "queen-tower", "princess-tower"}
_MODEL_UI = {
    "bar", "bar-level", "tower-bar", "king-tower-bar", "dagger-duchess-tower-bar",
    "skeleton-king-bar", "elixir", "clock", "emote", "evolution-symbol",
    "ice-spirit-evolution-symbol", "text", "selected",
}


class VisionError(RuntimeError):
    """Raised for missing vision packages, bad profiles or invalid model output."""


def _read_towers(units: list[DetectedUnit], now_ms: int, rubble_score, read_hp) -> tuple[TowerObservation, ...]:
    towers = []
    for side, king_y, princess_y in (("own", 0.90625, 0.796875), ("opponent", 0.09375, 0.203125)):
        for kind, lane, x, y in (("king", None, 0.5, king_y), ("princess", "L", 3.5 / 18, princess_y),
                                 ("princess", "R", 14.5 / 18, princess_y)):
            confidence = max((
                unit.card.confidence for unit in units
                if unit.card.value in _TOWER_NAMES
                and (unit.card.value == "king-tower") == (kind == "king")
                and unit.x.value is not None and unit.y.value is not None
                and abs(unit.x.value - x) <= 0.08 and abs(unit.y.value - y) <= 0.08
            ), default=0.0)
            hp = Evidence.unknown("tower_hp_unread", now_ms)
            if confidence:
                alive = Evidence(True, "inferred", confidence, "yolo.tower_at_slot", now_ms)
                fraction = read_hp(len(towers), side, kind)
                if fraction is not None:
                    hp = Evidence(fraction, "inferred", 0.6, "tower.hp_bar_fill", now_ms)
            else:
                score = rubble_score(x, 0.756 if side == "own" else 0.206) if kind == "princess" else 0.0
                if score >= 0.8:
                    alive = Evidence(False, "inferred", score, "tower.rubble_template", now_ms)
                    hp = Evidence(0.0, "inferred", score, "tower.rubble_template", now_ms)
                else:
                    alive = Evidence.unknown("tower_not_detected", now_ms)
            towers.append(TowerObservation(side, kind, lane, alive, hp))
    return tuple(towers)


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
    selected_card_lift: float = 0.0
    board_bounds: tuple[float, float, float, float] | None = None
    tower_hp_bars: tuple[tuple[float, float, float, float], ...] = ()
    next_card_box: tuple[float, float, float, float] | None = None

    def __post_init__(self) -> None:
        if self.schema != CALIBRATION_SCHEMA:
            raise ValueError(f"unsupported calibration schema: {self.schema}")
        if not self.name.strip():
            raise ValueError("calibration name is required")
        if self.screen_orientation not in ("portrait", "landscape"):
            raise ValueError("screen_orientation must be portrait or landscape")
        if len(self.hand_boxes) != 4:
            raise ValueError("calibration must define four hand boxes")
        if not math.isfinite(self.selected_card_lift) or self.selected_card_lift < 0:
            raise ValueError("selected_card_lift must be finite and nonnegative")
        if len(set(self.known_deck)) != len(self.known_deck):
            raise ValueError("known_deck cannot contain duplicate card names")
        boxes = [("arena_crop", self.arena_crop), ("elixir_bar", self.elixir_bar),
                           ("battle_indicator", self.battle_indicator),
                           *((f"hand_boxes[{i}]", box) for i, box in enumerate(self.hand_boxes))]
        if self.board_bounds is not None:
            boxes.append(("board_bounds", self.board_bounds))
        if self.next_card_box is not None:
            boxes.append(("next_card_box", self.next_card_box))
        if self.tower_hp_bars and len(self.tower_hp_bars) != 6:
            raise ValueError("tower_hp_bars must contain six boxes in tower slot order")
        boxes.extend((f"tower_hp_bars[{i}]", box) for i, box in enumerate(self.tower_hp_bars))
        for label, box in boxes:
            if len(box) != 4 or not all(math.isfinite(v) for v in box):
                raise ValueError(f"{label} must be a finite [x0,y0,x1,y1] box")
            x0, y0, x1, y1 = box
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                raise ValueError(f"{label} must be ordered and normalized to [0, 1]")
        if self.selected_card_lift > min(box[1] for box in self.hand_boxes):
            raise ValueError("selected_card_lift moves the hand crop outside the frame")

    @property
    def is_domain_validated(self) -> bool:
        return self.validated_for == "google-play-games-pc/clash-royale"

    def board_position(self, x: float, y: float) -> tuple[float, float]:
        if self.board_bounds is None:
            raise ValueError("board_bounds are required for board projection")
        x0, y0, x1, y1 = self.board_bounds
        return (x - x0) / (x1 - x0), (y - y0) / (y1 - y0)


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
            selected_card_lift=float(raw.get("selected_card_lift", 0)),
            board_bounds=tuple(float(v) for v in raw["board_bounds"]) if raw.get("board_bounds") is not None else None,
            tower_hp_bars=tuple(tuple(float(v) for v in box) for box in raw.get("tower_hp_bars", [])),
            next_card_box=tuple(float(v) for v in raw["next_card_box"]) if raw.get("next_card_box") is not None else None,
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


def _file_sha256(path: Path | None) -> str | None:
    if path is None:
        return None
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def _package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


class VisionProvider:
    """One-frame screen-only detector. Confidence values are not GPGPC-calibrated."""

    def __init__(self, weights: Path, templates: Path, calibration: Calibration, device: str = "cpu",
                 *, clock_templates: Path | None = None, regulation_clock: bool = False, track: bool = False,
                 rubble_template: Path | None = None, next_templates: Path | None = None) -> None:
        cv2, np, YOLO = _vision_packages()
        self.cv2, self.np = cv2, np
        if not weights.is_file():
            raise VisionError(f"YOLO weights not found: {weights}")
        if not templates.is_file():
            raise VisionError(f"card template archive not found: {templates}")
        if next_templates is not None and not next_templates.is_file():
            raise VisionError(f"next-card template archive not found: {next_templates}")
        if next_templates is not None and calibration.next_card_box is None:
            raise VisionError("next_card_box is required when next-card templates are configured")
        self.calibration = calibration
        self.asset_provenance = {
            "yolo_weights_sha256": _file_sha256(weights),
            "card_templates_sha256": _file_sha256(templates),
            "next_card_templates_sha256": _file_sha256(next_templates),
            "calibration_sha256": sha256(json.dumps(
                asdict(calibration), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            ).encode("utf-8")).hexdigest(),
            "clock_templates_sha256": _file_sha256(clock_templates),
            "rubble_template_sha256": _file_sha256(rubble_template),
            "tracking_enabled": track,
            "runtime": {
                "ultralytics": _package_version("ultralytics"),
                "torch": _package_version("torch"),
                "numpy": _package_version("numpy"),
                "opencv_python": _package_version("opencv-python"),
                "lap": _package_version("lap") if track else None,
            },
        }
        try:
            # Explicit, local file only: never use a model name that triggers a download.
            self.model = YOLO(str(weights), task="detect")
            archive = np.load(templates, allow_pickle=False)
            template_images = archive["images"]
            template_labels = archive["labels"].astype(str)
            next_template_images = None
            next_template_labels = None
            if next_templates is not None:
                next_archive = np.load(next_templates, allow_pickle=False)
                next_template_images = next_archive["images"]
                next_template_labels = next_archive["labels"].astype(str)
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
        self.card_orb = cv2.ORB_create(nfeatures=500, scaleFactor=1.2, nlevels=6)
        self.card_matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
        card_descriptors: dict[str, list] = {}
        for image, label in zip(self.template_images, self.template_labels):
            if str(label) == "empty":
                continue
            descriptors = self._card_descriptors(image)
            if descriptors is not None and len(descriptors) >= 2:
                card_descriptors.setdefault(str(label), []).append(descriptors)
        self.card_template_descriptors = {
            label: max(variants, key=len)
            for label, variants in sorted(card_descriptors.items())
        }
        if not self.card_template_descriptors:
            raise VisionError("card template archive has no usable ORB features")
        self.asset_provenance["card_reader"] = {
            "algorithm": "orb-template-matching.v1",
            "minimum_good_matches": _CARD_ORB_MIN_MATCHES,
            "minimum_label_margin": _CARD_ORB_MIN_MARGIN,
            "templates_per_card": 1,
        }
        self.next_template_images = None
        self.next_template_labels = None
        self.next_template_features = None
        if next_template_images is not None and next_template_labels is not None:
            if (next_template_images.ndim != 4 or next_template_images.shape[-1] != 3
                    or len(next_template_images) != len(next_template_labels) or len(next_template_labels) == 0):
                raise VisionError("next-card template archive has mismatched or invalid images/labels")
            if calibration.known_deck:
                keep = np.isin(next_template_labels, np.asarray(calibration.known_deck))
                next_template_images = next_template_images[keep]
                next_template_labels = next_template_labels[keep]
                missing = sorted(set(calibration.known_deck) - set(next_template_labels.tolist()))
                if missing:
                    raise VisionError(f"next-card template archive is missing deck cards: {', '.join(missing)}")
            self.next_template_images = next_template_images
            self.next_template_labels = next_template_labels
            self.next_template_features = np.stack(
                [self._next_card_feature(image) for image in next_template_images]
            )
        self.device = device
        if track and find_spec("lap") is None:
            raise VisionError('Install tracker support with pip install -e ".[vision,tracking]".')
        self.track = track
        self._raw_tracking_result = None
        self._tracker_rows = None
        self._wrapped_tracker_ids: set[int] = set()
        self._tracker_identities: dict[int, tuple[str, int]] = {}
        self._recent_track_positions: dict[int, tuple[str, Owner | None, float, float, int]] = {}
        self._next_track_id = 1
        self._track_observation_index = 0
        if track:
            self.asset_provenance["tracker_id_policy"] = "class-stable.short-gap-reid.v2"
            from ultralytics.trackers import register_tracker

            # Keep the raw detections before Ultralytics replaces them with confirmed tracks.
            self.model.add_callback("on_predict_postprocess_end", self._capture_raw_tracking_result)
            register_tracker(self.model, persist=True)
            self.model.add_callback("on_predict_start", self._capture_tracker_updates)
        self.rubble_template = None
        if rubble_template is not None:
            if calibration.board_bounds is None:
                raise VisionError("Rubble matching requires calibrated board_bounds")
            template = cv2.imdecode(np.frombuffer(rubble_template.read_bytes(), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if template is None or not all(8 <= size <= 71 for size in template.shape) or template.std() < 5:
                raise VisionError("Expected a non-uniform rubble template image between 8 and 71 pixels per side")
            self.rubble_template = template
        self.clock_profile = None
        self.regulation_clock = regulation_clock
        if clock_templates is not None:
            raw = json.loads(clock_templates.read_text(encoding="utf-8-sig"))
            x0, y0, x1, y1 = box = tuple(raw["box"])
            digits = {digit: np.asarray(mask) for digit, mask in raw["digits"].items()}
            if (not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1)
                    or set(digits) != set("0123456789")
                    or any(mask.shape != (24, 16) or not np.isin(mask, [0, 1]).all() for mask in digits.values())):
                raise VisionError("Invalid clock crop or digit templates")
            self.clock_profile = box, digits

    def _capture_raw_tracking_result(self, predictor) -> None:
        if getattr(getattr(predictor, "args", None), "mode", None) == "track":
            results = getattr(predictor, "results", None)
            if results:
                self._raw_tracking_result = results[0]

    def _capture_tracker_updates(self, predictor) -> None:
        if getattr(getattr(predictor, "args", None), "mode", None) != "track":
            return
        for tracker in getattr(predictor, "trackers", ()):
            tracker_identity = id(tracker)
            if tracker_identity in self._wrapped_tracker_ids:
                continue
            update = tracker.update

            def capture_update(results, *args, _update=update, **kwargs):
                rows = _update(results, *args, **kwargs)
                self._tracker_rows = rows.copy()
                return rows

            tracker.update = capture_update
            self._wrapped_tracker_ids.add(tracker_identity)

    def _recent_track_match(
        self,
        unit_name: str,
        owner: Owner | None,
        x: float,
        y: float,
        observation_index: int,
        used_track_ids: set[int],
    ) -> tuple[int, int, float] | None:
        candidates = []
        for track_id, (card, previous_owner, previous_x, previous_y, previous_index) in self._recent_track_positions.items():
            gap = observation_index - previous_index
            if (card != unit_name or previous_owner != owner or track_id in used_track_ids
                    or not 1 <= gap <= _TRACKER_REID_MAX_GAP):
                continue
            distance = math.hypot((x - previous_x) * 18, (y - previous_y) * 32)
            if distance <= _TRACKER_REID_MAX_DISTANCE_TILES:
                candidates.append((distance, track_id, gap))
        candidates.sort()
        if not candidates:
            return None
        if len(candidates) > 1 and candidates[1][0] - candidates[0][0] < _TRACKER_REID_AMBIGUITY_MARGIN_TILES:
            return None
        distance, track_id, gap = candidates[0]
        return track_id, gap, distance

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

    def _next_card_feature(self, crop):
        cv2, np = self.cv2, self.np
        resized = cv2.resize(crop, (64, 64))
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY) if resized.ndim == 3 else resized
        feature = gray.astype(np.float32)
        feature = (feature - feature.mean()) / (feature.std() + 1e-6)
        return feature.ravel() / np.sqrt(feature.size)

    def _card_descriptors(self, crop):
        cv2 = self.cv2
        resized = cv2.resize(crop, None, fx=_CARD_ORB_SCALE, fy=_CARD_ORB_SCALE,
                             interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY) if resized.ndim == 3 else resized
        return self.card_orb.detectAndCompute(gray, None)[1]

    def _identify_card(
        self, frame, box: tuple[float, float, float, float]
    ) -> tuple[str | None, float, dict[str, object]]:
        x0, y0, x1, y1 = _box_pixels(box, frame.shape[1], frame.shape[0])
        crop = frame[y0:y1, x0:x1]
        if crop.size == 0:
            return None, 0.0, {"method": "orb", "reason": "empty_crop", "accepted": False}
        # Ignore the card frame, where screen brightness and card state dominate the match.
        padx, pady = max(1, int(crop.shape[1] * 0.025)), max(1, int(crop.shape[0] * 0.02))
        inner = crop[pady:crop.shape[0] - pady, padx:crop.shape[1] - padx]
        if inner.size == 0:
            return None, 0.0, {"method": "orb", "reason": "empty_inner_crop", "accepted": False}

        query = self._card_descriptors(inner)
        scores: dict[str, int] = {}
        if query is not None and len(query) >= 2:
            for label, template in self.card_template_descriptors.items():
                pairs = self.card_matcher.knnMatch(query, template, k=2)
                scores[label] = sum(
                    1 for pair in pairs
                    if len(pair) == 2 and pair[0].distance < _CARD_ORB_MATCH_RATIO * pair[1].distance
                )
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        if ranked:
            name, matches = ranked[0]
            runner_up = ranked[1][1] if len(ranked) > 1 else 0
            margin = matches - runner_up
            accepted = (name != "empty" and matches >= _CARD_ORB_MIN_MATCHES
                        and margin >= _CARD_ORB_MIN_MARGIN)
            confidence = min(0.99, 0.5 + 0.008 * max(0, matches - _CARD_ORB_MIN_MATCHES)
                             + 0.006 * max(0, margin)) if accepted else 0.0
            details = {
                "method": "orb",
                "candidate": name,
                "good_matches": matches,
                "runner_up_matches": runner_up,
                "margin": margin,
                "accepted": accepted,
            }
            if accepted:
                return name, confidence, details

        # Keep the previous matcher as a conservative fallback for low-texture cards.
        similarities = self.template_features @ self._feature(inner)
        index = int(similarities.argmax())
        fallback_name, fallback_score = str(self.template_labels[index]), float(similarities[index])
        if fallback_name != "empty" and fallback_score >= 0.55:
            return fallback_name, max(0.0, min(fallback_score, 1.0)), {
                "method": "gray_cosine_fallback",
                "candidate": fallback_name,
                "similarity": max(0.0, min(fallback_score, 1.0)),
                "accepted": True,
            }
        if ranked:
            return None, 0.0, {**details, "reason": "orb_threshold_not_met"}
        return None, 0.0, {"method": "orb", "reason": "no_descriptors", "accepted": False}

    def _identify_next_card(self, frame) -> tuple[str | None, str | None, float, float]:
        if self.calibration.next_card_box is None or self.next_template_features is None:
            return None, None, 0.0, 0.0
        x0, y0, x1, y1 = _box_pixels(self.calibration.next_card_box, frame.shape[1], frame.shape[0])
        crop = frame[y0:y1, x0:x1]
        if crop.size == 0:
            return None, None, 0.0, 0.0
        scores = self.next_template_features @ self._next_card_feature(crop)
        label_scores = {
            label: float(scores[self.next_template_labels == label].max())
            for label in set(self.next_template_labels.tolist())
        }
        ranked = sorted(label_scores.items(), key=lambda item: item[1], reverse=True)
        if not ranked:
            return None, None, 0.0, 0.0
        label, score = ranked[0]
        margin = score - ranked[1][1] if len(ranked) > 1 else score
        accepted = score >= _NEXT_CARD_MIN_SIMILARITY and margin >= _NEXT_CARD_MIN_MARGIN
        return (label if accepted else None, label, max(0.0, min(score, 1.0)), max(0.0, min(margin, 1.0)))

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

    def _rubble_score(self, frame, x: float, y: float) -> float:
        if self.rubble_template is None:
            return 0.0
        cv2 = self.cv2
        x0, y0, x1, y1 = self.calibration.board_bounds
        dx, dy = x1 - x0, y1 - y0
        box = (x0 + dx * (x - 0.071), y0 + dy * (y - 0.047),
               x0 + dx * (x + 0.071), y0 + dy * (y + 0.047))
        h, w = frame.shape[:2]
        left, top, right, bottom = _box_pixels(box, w, h)
        crop = cv2.cvtColor(frame[top:bottom, left:right], cv2.COLOR_BGR2GRAY)
        region = cv2.resize(crop, (71, 71))
        return min(1.0, float(cv2.matchTemplate(region, self.rubble_template, cv2.TM_CCOEFF_NORMED).max()))

    def _tower_hp(self, frame, slot: int, side: str, kind: str) -> float | None:
        if not self.calibration.tower_hp_bars:
            return None
        h, w = frame.shape[:2]
        x0, y0, x1, y1 = _box_pixels(self.calibration.tower_hp_bars[slot], w, h)
        pixels = frame[y0:y1, x0:x1].astype(self.np.int16)
        if not pixels.size:
            return None
        b, g, r = pixels.transpose(2, 0, 1)
        if side == "own":
            filled = (b >= 200) & (b - r >= 105) & (b - g >= 30)
            empty_color = (115, 81, 66)
        else:
            filled = (r >= 205) & (r - b >= 65) & (r - g >= 95)
            empty_color = (74, 48, 90)
        if kind == "king":
            empty_color = (84, 93, 110)
        empty = (self.np.abs(pixels - empty_color).max(axis=2) <= 16)
        valid = filled | empty
        row = int(valid.mean(axis=1).argmax())
        if valid[row].mean() < 0.9 or not filled[row, :max(1, pixels.shape[1] // 8)].any():
            return None
        return round(float(filled[row].sum() / valid[row].sum()), 3)

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
            options = dict(imgsz=1024, conf=0.25, iou=0.6, verbose=False, device=self.device)
            if self.track:
                self._raw_tracking_result = None
                self._tracker_rows = None
                self.model.track(arena, persist=True, tracker="bytetrack.yaml", **options)
                result = self._raw_tracking_result
                if result is None or self._tracker_rows is None:
                    raise VisionError("tracker did not preserve raw detections and track assignments")
            else:
                result = self.model.predict(arena, **options)[0]
        except Exception as exc:
            raise VisionError(f"YOLO inference failed: {exc}") from exc

        if self.track:
            self._track_observation_index += 1
        track_observation_index = self._track_observation_index

        units: list[DetectedUnit] = []
        track_class_switches = []
        track_reassociations = []
        used_track_ids: set[int] = set()
        overlay = frame.copy()
        names: dict[int, str] = result.names
        boxes = result.boxes
        if boxes is not None:
            xyxy = boxes.xyxy.tolist()
            classes = boxes.cls.int().tolist()
            confs = boxes.conf.tolist()
            native_track_ids = [None] * len(xyxy)
            if self.track:
                for track_row in self._tracker_rows:
                    detection_index = int(track_row[-1])
                    if 0 <= detection_index < len(native_track_ids):
                        native_track_ids[detection_index] = int(track_row[4])
            arena_h, arena_w = arena.shape[:2]
            for index, (box, class_id, confidence, native_track_id) in enumerate(
                    zip(xyxy, classes, confs, native_track_ids, strict=True)):
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
                position_source = "yolo.box_center"
                if self.calibration.board_bounds is not None:
                    ground_y = y0 + cy + 0.25 * (ay1 - ay0)
                    nx, board_y = self.calibration.board_position((x0 + cx) / width, ground_y / height)
                    position_source = "yolo.projected_ground_estimate"
                on_board = 0.0 <= nx <= 1.0 and 0.0 <= board_y <= 1.0
                track_id = native_track_id
                if self.track and native_track_id is not None:
                    previous = self._tracker_identities.get(native_track_id)
                    if previous is not None and previous[0] == unit_name:
                        track_id = previous[1]
                    else:
                        if previous is not None:
                            track_class_switches.append({
                                "native_track_id": native_track_id,
                                "previous_class": previous[0],
                                "current_class": unit_name,
                            })
                        recent = self._recent_track_match(
                            unit_name, owner_value, nx, board_y, track_observation_index, used_track_ids,
                        ) if on_board else None
                        if recent is None:
                            track_id = self._next_track_id
                            self._next_track_id += 1
                        else:
                            track_id, gap, distance_tiles = recent
                            track_reassociations.append({
                                "native_track_id": native_track_id,
                                "output_track_id": track_id,
                                "unit": unit_name,
                                "owner": owner_value,
                                "gap_observations": gap,
                                "distance_tiles": round(distance_tiles, 3),
                            })
                        self._tracker_identities[native_track_id] = (unit_name, track_id)
                    if track_id in used_track_ids:
                        track_id = self._next_track_id
                        self._next_track_id += 1
                        self._tracker_identities[native_track_id] = (unit_name, track_id)
                    used_track_ids.add(track_id)
                    if on_board:
                        self._recent_track_positions[track_id] = (
                            unit_name, owner_value, nx, board_y, track_observation_index,
                        )
                captured = now_ms
                owner_evidence = (Evidence(owner_value, owner_status, float(confidence), "yolo.class_suffix", captured)
                                  if owner_value is not None else Evidence.unknown("yolo.owner_unavailable", captured))
                units.append(DetectedUnit(
                    card=Evidence(unit_name, "inferred", float(confidence), "yolo.class", captured),
                    owner=owner_evidence,
                    x=Evidence(nx, "inferred", float(confidence), position_source, captured)
                    if on_board else Evidence.unknown(position_source, captured),
                    y=Evidence(board_y, "inferred", float(confidence), position_source, captured)
                    if on_board else Evidence.unknown(position_source, captured),
                    detection_id=f"f{frame_id}-d{index}",
                    track_id=track_id,
                ))
                px0, py0 = int(x0 + ax0), int(y0 + ay0)
                px1, py1 = int(x0 + ax1), int(y0 + ay1)
                color = (255, 150, 30) if owner_value == "own" else (40, 40, 235) if owner_value == "opponent" else (0, 180, 255)
                cv2.rectangle(overlay, (px0, py0), (px1, py1), color, 2)
                cv2.putText(overlay, f"{unit_name} {confidence:.0%}", (px0, max(16, py0 - 4)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)

        if self.track:
            self._recent_track_positions = {
                track_id: state for track_id, state in self._recent_track_positions.items()
                if track_observation_index - state[4] <= _TRACKER_REID_MAX_GAP
            }

        battle_value, battle_confidence = self._read_battle_active(frame)
        clock_seconds = None
        if battle_value and self.clock_profile is not None:
            box, templates = self.clock_profile
            clock_seconds = read_clock(frame, box, templates, cv2)
        hand: list[Evidence[str]] = []
        card_readers: list[dict[str, object]] = []
        for slot, box in enumerate(self.calibration.hand_boxes):
            card, similarity, reader = (self._identify_card(frame, box) if battle_value else
                                        (None, 0.0, {"method": "orb", "reason": "battle_not_active",
                                                     "accepted": False}))
            lift = self.calibration.selected_card_lift
            if battle_value and card is None and lift:
                x0h, y0h, x1h, y1h = box
                raised_box = (x0h, y0h - lift, x1h, y1h - lift)
                raised_card, raised_similarity, raised_reader = self._identify_card(frame, raised_box)
                if raised_card is not None:
                    card, similarity, box, reader = raised_card, raised_similarity, raised_box, raised_reader
                elif raised_reader.get("good_matches", 0) > reader.get("good_matches", 0):
                    reader = raised_reader
            card_readers.append(reader)
            source = f"template_orb.slot_{slot}"
            hand.append(Evidence(card, "inferred", similarity, source, now_ms) if card
                        else Evidence.unknown(source, now_ms))
            x0c, y0c, x1c, y1c = _box_pixels(box, width, height)
            cv2.rectangle(overlay, (x0c, y0c), (x1c, y1c), (0, 220, 220), 1)
            label = f"{slot + 1}: {card or '?'} {similarity:.0%}"
            cv2.putText(overlay, label, (x0c, max(14, y0c - 4)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.38, (0, 220, 220), 1, cv2.LINE_AA)

        next_card = Evidence.unknown("next_card_box_not_configured", now_ms)
        next_reader = {"candidate": None, "similarity": 0.0, "margin": 0.0, "accepted": False,
                       "minimum_similarity": _NEXT_CARD_MIN_SIMILARITY,
                       "minimum_margin": _NEXT_CARD_MIN_MARGIN}
        if self.calibration.next_card_box is not None:
            if self.next_template_features is None:
                next_card = Evidence.unknown("next_card_templates_not_supplied", now_ms)
                next_reader["reason"] = "templates_not_supplied"
            else:
                card, candidate, similarity, margin = self._identify_next_card(frame)
                source = "template_similarity.next_card_icon"
                next_card = (Evidence(card, "inferred", similarity, source, now_ms) if card
                             else Evidence.unknown(source, now_ms))
                next_reader.update(candidate=candidate, similarity=similarity, margin=margin,
                                   accepted=card is not None,
                                   reason=None if card else "similarity_or_margin_below_threshold")
            x0n, y0n, x1n, y1n = _box_pixels(self.calibration.next_card_box, width, height)
            cv2.rectangle(overlay, (x0n, y0n), (x1n, y1n), (0, 220, 220), 1)
            label = f"Next: {next_card.value or '?'} {next_reader['similarity']:.0%}"
            cv2.putText(overlay, label, (x0n, max(14, y0n - 4)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.38, (0, 220, 220), 1, cv2.LINE_AA)

        elixir_value, elixir_confidence = self._read_elixir(frame) if battle_value else (None, 0.0)
        elixir = (Evidence(elixir_value, "inferred", elixir_confidence, "pink_bar_heuristic", now_ms)
                  if elixir_value is not None else Evidence.unknown("pink_bar_unreadable", now_ms))
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
            battle_clock_s=(Evidence(float(clock_seconds), "inferred", 0.6, "clock.digit_templates", now_ms)
                            if clock_seconds is not None else Evidence.unknown("clock_unread", now_ms)),
            battle_elapsed_s=(Evidence(float(180 - clock_seconds), "inferred", 0.6, "clock.regulation", now_ms)
                              if self.regulation_clock and clock_seconds is not None and 0 < clock_seconds <= 180
                              else None),
            battle_active=battle_active,
            my_elixir=elixir,
            opponent_elixir=Evidence.unknown("hidden_game_state", now_ms),
            my_hand=tuple(hand), my_next=next_card, units=tuple(units), public_only=True,
            towers=_read_towers(units, now_ms, lambda x, y: self._rubble_score(frame, x, y),
                                lambda slot, side, kind: self._tower_hp(frame, slot, side, kind))
            if battle_value and self.calibration.board_bounds is not None else (),
        )
        metadata = {
            "schema": ANALYSIS_SCHEMA,
            "calibration": asdict(self.calibration),
            "calibration_validated_for_target": self.calibration.is_domain_validated,
            "asset_provenance": self.asset_provenance,
            "hand_card_readers": card_readers,
            "next_card_reader": next_reader,
            "track_class_switches": track_class_switches,
            "track_reassociations": track_reassociations,
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
