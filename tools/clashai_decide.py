"""Run a ClashAI S1 or Gen FV3 policy on one exported BoardState without game I/O."""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import file_digest
import json
from pathlib import Path

from opencrbot.clashai_export import to_board_state
from opencrbot.clashai_history import tracked_play_history
from opencrbot.clashai_policy import load_clashai_policy


def compare_session(analysis: Path, policy, *, use_tracked_history: bool = False) -> dict:
    with analysis.open("rb") as stream:
        source_sha256 = file_digest(stream, "sha256").hexdigest()
    with analysis.open(encoding="utf-8-sig") as stream:
        rows = [json.loads(line) for line in stream]
    own_plays, opponent_plays, history_report = (tracked_play_history(rows, policy)
                                                   if use_tracked_history else ([], [], None))
    results = []
    previous_id = 0
    for row in rows:
        observation = row["observation"]
        frame_id = row["capture"]["frame_id"]
        if frame_id <= previous_id or frame_id != observation["frame_id"]:
            raise ValueError(f"Invalid analysis frame order at {frame_id}")
        previous_id = frame_id
        result = {"frame_id": frame_id, "baseline": row["recommendation"]}
        try:
            board = to_board_state(observation, policy.vocabulary, list(policy.deck.cards))
        except ValueError as exc:
            result.update(status="skipped", reason=str(exc))
        else:
            if hasattr(policy, "dataset_gen"):
                decision = policy.decide(board, accepted_plays=own_plays, opponent_plays=opponent_plays)
            else:
                decision = policy.decide(board, accepted_plays=own_plays)
            result.update(status="play" if decision["play"] else "wait", clashai=decision)
        results.append(result)
    if not results:
        raise ValueError("Session analysis contains no frames")
    evaluated = [row for row in results if row["status"] != "skipped"]
    report = {
        "source_sha256": source_sha256,
        "checkpoint_sha256": policy.checkpoint_sha256,
        "frames": len(results),
        "clashai_counts": dict(Counter(row["status"] for row in results)),
        "baseline_counts_on_compared_frames": dict(Counter(row["baseline"]["status"] for row in evaluated)),
        "play_wait_agreement": sum((row["status"] == "play") == (row["baseline"]["status"] == "suggest")
                                   for row in evaluated),
        "decisions": results,
    }
    if history_report is not None:
        report["tracked_history"] = history_report
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("board", type=Path)
    parser.add_argument("--clashai-root", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--deck", type=Path, help="Deck YAML/name required for a generalist checkpoint, e.g. icebow.")
    parser.add_argument("--costs", type=int, nargs=8, required=True, help="Card costs in checkpoint deck order")
    parser.add_argument("--compare-session", action="store_true", help="Read analyze --session JSONL and compare policies.")
    parser.add_argument("--use-tracked-history", action="store_true",
                        help="Infer approximate past plays from YOLO track births in the complete session.")
    args = parser.parse_args()
    if args.use_tracked_history and not args.compare_session:
        parser.error("--use-tracked-history requires --compare-session")
    try:
        policy = load_clashai_policy(args.clashai_root, args.checkpoint, args.costs, args.deck)
        if args.compare_session:
            result = compare_session(args.board, policy, use_tracked_history=args.use_tracked_history)
        else:
            result = policy.decide(json.loads(args.board.read_text(encoding="utf-8-sig")))
        print(json.dumps(result))
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
