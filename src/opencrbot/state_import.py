from __future__ import annotations

from .contracts import DetectedUnit, Evidence, PublicObservation


def _in_range(value, maximum: float) -> bool:
    return type(value) in (int, float) and 0 <= value <= maximum


def import_state(snapshot: dict, catalog: list | dict, *, format: str) -> PublicObservation:
    """Convert one offline snapshot into the public fields OpenCRBOT understands."""
    memory = format == "cr-mem"
    if not isinstance(snapshot, dict):
        raise ValueError("Snapshot must be a JSON object.")
    if memory:
        if snapshot.get("event") != "runtime_snapshot" or not isinstance(snapshot.get("entities"), list):
            raise ValueError("Expected a cr-mem runtime_snapshot with an entities list.")
        if not isinstance(catalog, dict) or not isinstance(catalog.get("items"), list):
            raise ValueError("Expected the cr-mem cards.json catalog.")
        names = {entry["id"]: entry["name"] for entry in catalog["items"]
                 if isinstance(entry, dict) and type(entry.get("id")) is int
                 and isinstance(entry.get("name"), str)}
    else:
        if not isinstance(snapshot.get("my_hand"), list) or not isinstance(snapshot.get("units"), list):
            raise ValueError("Expected a ClashAI BoardState with my_hand and units lists.")
        if not isinstance(catalog, list) or not all(isinstance(name, str) for name in catalog):
            raise ValueError("Expected ClashAI UNIT_VOCAB as a JSON array of names.")
        names = dict(enumerate(catalog))

    source = f"offline:{format}"
    # Reader timestamps are wall-clock milliseconds, not live monotonic timestamps.
    def evidence(value, confidence: float = 1.0):
        if value is None:
            return Evidence.unknown(source)
        return Evidence(value, "inferred", confidence, source, 0)

    def card(card_id):
        name = names.get(card_id) if type(card_id) is int else None
        if name is None:
            return None
        key = name.lower().replace(".", "").replace("_", "-").replace(" ", "-")
        return {"minipekka": "mini-pekka", "hogrider": "hog-rider"}.get(key, key)

    hand = [evidence(None) for _ in range(4)]
    if memory:
        hand_rows = snapshot.get("hand", [])
        if not isinstance(hand_rows, list):
            raise ValueError("cr-mem hand must be a list.")
        for row in hand_rows:
            if not isinstance(row, dict):
                continue
            slot = row.get("slot")
            if type(slot) is int and 0 <= slot < 4:
                hand[slot] = evidence(card(row.get("data_id")))
    else:
        for slot, card_id in enumerate(snapshot["my_hand"][:4]):
            hand[slot] = evidence(card(card_id))

    units = []
    for index, row in enumerate(snapshot["entities" if memory else "units"]):
        if not isinstance(row, dict):
            continue
        if memory and row.get("kind") in (12, 13):
            continue
        x, y = row.get("x"), row.get("y")
        if memory and _in_range(x, 18000) and _in_range(y, 32000):
            x, y = x / 18000, 1 - y / 32000
        elif memory or not (_in_range(x, 1) and _in_range(y, 1)):
            continue
        side = row.get("side")
        owner = {0: "own", 1: "opponent"}.get(side) if type(side) is int else None
        confidence = 1.0 if memory else row.get("conf", 0.0)
        if not _in_range(confidence, 1):
            continue
        units.append(DetectedUnit(evidence(card(row.get("card_id" if memory else "cls")), confidence),
                                  evidence(owner, confidence), evidence(x, confidence),
                                  evidence(y, confidence), f"{format}:{index}"))

    active = snapshot.get("battle_active") if memory else True
    clock = snapshot.get("battle_clock" if memory else "t_sec")
    elixir = snapshot.get("own_elixir" if memory else "my_elixir")
    return PublicObservation(
        frame_id=0, serial=source, captured_at_ms=0, screen_width=None, screen_height=None,
        battle_active=evidence(active if type(active) is bool else None),
        battle_clock_s=evidence(clock if _in_range(clock, 600) else None),
        my_elixir=evidence(elixir if _in_range(elixir, 10) else None),
        opponent_elixir=evidence(None), my_hand=tuple(hand), units=tuple(units),
    )
