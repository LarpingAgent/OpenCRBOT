from __future__ import annotations

from collections import Counter
from math import hypot

from .clashai_export import _BODY_ALIASES

_IGNORED_UNITS = {"king_tower", "queen_tower", "princess_tower"}


def tracked_play_history(rows: list[dict], policy, *, min_confidence: float = 0.70) -> tuple[list, list[dict], dict]:
    """Infer approximate card-play history from first sightings of tracked public units.

    This is intended for offline comparison. Track births can be missed, split or misclassified,
    and they do not prove that a play was accepted by the game.
    """
    if not 0.0 <= min_confidence <= 1.0:
        raise ValueError("min_confidence must be in [0, 1]")
    if not hasattr(policy, "dataset_gen"):
        raise ValueError("Tracked play history currently supports GenModel FV3 only")

    card_key = policy.dataset_gen.card_key
    gid = policy.gen_policy.gid
    tick_s = float(policy.contract.TICK_S)
    deck_slots = {card_key(name): index for index, name in enumerate(policy.deck.cards)}
    seen_tracks: set[tuple[str, str, int]] = set()
    candidates: list[dict] = []
    skipped = Counter()

    for row in rows:
        observation = row.get("observation", {})
        if observation.get("battle_active", {}).get("value") is not True:
            continue
        elapsed = (observation.get("battle_elapsed_s") or {}).get("value")
        if elapsed is None:
            skipped["elapsed_time_unknown"] += 1
            continue
        tick = round(float(elapsed) / tick_s)
        frame_id = int(row.get("capture", {}).get("frame_id", observation.get("frame_id", 0)))
        for unit in observation.get("units", []):
            owner = unit.get("owner", {}).get("value")
            card = unit.get("card", {}).get("value")
            track_id = unit.get("track_id")
            x = unit.get("x", {}).get("value")
            y = unit.get("y", {}).get("value")
            confidence = float(unit.get("card", {}).get("confidence", 0.0))
            if owner not in ("own", "opponent") or not card or track_id is None:
                skipped["missing_owner_card_or_track"] += 1
                continue
            if confidence < min_confidence or x is None or y is None:
                skipped["low_confidence_or_position"] += 1
                continue

            raw = str(card).lower().replace("-", "_").replace(" ", "_")
            form = 2 if raw.endswith("_hero") else 1 if raw.endswith(("_evo", "_evolution")) else 0
            base = raw.removesuffix("_evolution").removesuffix("_evo").removesuffix("_hero")
            if base in _IGNORED_UNITS or base.endswith("_skill") or base.endswith("_ability"):
                skipped["tower_or_ability_effect"] += 1
                continue
            canonical = _BODY_ALIASES.get(base, base)
            identity = card_key(canonical)
            if not identity or int(gid.get(identity, 0)) <= 0:
                skipped["card_outside_checkpoint_vocabulary"] += 1
                continue
            if owner == "own" and identity not in deck_slots:
                skipped["own_card_outside_configured_deck"] += 1
                continue

            track_key = (owner, identity, int(track_id))
            if track_key in seen_tracks:
                continue
            seen_tracks.add(track_key)
            candidates.append({
                "owner": owner,
                "card": identity,
                "form": form,
                "tick": tick,
                "frame_id": frame_id,
                "x": float(x),
                "y": float(y),
                "track_id": int(track_id),
            })

    candidates.sort(key=lambda item: (item["tick"], item["frame_id"], item["owner"], item["card"]))
    clusters: list[dict] = []
    for item in candidates:
        match = next((cluster for cluster in reversed(clusters)
                      if cluster["owner"] == item["owner"] and cluster["card"] == item["card"]
                      and item["tick"] - cluster["last_tick"] <= 10
                      and hypot(item["x"] - cluster["x"], item["y"] - cluster["y"]) <= 0.10), None)
        if match is None:
            clusters.append({**item, "last_tick": item["tick"], "count": 1})
            continue
        count = match["count"] + 1
        match["x"] = (match["x"] * match["count"] + item["x"]) / count
        match["y"] = (match["y"] * match["count"] + item["y"]) / count
        match["form"] = max(match["form"], item["form"])
        match["last_tick"] = item["tick"]
        match["count"] = count

    own_plays = []
    opponent_plays = []
    for play_index, event in enumerate(clusters):
        if event["owner"] == "own":
            own_plays.append((event["tick"], deck_slots[event["card"]], event["x"], event["y"]))
        else:
            # ClashAI's replay log stores engine-space coordinates; observations use our normalized board frame.
            opponent_plays.append({
                "tick": event["tick"],
                "side": 1,
                "card": event["card"],
                "form": event["form"],
                "x": event["x"] * 18.0,
                "y": (1.0 - event["y"]) * 32.0,
                "accepted": True,
                "inferred": True,
                "play_index": play_index,
            })

    own_plays.sort(key=lambda play: play[0])
    opponent_plays.sort(key=lambda play: (play["tick"], play["play_index"]))
    return own_plays, opponent_plays, {
        "source": "YOLO track first sightings from public screenshots",
        "min_detection_confidence": min_confidence,
        "candidate_track_births": len(candidates),
        "own_inferred_plays": len(own_plays),
        "opponent_inferred_plays": len(opponent_plays),
        "skipped": dict(skipped),
        "use_limitations": [
            "Track births are approximate and can be missed, split, merged or misclassified.",
            "A visible unit does not prove that a corresponding card play was accepted.",
            "Brief spell effects and plays without a detected unit may be absent.",
        ],
    }
