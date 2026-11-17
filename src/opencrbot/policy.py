from __future__ import annotations

from dataclasses import asdict, dataclass

from .contracts import PublicObservation

POLICY_SCHEMA = "opencrbot.recommendation.v1"

# Small, explicit card catalog used only for a human-readable defensive hint.
# It is intentionally not a full Clash Royale rules engine.
CARD_COST = {
    "giant": 5, "knight": 3, "mini-pekka": 4, "cannon": 3,
    "musketeer": 4, "witch": 5, "minions": 3, "fireball": 4,
    "valkyrie": 4, "archers": 3, "arrows": 3,
    "spear-goblins": 2,
}
AIR_THREATS = {
    "minion", "minion-horde", "mega-minion", "bat", "balloon", "lava-hound",
    "baby-dragon", "inferno-dragon", "phoenix-big", "phoenix-small", "flying-machine",
}
TANK_THREATS = {
    "giant", "golem", "pekka", "mega-knight", "royal-giant", "electro-giant",
    "goblin-giant", "lava-hound", "giant-skeleton", "hog", "hog-rider", "balloon",
}
AIR_COUNTERS = {"musketeer", "minions", "witch", "archers", "spear-goblins"}
TANK_COUNTERS = {"mini-pekka", "cannon", "musketeer", "knight", "valkyrie"}
GENERAL_COUNTERS = {"knight", "valkyrie", "musketeer", "mini-pekka", "cannon", "minions", "archers"}


@dataclass(frozen=True)
class Recommendation:
    schema: str
    status: str
    card_slot: int | None
    card: str | None
    target_x: float | None
    target_y: float | None
    confidence: float
    reason: str
    executable: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def recommend(observation: PublicObservation) -> Recommendation:
    """Return at most one conservative defensive hint; this never dispatches input."""
    if observation.battle_active.value is not True:
        return Recommendation(POLICY_SCHEMA, "wait", None, None, None, None, 0.0,
                              "Battle screen was not recognized; no recommendation is produced.")
    empty = Recommendation(POLICY_SCHEMA, "wait", None, None, None, None, 0.0,
                           "No reliable public threat and playable hand evidence; wait for a human review.")
    threats = []
    for unit in observation.units:
        if (unit.owner.value == "opponent" and unit.owner.status != "unknown"
                and unit.card.value is not None and unit.y.value is not None
                and unit.y.value >= 0.50 and unit.card.confidence >= 0.60):
            threats.append(unit)
    if not threats:
        return empty

    # Choose the threat closest to our tower (larger board y means closer to own side).
    threat = max(threats, key=lambda u: float(u.y.value or 0.0))
    threat_name = str(threat.card.value).lower()
    pool = AIR_COUNTERS if threat_name in AIR_THREATS else TANK_COUNTERS if threat_name in TANK_THREATS else GENERAL_COUNTERS
    available = []
    for slot, evidence in enumerate(observation.my_hand):
        if evidence.status == "unknown" or evidence.value is None:
            continue
        card = str(evidence.value).lower()
        cost = CARD_COST.get(card)
        if cost is None or card not in pool:
            continue
        if observation.my_elixir.value is not None and float(observation.my_elixir.value) < cost:
            continue
        # Similarity and YOLO score are domain-unvalidated, so this remains a suggestion only.
        score = min(evidence.confidence, threat.card.confidence,
                    observation.my_elixir.confidence if observation.my_elixir.value is not None else 0.0,
                    0.70)
        available.append((cost, -score, slot, card, score))
    if not available:
        return Recommendation(POLICY_SCHEMA, "wait", None, None, None, None, 0.0,
                              f"Visible opponent {threat_name} is in our half, but no known affordable response was read.")

    _, _, slot, card, score = min(available)
    target_x = min(0.92, max(0.08, float(threat.x.value or 0.5)))
    target_y = min(0.88, max(0.56, float(threat.y.value or 0.56) + 0.08))
    return Recommendation(
        POLICY_SCHEMA, "suggest", slot, card, round(target_x, 4), round(target_y, 4), round(score, 3),
        f"Defensive hint: {threat_name} was detected on our half; {card} is the lowest-cost known counter in hand. Review the frame and target manually.",
        executable=False,
    )
