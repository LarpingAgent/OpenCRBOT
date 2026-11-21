from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Generic, Literal, TypeVar

T = TypeVar("T")
EvidenceStatus = Literal["observed", "inferred", "unknown"]
Owner = Literal["own", "opponent", "unknown"]
OBSERVATION_SCHEMA = "opencrbot.observation.v1"


@dataclass(frozen=True)
class Evidence(Generic[T]):
    """A state value with its origin, certainty and capture time."""

    value: T | None
    status: EvidenceStatus
    confidence: float
    source: str
    captured_at_ms: int

    def __post_init__(self) -> None:
        if self.status not in ("observed", "inferred", "unknown"):
            raise ValueError(f"unsupported evidence status: {self.status}")
        if self.status == "unknown" and self.value is not None:
            raise ValueError("unknown evidence cannot carry a value")
        if self.status != "unknown" and self.value is None:
            raise ValueError("observed/inferred evidence must carry a value")
        if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be finite and in [0, 1]")
        if self.status == "unknown" and self.confidence != 0.0:
            raise ValueError("unknown evidence must have zero confidence")
        if self.captured_at_ms < 0:
            raise ValueError("captured_at_ms must be non-negative")
        if not self.source:
            raise ValueError("source is required, including for unknown evidence")

    @classmethod
    def unknown(cls, source: str = "not_observed", captured_at_ms: int = 0) -> Evidence[T]:
        return cls(None, "unknown", 0.0, source, captured_at_ms)

    def age_ms(self, now_ms: int) -> int:
        return now_ms - self.captured_at_ms

    def is_fresh(self, now_ms: int, max_age_ms: int) -> bool:
        age = self.age_ms(now_ms)
        return self.status != "unknown" and 0 <= age <= max_age_ms


@dataclass(frozen=True)
class DetectedUnit:
    card: Evidence[str]
    owner: Evidence[Owner]
    x: Evidence[float]
    y: Evidence[float]
    detection_id: str
    track_id: int | None = None

    def __post_init__(self) -> None:
        for axis in (self.x, self.y):
            if axis.value is not None and not 0.0 <= axis.value <= 1.0:
                raise ValueError("unit coordinates must use normalized [0, 1] board space")
        if not self.detection_id:
            raise ValueError("detection_id is required")


@dataclass(frozen=True)
class TowerObservation:
    side: Literal["own", "opponent"]
    kind: Literal["king", "princess"]
    lane: Literal["L", "R"] | None
    alive: Evidence[bool]
    hp_frac: Evidence[float]


@dataclass(frozen=True)
class PublicObservation:
    """Versioned, screen/public-state contract shared by perception and policy."""

    frame_id: int
    serial: str
    captured_at_ms: int
    screen_width: int | None
    screen_height: int | None
    battle_clock_s: Evidence[float]
    battle_active: Evidence[bool]
    my_elixir: Evidence[float]
    opponent_elixir: Evidence[float]
    my_hand: tuple[Evidence[str], ...] = ()
    units: tuple[DetectedUnit, ...] = ()
    public_only: bool = True
    schema: str = OBSERVATION_SCHEMA
    towers: tuple[TowerObservation, ...] = ()
    battle_elapsed_s: Evidence[float] | None = None
    my_next: Evidence[str] | None = None

    def __post_init__(self) -> None:
        if self.schema != OBSERVATION_SCHEMA:
            raise ValueError(f"unsupported observation schema: {self.schema}")
        if self.frame_id < 0 or self.captured_at_ms < 0:
            raise ValueError("frame_id and captured_at_ms must be non-negative")
        if not self.serial:
            raise ValueError("serial is required")
        if (self.screen_width is None) != (self.screen_height is None):
            raise ValueError("screen dimensions must both be present or both be absent")
        if self.screen_width is not None and self.screen_height is not None:
            if self.screen_width <= 0 or self.screen_height <= 0:
                raise ValueError("screen dimensions must be positive")
        if self.my_hand and len(self.my_hand) != 4:
            raise ValueError("hand must be empty or contain exactly four slots")
        if not self.public_only:
            raise ValueError("the baseline observation contract accepts public state only")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ActionProposal:
    """A policy suggestion; it is not an instruction to touch the device."""

    frame_id: int
    card_slot: int
    target_x: float
    target_y: float
    confidence: float
    reason: str
    expected_card: str
    elixir_cost: int

    def __post_init__(self) -> None:
        if self.frame_id < 0:
            raise ValueError("frame_id must be non-negative")
        if not 0 <= self.card_slot < 4:
            raise ValueError("card_slot must be in [0, 3]")
        if not (math.isfinite(self.target_x) and math.isfinite(self.target_y)):
            raise ValueError("target must be finite")
        if not (0.0 <= self.target_x <= 1.0 and 0.0 <= self.target_y <= 1.0):
            raise ValueError("target must use normalized [0, 1] board space")
        if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be finite and in [0, 1]")
        if not self.reason.strip():
            raise ValueError("reason is required")
        if not self.expected_card.strip():
            raise ValueError("expected_card is required")
        if type(self.elixir_cost) is not int or not 1 <= self.elixir_cost <= 10:
            raise ValueError("elixir_cost must be between 1 and 10")


@dataclass(frozen=True)
class GateResult:
    accepted: bool
    reasons: tuple[str, ...]


def check_action_proposal(
    proposal: ActionProposal,
    observation: PublicObservation,
    *,
    now_ms: int,
    max_age_ms: int = 500,
    min_confidence: float = 0.85,
) -> GateResult:
    """Fail-closed validation for a future action adapter; never dispatches input."""
    if max_age_ms < 0:
        raise ValueError("max_age_ms must be non-negative")
    if not math.isfinite(min_confidence) or not 0.0 <= min_confidence <= 1.0:
        raise ValueError("min_confidence must be finite and in [0, 1]")
    reasons: list[str] = []
    if proposal.frame_id != observation.frame_id:
        reasons.append("proposal_frame_mismatch")
    observation_age_ms = now_ms - observation.captured_at_ms
    if observation_age_ms < 0:
        reasons.append("observation_timestamp_in_future")
    elif observation_age_ms > max_age_ms:
        reasons.append("observation_stale")
    if (observation.battle_active.value is not True
            or observation.battle_active.confidence < min_confidence
            or not observation.battle_active.is_fresh(now_ms, max_age_ms)):
        reasons.append("battle_unconfirmed_or_stale")
    if proposal.confidence < min_confidence:
        reasons.append("proposal_confidence_low")
    if len(observation.my_hand) != 4:
        reasons.append("hand_unavailable")
    elif not observation.my_hand[proposal.card_slot].is_fresh(now_ms, max_age_ms):
        reasons.append("card_slot_unavailable_or_stale")
    elif observation.my_hand[proposal.card_slot].status != "observed":
        reasons.append("card_slot_not_observed")
    elif observation.my_hand[proposal.card_slot].confidence < min_confidence:
        reasons.append("card_slot_confidence_low")
    elif observation.my_hand[proposal.card_slot].value != proposal.expected_card:
        reasons.append("card_slot_changed")
    if (observation.my_elixir.status != "observed"
            or observation.my_elixir.confidence < min_confidence
            or not observation.my_elixir.is_fresh(now_ms, max_age_ms)):
        reasons.append("elixir_unavailable_or_stale")
    else:
        elixir = observation.my_elixir.value
        if elixir is None or not math.isfinite(elixir) or not 0 <= elixir <= 10:
            reasons.append("elixir_invalid")
        elif elixir < proposal.elixir_cost:
            reasons.append("insufficient_elixir")
    return GateResult(not reasons, tuple(reasons))
