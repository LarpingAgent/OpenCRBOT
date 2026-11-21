from __future__ import annotations

import math


# The detector labels individual bodies while ClashAI often names the card.
_BODY_ALIASES = {
    "archer": "archers",
    "barbarian": "barbarians",
    "bat": "bats",
    "elite_barbarian": "elite_barbarians",
    "goblin": "goblins",
    "guard": "guards",
    "hog": "hog_rider",
    "lava_pup": "lava_pups",
    "minion": "minions",
    "royal_hog": "royal_hogs",
    "skeleton": "skeletons",
    "skeleton_dragon": "skeleton_dragons",
    "spear_goblin": "spear_goblins",
    "wall_breaker": "wall_breakers",
    "zappy": "zappies",
}


def to_board_state(observation: dict, vocabulary: list[str], deck: list[str]) -> dict:
    """Convert a projected, regulation-time OpenCRBOT observation to ClashAI BoardState JSON."""
    def key(name: str) -> str:
        return name.lower().replace("-", "_").replace(" ", "_")

    def base_key(name: str) -> str:
        result = key(name)
        for suffix in ("_evolution", "_evo", "_hero", "_ability", "_aoe"):
            result = result.removesuffix(suffix)
        return result

    ids = {key(name): i for i, name in enumerate(vocabulary)}
    if len(deck) != 8 or len({key(name) for name in deck}) != 8:
        raise ValueError("ClashAI requires eight distinct deck cards")
    if any(key(name) not in ids for name in deck):
        raise ValueError("Deck contains a card outside the ClashAI vocabulary")
    deck_ids = [ids[key(name)] for name in deck]
    deck_ids_by_base = {base_key(name): cls_id for name, cls_id in zip(deck, deck_ids)}
    if observation.get("battle_active", {}).get("value") is not True:
        raise ValueError("BoardState requires an active match")
    elapsed = (observation.get("battle_elapsed_s") or {}).get("value")
    elixir = observation.get("my_elixir", {}).get("value")
    if elapsed is None or not math.isfinite(elapsed) or not 0 <= elapsed < 180:
        raise ValueError("BoardState requires inferred or observed regulation elapsed seconds")
    if elixir is None or not math.isfinite(elixir) or not 0 <= elixir <= 10:
        raise ValueError("BoardState requires own elixir")

    hand = [ids.get(key(slot.get("value") or ""), -1) for slot in observation.get("my_hand", [])]
    if len(hand) != 4:
        raise ValueError("BoardState requires four hand slots")
    hand = [card if card in deck_ids else deck_ids_by_base.get(base_key(slot.get("value") or ""), -1)
            for card, slot in zip(hand, observation.get("my_hand", []))]
    next_evidence = observation.get("my_next") or {}
    next_name = next_evidence.get("value") if next_evidence.get("status") != "unknown" else None
    my_next = ids.get(key(next_name), -1) if next_name else -1
    if my_next not in deck_ids and next_name:
        my_next = deck_ids_by_base.get(base_key(next_name), -1)

    towers = []
    side_ids = {"own": 0, "opponent": 1}
    observed_towers = {(tower["side"], tower["kind"], tower["lane"]): tower
                       for tower in observation.get("towers", [])}
    for side, side_id in side_ids.items():
        for kind, lane in (("king", None), ("princess", "L"), ("princess", "R")):
            tower = observed_towers.get((side, kind, lane), {})
            alive = tower.get("alive", {}).get("value")
            if type(alive) is not bool:
                raise ValueError(f"Tower status unknown: {side} {kind} {lane or ''}".rstrip())
            hp = tower.get("hp_frac", {}).get("value") if alive else 0.0
            towers.append(dict(side=side_id, kind=kind, lane=lane, hp_frac=hp, alive=alive))

    units, spells = [], []
    for unit in observation.get("units", []):
        card = unit["card"]
        name = key(card.get("value") or "")
        form = 1 if name.endswith(("_evo", "_evolution")) else 2 if name.endswith("_hero") else 0
        if name not in ids:
            base = base_key(name)
            canonical = _BODY_ALIASES.get(base, base)
            name = canonical + ("_evo" if form == 1 else "_hero" if form == 2 else "")
        if name not in ids or name in {"king_tower", "queen_tower", "princess_tower"}:
            continue
        x, y = unit["x"], unit["y"]
        if any(axis.get("source") != "yolo.projected_ground_estimate" for axis in (x, y)):
            raise ValueError("Unit positions must use projected board coordinates")
        if x.get("value") is None or y.get("value") is None:
            continue
        target = spells if name.endswith(("_aoe", "_ability")) else units
        target.append(dict(cls=ids[name], side=side_ids.get(unit["owner"].get("value"), -1),
                           x=x["value"], y=y["value"], hp_frac=None, deploying=None, age_sec=None,
                           conf=card["confidence"], form=form))

    return dict(source="live", t_sec=elapsed, t_source="timer", double_elixir=elapsed >= 120,
                overtime=False, my_elixir=elixir, my_elixir_exact=False, opp_elixir=None,
                my_hand=hand, my_next=my_next, towers=towers, units=units, spells=spells, deck=deck_ids)
