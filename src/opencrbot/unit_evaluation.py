from __future__ import annotations

import json
import math
from pathlib import Path


def evaluate_units(analysis: Path, labels: Path) -> dict:
    """Score unit bodies against hand-labeled ground points in capture pixels."""
    label_document = json.loads(labels.read_text(encoding="utf-8-sig"))
    expected = label_document["frames"]
    scope = label_document.get("evaluation_scope", "full_frame_units")
    if scope not in {"full_frame_units", "tracked_identities"}:
        raise ValueError(f"Unknown unit label evaluation_scope: {scope}")
    by_id = {row["frame_id"]: row["units"] for row in expected}
    if not by_id or len(by_id) != len(expected):
        raise ValueError("Expected distinct labeled frames")
    if scope == "tracked_identities":
        _validate_identity_labels(expected)
    frames = []
    seen = set()
    with analysis.open(encoding="utf-8-sig") as stream:
        for line in stream:
            row = json.loads(line)
            frame_id = row["capture"]["frame_id"]
            if frame_id not in by_id:
                continue
            if frame_id in seen or row["observation"]["frame_id"] != frame_id:
                raise ValueError(f"Duplicate or inconsistent frame {frame_id}")
            seen.add(frame_id)
            frames.append(_score_frame(row, by_id[frame_id]))
    if seen != by_id.keys():
        raise ValueError(f"Analysis missing labeled frames: {sorted(by_id.keys() - seen)}")
    if scope == "tracked_identities":
        return {
            "evaluation_scope": scope,
            "identity_tracking": _score_identity_tracking(frames),
            "frames": frames,
        }
    matches = [m for frame in frames for m in frame["matches"]]
    detected = len(matches) + sum(len(f["unmatched_predictions"]) for f in frames)
    labeled = len(matches) + sum(len(f["missed_labels"]) for f in frames)
    return {
        "evaluation_scope": scope,
        "labeled": labeled, "detected": detected, "matched_by_type_and_position": len(matches),
        "precision": round(len(matches) / detected, 3) if detected else None,
        "recall": round(len(matches) / labeled, 3) if labeled else None,
        "correct_owner": sum(m["expected"]["owner"] == m["owner"] for m in matches),
        "mean_distance_tiles": round(sum(m["distance_tiles"] for m in matches) / len(matches), 3) if matches else None,
        "frames": frames,
    }


def _score_frame(row: dict, truth: list[dict]) -> dict:
    observation = row["observation"]
    bounds = row["calibration"].get("board_bounds")
    if bounds is None:
        raise ValueError("Unit evaluation requires calibrated board_bounds")
    left, top, right, bottom = bounds
    width, height = observation["screen_width"], observation["screen_height"]
    predictions = [unit for unit in observation["units"]
                   if unit["card"]["value"] not in {
                       "king-tower", "queen-tower", "princess-tower", "dagger-duchess-tower",
                   }]
    candidates = []
    for i, label in enumerate(truth):
        tx = 18 * (label["pixel_x"] / width - left) / (right - left)
        ty = 32 * (label["pixel_y"] / height - top) / (bottom - top)
        for j, unit in enumerate(predictions):
            x, y = unit["x"]["value"], unit["y"]["value"]
            if unit["card"]["value"] == label["card"] and x is not None and y is not None:
                distance = math.hypot(18 * x - tx, 32 * y - ty)
                if distance <= 3:
                    candidates.append((distance, i, j))
    matched_labels, matched_predictions = set(), set()
    matches = []
    for distance, i, j in sorted(candidates):
        if i not in matched_labels and j not in matched_predictions:
            matched_labels.add(i)
            matched_predictions.add(j)
            matches.append({"expected": truth[i], "owner": predictions[j]["owner"]["value"],
                                "distance_tiles": round(distance, 3),
                                "track_id": predictions[j].get("track_id")})
    return {"frame_id": observation["frame_id"], "matches": matches,
            "missed_labels": [label for i, label in enumerate(truth) if i not in matched_labels],
            "unmatched_predictions": [unit["card"]["value"] for j, unit in enumerate(predictions)
                                      if j not in matched_predictions]}


def _validate_identity_labels(frames: list[dict]) -> None:
    seen_per_frame = set()
    card_by_instance = {}
    for frame in frames:
        frame_id = frame["frame_id"]
        for label in frame["units"]:
            instance_id = label.get("instance_id")
            if not isinstance(instance_id, str) or not instance_id:
                raise ValueError("tracked_identities labels require a non-empty instance_id")
            key = (frame_id, instance_id)
            if key in seen_per_frame:
                raise ValueError(f"Duplicate instance_id {instance_id!r} in frame {frame_id}")
            seen_per_frame.add(key)
            card = (label.get("card"), label.get("owner"))
            previous = card_by_instance.setdefault(instance_id, card)
            if previous != card:
                raise ValueError(f"Inconsistent card or owner for instance_id {instance_id!r}")


def _score_identity_tracking(frames: list[dict]) -> dict:
    observations: dict[str, list[dict]] = {}
    for frame in frames:
        frame_id = frame["frame_id"]
        for match in frame["matches"]:
            label = match["expected"]
            observations.setdefault(label["instance_id"], []).append({
                "frame_id": frame_id,
                "detected": True,
                "track_id": match["track_id"],
                "owner_correct": label["owner"] == match["owner"],
                "distance_tiles": match["distance_tiles"],
            })
        for label in frame["missed_labels"]:
            observations.setdefault(label["instance_id"], []).append({
                "frame_id": frame_id,
                "detected": False,
                "track_id": None,
                "owner_correct": None,
                "distance_tiles": None,
            })

    instances = []
    all_rows = [row for rows in observations.values() for row in rows]
    tracked_rows = [row for row in all_rows if row["track_id"] is not None]
    switch_count = 0
    for instance_id, rows in sorted(observations.items()):
        rows.sort(key=lambda row: row["frame_id"])
        ids = [row["track_id"] for row in rows if row["track_id"] is not None]
        counts = {track_id: ids.count(track_id) for track_id in set(ids)}
        switches = sum(previous != current for previous, current in zip(ids, ids[1:]))
        switch_count += switches
        instances.append({
            "instance_id": instance_id,
            "labeled_observations": len(rows),
            "matched_observations": sum(row["detected"] for row in rows),
            "track_id_observations": len(ids),
            "unique_track_ids": sorted(set(ids)),
            "identity_switches": switches,
            "dominant_track_fraction": round(max(counts.values()) / len(ids), 3) if ids else None,
            "observations": rows,
        })
    matched_rows = [row for row in all_rows if row["detected"]]
    owner_rows = [row for row in matched_rows if row["owner_correct"] is not None]
    return {
        "labeled_observations": len(all_rows),
        "matched_observations": len(matched_rows),
        "detection_coverage": round(len(matched_rows) / len(all_rows), 3) if all_rows else None,
        "track_id_observations": len(tracked_rows),
        "track_id_coverage": round(len(tracked_rows) / len(matched_rows), 3) if matched_rows else None,
        "owner_correct": sum(row["owner_correct"] is True for row in owner_rows),
        "owner_accuracy": round(sum(row["owner_correct"] is True for row in owner_rows) / len(owner_rows), 3)
        if owner_rows else None,
        "identity_switches": switch_count,
        "instances": instances,
    }
