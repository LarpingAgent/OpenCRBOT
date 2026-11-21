"""Compare unit detections from two analyses of the same captured frames."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path


def _signature(unit: dict) -> tuple:
    return (
        unit.get("detection_id"),
        (unit.get("card") or {}).get("value"),
        (unit.get("owner") or {}).get("value"),
        (unit.get("x") or {}).get("value"),
        (unit.get("y") or {}).get("value"),
    )


def _load_analysis(path: Path) -> tuple[dict[int, tuple[str, Counter]], dict]:
    rows: dict[int, tuple[str, Counter]] = {}
    frames_by_track: dict[int, set[int]] = defaultdict(set)
    classes_by_track: dict[int, set[str | None]] = defaultdict(set)
    non_tower_count = 0
    tracked_non_tower_count = 0
    class_switch_count = 0
    reassociation_count = 0
    tracker_policy = None
    tower_classes = {"king-tower", "queen-tower", "princess-tower"}
    with path.open(encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, start=1):
            row = json.loads(line)
            capture = row.get("capture") or {}
            frame_id = capture.get("frame_id")
            digest = capture.get("sha256")
            if not isinstance(frame_id, int) or not isinstance(digest, str):
                raise ValueError(f"{path}:{line_number} has no frame ID or source hash")
            if frame_id in rows:
                raise ValueError(f"{path}:{line_number} repeats frame {frame_id}")
            units = (row.get("observation") or {}).get("units") or []
            rows[frame_id] = (digest.casefold(), Counter(_signature(unit) for unit in units))
            for unit in units:
                card = (unit.get("card") or {}).get("value")
                if card in tower_classes:
                    continue
                non_tower_count += 1
                track_id = unit.get("track_id")
                if track_id is not None:
                    tracked_non_tower_count += 1
                    frames_by_track[track_id].add(frame_id)
                    classes_by_track[track_id].add(card)
            class_switch_count += len(row.get("track_class_switches") or [])
            reassociation_count += len(row.get("track_reassociations") or [])
            tracker_policy = (row.get("asset_provenance") or {}).get("tracker_id_policy", tracker_policy)
    if not rows:
        raise ValueError(f"No analysis frames found in {path}")
    return rows, {
        "tracker_id_policy": tracker_policy,
        "non_tower_detections": non_tower_count,
        "tracked_detections": tracked_non_tower_count,
        "untracked_detections": non_tower_count - tracked_non_tower_count,
        "track_id_coverage": round(tracked_non_tower_count / non_tower_count, 3) if non_tower_count else None,
        "unique_track_ids": len(frames_by_track),
        "tracks_seen_multiple_frames": sum(len(frame_ids) > 1 for frame_ids in frames_by_track.values()),
        "track_ids_with_multiple_classes": sum(len(classes) > 1 for classes in classes_by_track.values()),
        "class_switch_events": class_switch_count,
        "track_reassociations": reassociation_count,
    }


def compare(left_path: Path, right_path: Path, *, common_frames: bool = False) -> dict:
    left, left_tracking = _load_analysis(left_path)
    right, right_tracking = _load_analysis(right_path)
    left_ids, right_ids = set(left), set(right)
    common_ids = left_ids & right_ids
    if not common_ids:
        raise ValueError("Analysis files have no common frame IDs")
    if not common_frames and left_ids != right_ids:
        raise ValueError(
            "Analysis files do not cover the same frame IDs: "
            f"left-only={sorted(left_ids - right_ids)}, right-only={sorted(right_ids - left_ids)}"
        )
    compared_ids = sorted(common_ids)

    hash_mismatches = [
        frame_id for frame_id in compared_ids if left[frame_id][0] != right[frame_id][0]
    ]
    if hash_mismatches:
        raise ValueError(
            "Analysis files do not reference the same source images; "
            f"hash mismatch frames={hash_mismatches[:20]}"
        )

    changed = []
    left_count = 0
    right_count = 0
    for frame_id in compared_ids:
        left_signature = left[frame_id][1]
        right_signature = right[frame_id][1]
        left_count += sum(left_signature.values())
        right_count += sum(right_signature.values())
        if left_signature != right_signature:
            changed.append({
                "frame_id": frame_id,
                "left_only": list((left_signature - right_signature).elements()),
                "right_only": list((right_signature - left_signature).elements()),
            })

    return {
        "schema": "opencrbot.detection-comparison.v1",
        "left_analysis": str(left_path),
        "right_analysis": str(right_path),
        "frame_comparison": "common_frame_ids" if common_frames else "exact_frame_set",
        "left_only_frame_ids": sorted(left_ids - right_ids),
        "right_only_frame_ids": sorted(right_ids - left_ids),
        "frames_compared": len(compared_ids),
        "frames_with_identical_detections": len(compared_ids) - len(changed),
        "frames_with_detection_differences": changed,
        "left_detection_count": left_count,
        "right_detection_count": right_count,
        "left_tracking": left_tracking,
        "right_tracking": right_tracking,
        "tracking_metric_scope": "all_frames_in_each_analysis",
        "track_ids_ignored": True,
        "source_hashes_verified": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path, help="First OpenCRBOT analysis JSONL")
    parser.add_argument("right", type=Path, help="Second OpenCRBOT analysis JSONL")
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    parser.add_argument("--common-frames", action="store_true",
                        help="Compare shared frame IDs when the analyses have different frame ranges")
    args = parser.parse_args(argv)

    report = compare(args.left, args.right, common_frames=args.common_frames)
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 1 if report["frames_with_detection_differences"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
