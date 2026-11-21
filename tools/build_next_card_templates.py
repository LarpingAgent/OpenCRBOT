"""Create a provenance-tracked GPGPC Next-icon template archive from manual labels."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from opencrbot.recording import verified_frames
from opencrbot.vision import _box_pixels, load_calibration


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True,
                        help="JSON with frames containing frame_id and my_next card labels.")
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    calibration = load_calibration(args.calibration)
    if calibration.next_card_box is None:
        parser.error("calibration must define next_card_box")
    labels_doc = json.loads(args.labels.read_text(encoding="utf-8-sig"))
    label_rows = labels_doc.get("frames")
    if not isinstance(label_rows, list) or not label_rows:
        parser.error("labels must contain a non-empty frames array")
    labels_by_id = {}
    for row in label_rows:
        frame_id, card = row.get("frame_id"), row.get("my_next")
        if type(frame_id) is not int or frame_id <= 0 or frame_id in labels_by_id:
            parser.error(f"invalid or duplicate frame_id: {frame_id!r}")
        if not isinstance(card, str) or not card.strip():
            parser.error(f"missing my_next label for frame {frame_id}")
        if calibration.known_deck and card not in calibration.known_deck:
            parser.error(f"card {card!r} is outside calibration known_deck")
        labels_by_id[frame_id] = card

    records = {record["frame_id"]: record for record in verified_frames(args.session)}
    missing = sorted(set(labels_by_id) - set(records))
    if missing:
        parser.error(f"labeled frames are missing from the verified session: {missing}")

    images, cards, frame_ids, frame_hashes = [], [], [], []
    for frame_id, card in sorted(labels_by_id.items()):
        record = records[frame_id]
        image = cv2.imdecode(np.frombuffer((args.session / record["file"]).read_bytes(), dtype=np.uint8),
                             cv2.IMREAD_COLOR)
        if image is None:
            parser.error(f"could not decode frame {frame_id}")
        x0, y0, x1, y1 = _box_pixels(calibration.next_card_box, image.shape[1], image.shape[0])
        crop = image[y0:y1, x0:x1]
        if crop.size == 0:
            parser.error(f"next_card_box is empty in frame {frame_id}")
        images.append(crop)
        cards.append(card)
        frame_ids.append(frame_id)
        frame_hashes.append(record["sha256"])

    if len({image.shape for image in images}) != 1:
        parser.error("next-card crops have inconsistent dimensions")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("xb") as stream:
        np.savez_compressed(stream, images=np.stack(images), labels=np.asarray(cards),
                            frame_ids=np.asarray(frame_ids, dtype=np.int32),
                            source_frame_sha256=np.asarray(frame_hashes))
    archive_sha = hashlib.sha256(args.out.read_bytes()).hexdigest()
    calibration_sha = hashlib.sha256(
        json.dumps(calibration.__dict__, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest = {
        "schema": "opencrbot.next_card_templates.v1",
        "session": str(args.session.resolve()),
        "labels": str(args.labels.resolve()),
        "calibration": str(args.calibration.resolve()),
        "calibration_sha256": calibration_sha,
        "template_archive": str(args.out.resolve()),
        "template_archive_sha256": archive_sha,
        "examples": [{"frame_id": frame_id, "my_next": card, "source_frame_sha256": digest}
                     for frame_id, card, digest in zip(frame_ids, cards, frame_hashes, strict=True)],
    }
    manifest_path = args.out.with_suffix(args.out.suffix + ".manifest.json")
    with manifest_path.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    print(json.dumps({"templates": str(args.out.resolve()), "sha256": archive_sha,
                      "examples": len(images), "manifest": str(manifest_path.resolve())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
