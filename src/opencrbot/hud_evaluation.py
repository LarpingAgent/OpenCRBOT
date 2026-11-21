from __future__ import annotations

import json
from pathlib import Path


def evaluate_hud(analysis: Path, labels: Path) -> dict:
    """Score only manually labeled fields, counting missing predictions as uncovered."""
    expected = {}
    for frame in json.loads(labels.read_text(encoding="utf-8-sig"))["frames"]:
        frame_id = frame["frame_id"]
        if type(frame_id) is not int or frame_id <= 0 or frame_id in expected:
            raise ValueError(f"Invalid or duplicate label frame ID: {frame_id}")
        if "my_hand" in frame and len(frame["my_hand"]) != 4:
            raise ValueError(f"Expected four labeled hand slots at frame {frame_id}")
        if "towers_alive" in frame and len(frame["towers_alive"]) != 6:
            raise ValueError(f"Expected six labeled tower slots at frame {frame_id}")
        expected[frame_id] = frame
    if not expected:
        raise ValueError("No labeled frames")

    counts = {name: {"labeled": 0, "known": 0, "correct": 0} for name in ("battle_active", "my_hand")}
    if any("my_next" in label for label in expected.values()):
        counts["my_next"] = {"labeled": 0, "known": 0, "correct": 0}
    if any("towers_alive" in label for label in expected.values()):
        counts["towers_alive"] = {"labeled": 0, "known": 0, "correct": 0}
    numeric = {"displayed_elixir": "my_elixir", "battle_clock_s": "battle_clock_s"}
    numeric_counts = dict.fromkeys(numeric, 0)
    numeric_errors = {name: [] for name in numeric}
    seen = set()

    def score(name, label, value):
        counts[name]["labeled"] += 1
        counts[name]["known"] += value is not None
        counts[name]["correct"] += value == label

    with analysis.open(encoding="utf-8-sig") as stream:
        for line in stream:
            row = json.loads(line)
            frame_id = row["capture"]["frame_id"]
            if frame_id not in expected:
                continue
            observation = row["observation"]
            if frame_id in seen or observation["frame_id"] != frame_id:
                raise ValueError(f"Duplicate or inconsistent analysis frame ID: {frame_id}")
            seen.add(frame_id)
            label = expected[frame_id]
            if "battle_active" in label:
                score("battle_active", label["battle_active"], observation["battle_active"]["value"])
            if "my_hand" in label:
                for card, reading in zip(label["my_hand"], observation["my_hand"], strict=True):
                    score("my_hand", card, reading["value"])
            if "my_next" in label:
                score("my_next", label["my_next"], observation.get("my_next", {}).get("value"))
            if "towers_alive" in label:
                towers = {(tower["side"], tower["kind"], tower["lane"]): tower["alive"]["value"]
                          for tower in observation.get("towers", [])}
                slots = [(side, kind, lane) for side in ("own", "opponent")
                         for kind, lane in (("king", None), ("princess", "L"), ("princess", "R"))]
                for tower_slot, alive in zip(slots, label["towers_alive"], strict=True):
                    score("towers_alive", alive, towers.get(tower_slot))
            for label_key, field in numeric.items():
                if label_key in label:
                    numeric_counts[label_key] += 1
                    value = observation[field]["value"]
                    if value is not None:
                        numeric_errors[label_key].append(abs(value - label[label_key]))

    if seen != expected.keys():
        raise ValueError(f"Missing labeled frame IDs in analysis: {sorted(expected.keys() - seen)}")
    report = {"frames": len(seen), **counts}
    for label_key, output_key in (("displayed_elixir", "elixir"), ("battle_clock_s", "battle_clock_s")):
        errors = numeric_errors[label_key]
        report[output_key] = {
            "labeled": numeric_counts[label_key],
            "known": len(errors),
            "mean_absolute_error": round(sum(errors) / len(errors), 3) if errors else None,
        }
    return report
