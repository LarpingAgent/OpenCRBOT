from __future__ import annotations

import json
from pathlib import Path


def verify_play_transition(before: dict, after: dict, slot: int, cost: int, elapsed_ms: int) -> dict:
    """Verify one action from the hand-slot replacement and elixir change."""
    if not 0 <= slot < 4 or not 1 <= cost <= 10:
        raise ValueError("Expected a valid hand slot and a card cost from 1 to 10")
    before_hand = [item.get("value") for item in before.get("my_hand", [])]
    after_hand = [item.get("value") for item in after.get("my_hand", [])]
    before_elixir = (before.get("my_elixir") or {}).get("value")
    after_elixir = (after.get("my_elixir") or {}).get("value")
    reasons = []
    if not 0 < elapsed_ms <= 3000:
        reasons.append("frames_outside_verification_window")
    if any((row.get("battle_active") or {}).get("value") is not True
           or (row.get("battle_active") or {}).get("status") == "unknown"
           or (row.get("battle_active") or {}).get("confidence", 0) < 0.5 for row in (before, after)):
        reasons.append("battle_unconfirmed")
    if len(before_hand) != 4 or len(after_hand) != 4 or None in (*before_hand, *after_hand):
        reasons.append("hand_unreadable")
    elif any((row.get("my_hand") or [])[index].get("status") == "unknown"
             or (row.get("my_hand") or [])[index].get("confidence", 0) < 0.5
             for row in (before, after) for index in range(4)):
        reasons.append("hand_low_confidence")
    elif [i for i in range(4) if before_hand[i] != after_hand[i]] != [slot]:
        reasons.append("expected_slot_not_uniquely_replaced")
    drop = None
    before_elixir_evidence = before.get("my_elixir") or {}
    after_elixir_evidence = after.get("my_elixir") or {}
    if (before_elixir is not None and after_elixir is not None
            and before_elixir_evidence.get("status") != "unknown"
            and after_elixir_evidence.get("status") != "unknown"
            and before_elixir_evidence.get("confidence", 0) >= 0.5
            and after_elixir_evidence.get("confidence", 0) >= 0.5):
        try:
            first_elixir, last_elixir = float(before_elixir), float(after_elixir)
            if 0 <= first_elixir <= 10 and 0 <= last_elixir <= 10:
                drop = round(first_elixir - last_elixir, 2)
        except (TypeError, ValueError):
            pass
    if drop is None or not max(0.5, cost - 1.5) <= drop <= cost + 1:
        reasons.append("elixir_drop_inconclusive")
    return {"status": "inconclusive" if reasons else "consistent_with_play", "reasons": reasons,
            "elapsed_ms": elapsed_ms, "slot": slot + 1, "estimated_elixir_drop": drop}


def verify_recorded_play(analysis: Path, before_id: int, after_id: int, slot: int, cost: int) -> dict:
    """Look for one hand replacement and a compatible elixir drop on recorded frames."""
    if not 0 < before_id < after_id or not 0 <= slot < 4 or not 1 <= cost <= 10:
        raise ValueError("Expected ordered positive frame IDs, a valid hand slot and a card cost from 1 to 10")
    selected = {}
    with analysis.open(encoding="utf-8-sig") as stream:
        for line in stream:
            row = json.loads(line)
            frame_id = row["capture"]["frame_id"]
            if frame_id in (before_id, after_id):
                if frame_id in selected or frame_id != row["observation"]["frame_id"]:
                    raise ValueError(f"Duplicate or inconsistent frame {frame_id}")
                selected[frame_id] = row
    if len(selected) != 2:
        raise ValueError("Both frames must be present in one session analysis")
    first, last = selected[before_id], selected[after_id]
    elapsed_ms = last["capture"]["captured_at_monotonic_ms"] - first["capture"]["captured_at_monotonic_ms"]
    result = verify_play_transition(first["observation"], last["observation"], slot, cost, elapsed_ms)
    result.update(before_frame=before_id, after_frame=after_id)
    return result
