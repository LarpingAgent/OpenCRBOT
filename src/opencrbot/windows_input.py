from __future__ import annotations

from dataclasses import dataclass
import math
import sys
import time

from .contracts import ActionProposal, PublicObservation
from .vision import Calibration
from .windows_capture import window_frame_bounds

TRAINING_CAMP_CONFIRMATION = "PLAY TRAINING CAMP"


class ActionInputError(RuntimeError):
    """Raised when a confirmed action cannot be safely dispatched."""


@dataclass(frozen=True)
class ActionPlan:
    window_handle: int
    window_bounds: tuple[int, int, int, int]
    frame_id: int
    card_slot: int
    card: str
    card_point: tuple[int, int]
    target_point: tuple[int, int]
    confirmation: str

    def __post_init__(self) -> None:
        left, top, right, bottom = self.window_bounds
        if self.window_handle <= 0 or right <= left or bottom <= top:
            raise ValueError("Action plan requires a valid window handle and bounds")
        if self.frame_id < 0 or not 0 <= self.card_slot < 4:
            raise ValueError("Action plan requires a valid frame and hand slot")
        if self.confirmation != TRAINING_CAMP_CONFIRMATION:
            raise ValueError("Action plan requires the exact Training Camp confirmation")
        for point in (self.card_point, self.target_point):
            if not left <= point[0] < right or not top <= point[1] < bottom:
                raise ValueError("Action plan point is outside the selected window")


def focus_game_window(handle: int) -> None:
    """Bring the explicitly selected, visible game window to the foreground."""
    if sys.platform != "win32":
        raise ActionInputError("Windows input is unavailable on this platform")
    from ctypes import WinDLL, c_void_p

    user32 = WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = c_void_p
    user32.IsWindow.argtypes = (c_void_p,)
    user32.IsWindowVisible.argtypes = (c_void_p,)
    user32.IsIconic.argtypes = (c_void_p,)
    user32.SetForegroundWindow.argtypes = (c_void_p,)
    if not user32.IsWindow(handle) or not user32.IsWindowVisible(handle) or user32.IsIconic(handle):
        raise ActionInputError("game_window_unavailable")
    if user32.GetForegroundWindow() != handle:
        user32.SetForegroundWindow(handle)
        time.sleep(0.1)
    if user32.GetForegroundWindow() != handle:
        raise ActionInputError("could_not_focus_game_window")


def plan_confirmed_action(
    proposal: ActionProposal,
    observation: PublicObservation,
    calibration: Calibration,
    *,
    window_handle: int,
    window_bounds: tuple[int, int, int, int],
    now_ms: int,
    confirmation: str,
    max_age_ms: int = 1000,
) -> ActionPlan:
    """Build one manually confirmed GPGPC click plan without sending input."""
    if confirmation != TRAINING_CAMP_CONFIRMATION:
        raise ActionInputError("Training Camp confirmation phrase did not match")
    if max_age_ms < 0:
        raise ValueError("max_age_ms must be non-negative")
    if proposal.frame_id != observation.frame_id:
        raise ActionInputError("proposal_frame_mismatch")
    if not 0 <= proposal.card_slot < 4:
        raise ActionInputError("card_slot_out_of_range")
    if not 1 <= proposal.elixir_cost <= 10:
        raise ActionInputError("card_cost_out_of_range")
    age_ms = now_ms - observation.captured_at_ms
    if not 0 <= age_ms <= max_age_ms:
        raise ActionInputError("observation_stale")
    if (observation.battle_active.value is not True or observation.battle_active.confidence < 0.5
            or not observation.battle_active.is_fresh(now_ms, max_age_ms)):
        raise ActionInputError("battle_unconfirmed")
    elapsed = observation.battle_elapsed_s
    if (elapsed is None or elapsed.value is None or elapsed.status == "unknown"
            or elapsed.confidence < 0.5 or not elapsed.is_fresh(now_ms, max_age_ms)
            or not math.isfinite(float(elapsed.value)) or not 0 <= float(elapsed.value) < 180):
        raise ActionInputError("regulation_clock_unconfirmed")
    next_card = observation.my_next
    if (next_card is None or not next_card.value or next_card.confidence < 0.72
            or not next_card.is_fresh(now_ms, max_age_ms)):
        raise ActionInputError("next_card_unconfirmed")
    if len(observation.my_hand) != 4:
        raise ActionInputError("hand_unavailable")
    selected = observation.my_hand[proposal.card_slot]
    if (selected.value != proposal.expected_card or selected.status == "unknown"
            or selected.confidence < 0.55 or not selected.is_fresh(now_ms, max_age_ms)):
        raise ActionInputError("selected_card_unconfirmed")
    elixir = observation.my_elixir
    if (elixir.value is None or elixir.status == "unknown" or elixir.confidence < 0.5
            or not elixir.is_fresh(now_ms, max_age_ms)
            or not math.isfinite(float(elixir.value)) or not 0 <= float(elixir.value) <= 10):
        raise ActionInputError("elixir_unconfirmed")
    if float(elixir.value) < proposal.elixir_cost:
        raise ActionInputError("insufficient_elixir")
    if calibration.board_bounds is None:
        raise ActionInputError("board_bounds_unavailable")
    if observation.screen_width is None or observation.screen_height is None:
        raise ActionInputError("screen_dimensions_unavailable")
    left, top, right, bottom = window_bounds
    width, height = right - left, bottom - top
    if width <= 1 or height <= 1:
        raise ActionInputError("window_bounds_invalid")
    if width != observation.screen_width or height != observation.screen_height:
        raise ActionInputError("window_size_changed_since_capture")

    x0, y0, x1, y1 = calibration.board_bounds
    if not (0 <= proposal.target_x <= 1 and 0 <= proposal.target_y <= 1):
        raise ActionInputError("target_outside_board_space")
    screen_x = x0 + proposal.target_x * (x1 - x0)
    board_y = 1 - proposal.target_y if calibration.board_y_flipped else proposal.target_y
    screen_y = y0 + board_y * (y1 - y0)
    if not (x0 <= screen_x <= x1 and y0 <= screen_y <= y1):
        raise ActionInputError("target_outside_calibrated_board")

    if len(calibration.hand_boxes) != 4:
        raise ActionInputError("hand_boxes_unavailable")
    hand_x0, hand_y0, hand_x1, hand_y1 = calibration.hand_boxes[proposal.card_slot]
    card_x = (hand_x0 + hand_x1) / 2
    card_y = (hand_y0 + hand_y1) / 2
    card_point = (left + round(card_x * (width - 1)), top + round(card_y * (height - 1)))
    target_point = (left + round(screen_x * (width - 1)), top + round(screen_y * (height - 1)))
    return ActionPlan(
        window_handle=window_handle,
        window_bounds=window_bounds,
        frame_id=observation.frame_id,
        card_slot=proposal.card_slot,
        card=str(proposal.expected_card),
        card_point=card_point,
        target_point=target_point,
        confirmation=confirmation,
    )


def dispatch_confirmed_action(plan: ActionPlan, *, delay_s: float = 0.15) -> dict:
    """Select a card and place it by left-clicking only the confirmed visible window."""
    if sys.platform != "win32":
        raise ActionInputError("Windows input is unavailable on this platform")
    if not 0 <= delay_s <= 1:
        raise ValueError("delay_s must be between 0 and 1 seconds")

    from ctypes import WinDLL, c_int, c_size_t, c_void_p
    from ctypes.wintypes import DWORD

    user32 = WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = c_void_p
    user32.IsWindow.argtypes = (c_void_p,)
    user32.IsWindowVisible.argtypes = (c_void_p,)
    user32.IsIconic.argtypes = (c_void_p,)
    user32.SetForegroundWindow.argtypes = (c_void_p,)
    user32.SetCursorPos.argtypes = (c_int, c_int)
    user32.mouse_event.argtypes = (DWORD, DWORD, DWORD, DWORD, c_size_t)

    set_dpi_context = getattr(user32, "SetThreadDpiAwarenessContext", None)
    if set_dpi_context is None:
        raise ActionInputError("per_monitor_dpi_input_unavailable")
    set_dpi_context.argtypes = (c_void_p,)
    set_dpi_context.restype = c_void_p
    previous_dpi_context = set_dpi_context(c_void_p(-4))  # PER_MONITOR_AWARE_V2
    if not previous_dpi_context:
        raise ActionInputError("per_monitor_dpi_context_unavailable")

    handle = plan.window_handle
    try:
        if not user32.IsWindow(handle) or not user32.IsWindowVisible(handle) or user32.IsIconic(handle):
            raise ActionInputError("game_window_unavailable")
        focus_game_window(handle)

        def ensure_target_window() -> tuple[int, int, int, int]:
            if user32.GetForegroundWindow() != handle:
                raise ActionInputError("foreground_window_changed")
            bounds = window_frame_bounds(handle)
            if bounds != plan.window_bounds:
                raise ActionInputError("window_moved_or_resized_since_confirmation")
            return bounds

        def click(point: tuple[int, int]) -> None:
            ensure_target_window()
            if not user32.SetCursorPos(*point):
                raise ActionInputError("cursor_position_rejected")
            user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
            time.sleep(0.04)
            user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP

        ensure_target_window()
        click(plan.card_point)
        time.sleep(delay_s)
        try:
            click(plan.target_point)
        except Exception as exc:
            raise ActionInputError(f"card_selected_but_placement_not_sent: {exc}") from exc
        return {
            "status": "input_sent",
            "frame_id": plan.frame_id,
            "card_slot": plan.card_slot,
            "card": plan.card,
            "card_point": list(plan.card_point),
            "target_point": list(plan.target_point),
            "confirmation": plan.confirmation,
        }
    finally:
        set_dpi_context(previous_dpi_context)
