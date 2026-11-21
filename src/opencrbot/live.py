from __future__ import annotations

import hashlib
import json
from pathlib import Path
from queue import Empty, Queue
import sys
from threading import Thread
import time

from collections.abc import Callable

from .contracts import ActionProposal, PublicObservation
from .play_verification import verify_play_transition
from .policy import Recommendation
from .vision import Calibration, VisionProvider, decode_image
from .windows_capture import capture_window, window_frame_bounds
from .windows_input import (
    TRAINING_CAMP_CONFIRMATION,
    dispatch_confirmed_action,
    focus_game_window,
    plan_confirmed_action,
)

MAX_HINT_AGE_MS = 1000
_CONFIRMATION_MAX_AGE_MS = 15_000


def _fresh_next_card(observation: PublicObservation, now_ms: int, max_age_ms: int = MAX_HINT_AGE_MS) -> bool:
    evidence = observation.my_next
    return bool(evidence and evidence.value and evidence.confidence >= 0.72
                and evidence.is_fresh(now_ms, max_age_ms))


def _same_action(first: Recommendation, second: Recommendation) -> bool:
    if (first.status != "suggest" or second.status != "suggest"
            or first.card_slot != second.card_slot or first.card != second.card
            or first.target_x is None or first.target_y is None
            or second.target_x is None or second.target_y is None):
        return False
    return abs(first.target_x - second.target_x) <= 0.03 and abs(first.target_y - second.target_y) <= 0.03


def _cost_key(name: str) -> str:
    return "".join(character for character in name.casefold() if character.isalnum())


def _review_match_end(output_dir: Path, stamp: int, frame_id: int, source: bytes,
                      digest: str, inactive_frame_ids: list[int]) -> dict:
    final_frame = output_dir / f"result-frame-{stamp}-{frame_id:06d}.png"
    final_frame.write_bytes(source)
    try:
        typed_result = input(
            "Battle screen inactive for 3 frames. Review the saved result image and type WIN, LOSS, DRAW, or UNKNOWN: "
        )
    except EOFError:
        typed_result = "UNKNOWN"
    operator_result = typed_result.strip().upper()
    if operator_result not in ("WIN", "LOSS", "DRAW", "UNKNOWN"):
        operator_result = "UNKNOWN"
    print(f"Battle end recorded as {operator_result}: {final_frame.resolve()}", flush=True)
    return {
        "source": "operator_review_of_captured_image",
        "value": operator_result,
        "image": str(final_frame),
        "sha256": digest,
        "inactive_frame_ids": inactive_frame_ids[-3:],
    }


def _read_confirmation(prompt: str, timeout_s: float) -> tuple[str, str | None]:
    """Read an interactive response with a bounded wait; callers stop on timeout."""
    if not sys.stdin.isatty():
        return "unavailable", None

    responses: Queue[tuple[str, str | None]] = Queue(maxsize=1)

    def read_line() -> None:
        try:
            responses.put(("line", input()))
        except (EOFError, OSError, ValueError):
            responses.put(("unavailable", None))

    print(prompt, end="", flush=True)
    Thread(target=read_line, daemon=True).start()
    try:
        return responses.get(timeout=timeout_s)
    except Empty:
        print("\nConfirmation timed out; stopping without playing a card.", flush=True)
        return "timeout", None


def _confirmed_action(handle: int, serial: str, provider: VisionProvider, calibration: Calibration,
                      recommend: Callable[[PublicObservation], Recommendation], card_costs: dict[str, int],
                      observation: PublicObservation, recommendation: Recommendation, source: bytes,
                      overlay, output_dir: Path, actions_path: Path, run_stamp: int,
                      frame_id: int) -> tuple[dict, int, str | None, bool]:
    """Ask before one play, refresh it, dispatch once, then verify the transition."""
    if (recommendation.card_slot is None or recommendation.card is None
            or recommendation.target_x is None or recommendation.target_y is None):
        return ({"status": "malformed_recommendation", "frame_id": observation.frame_id},
                frame_id, None, True)
    preview = output_dir / f"preview-frame-{run_stamp}-{frame_id:06d}.png"
    overlay_path = output_dir / f"preview-overlay-{run_stamp}-{frame_id:06d}.png"
    try:
        preview.write_bytes(source)
        preview_overlay = overlay.copy()
        cv2 = provider.cv2
        height, width = preview_overlay.shape[:2]
        if calibration.board_bounds is not None:
            x0, y0, x1, y1 = calibration.board_bounds
            target_x = x0 + recommendation.target_x * (x1 - x0)
            target_y_norm = 1 - recommendation.target_y if calibration.board_y_flipped else recommendation.target_y
            target_y = y0 + target_y_norm * (y1 - y0)
            point = (round(target_x * (width - 1)), round(target_y * (height - 1)))
            cv2.drawMarker(preview_overlay, point, (255, 0, 255), cv2.MARKER_CROSS, 30, 3)
            cv2.circle(preview_overlay, point, 10, (255, 255, 255), 2)
            cv2.putText(preview_overlay, "PLACEMENT", (max(4, point[0] - 86), max(24, point[1] - 18)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 0, 255), 2, cv2.LINE_AA)
        if len(calibration.hand_boxes) == 4:
            hx0, hy0, hx1, hy1 = calibration.hand_boxes[recommendation.card_slot]
            box = (round(hx0 * (width - 1)), round(hy0 * (height - 1)),
                   round(hx1 * (width - 1)), round(hy1 * (height - 1)))
            cv2.rectangle(preview_overlay, box[:2], box[2:], (0, 255, 255), 3)
        cv2.putText(preview_overlay,
                    f"CONFIRM SLOT {recommendation.card_slot + 1}: {recommendation.card}  "
                    f"TARGET ({recommendation.target_x:.3f}, {recommendation.target_y:.3f})",
                    (12, 76), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 255, 255), 2, cv2.LINE_AA)
        if not cv2.imwrite(str(overlay_path), preview_overlay):
            raise OSError(f"could not save action preview: {overlay_path}")
    except Exception as exc:
        return ({"status": "preview_error", "error": str(exc), "frame_id": observation.frame_id},
                frame_id, None, True)
    print(f"Action preview: {overlay_path.resolve()}", flush=True)
    print(f"Suggested: slot {recommendation.card_slot + 1} {recommendation.card} at "
          f"({recommendation.target_x:.3f}, {recommendation.target_y:.3f})", flush=True)
    with actions_path.open("a", encoding="utf-8") as action_stream:
        action_stream.write(json.dumps({
            "status": "confirmation_requested",
            "frame_id": observation.frame_id,
            "card_slot": recommendation.card_slot,
            "card": recommendation.card,
            "target": [recommendation.target_x, recommendation.target_y],
            "preview": str(preview),
            "overlay_preview": str(overlay_path),
            "timeout_s": _CONFIRMATION_MAX_AGE_MS / 1000,
        }, ensure_ascii=False) + "\n")
    confirmation_status, typed = _read_confirmation(
        f'Type {TRAINING_CAMP_CONFIRMATION!r} to execute this one play, or press Enter to skip '
        f'({int(_CONFIRMATION_MAX_AGE_MS / 1000)} seconds; timeout stops the run): ',
        _CONFIRMATION_MAX_AGE_MS / 1000,
    )
    if confirmation_status == "timeout":
        return ({"status": "confirmation_timeout", "preview": str(preview),
                 "frame_id": observation.frame_id}, frame_id, None, True)
    if confirmation_status != "line":
        return ({"status": "confirmation_unavailable", "preview": str(preview),
                 "frame_id": observation.frame_id}, frame_id, None, True)
    assert typed is not None
    if typed != TRAINING_CAMP_CONFIRMATION:
        return ({"status": "rejected_by_operator", "preview": str(preview),
                 "frame_id": observation.frame_id}, frame_id, None, False)

    input_attempted = False
    sent = None
    try:
        focus_game_window(handle)
        bounds_before_capture = window_frame_bounds(handle)
        before_source = capture_window(handle)
        bounds_after_capture = window_frame_bounds(handle)
        if bounds_before_capture != bounds_after_capture:
            return ({"status": "cancelled_after_refresh", "reason": "window_moved_during_capture",
                     "frame_id": observation.frame_id}, frame_id, None, False)
        before_digest = hashlib.sha256(before_source).hexdigest()
        before_frame = decode_image(before_source)
        before_observation, before_overlay, metadata = provider.analyze(
            before_frame, serial=serial, frame_id=frame_id + 1,
        )
        frame_id += 1
        refreshed = recommend(before_observation)
        if not _same_action(recommendation, refreshed):
            return ({"status": "cancelled_after_refresh", "reason": "recommendation_changed",
                     "frame_id": observation.frame_id, "refreshed_frame_id": frame_id,
                     "refreshed_recommendation": refreshed.to_dict(), "asset_provenance": metadata.get("asset_provenance"),
                     "preview": str(preview), "overlay_preview": str(overlay_path)},
                    frame_id, before_digest, False)
        confirmation_age_ms = before_observation.captured_at_ms - observation.captured_at_ms
        if not 0 <= confirmation_age_ms <= _CONFIRMATION_MAX_AGE_MS:
            return ({"status": "cancelled_after_refresh", "reason": "confirmation_expired",
                     "frame_id": observation.frame_id, "refreshed_frame_id": frame_id,
                     "confirmation_age_ms": confirmation_age_ms,
                     "preview": str(preview), "overlay_preview": str(overlay_path)},
                    frame_id, before_digest, False)
        if (not _fresh_next_card(before_observation, before_observation.captured_at_ms)
                or observation.my_next is None
                or before_observation.my_next.value != observation.my_next.value):
            return ({"status": "cancelled_after_refresh", "reason": "next_card_unconfirmed_or_changed",
                     "frame_id": observation.frame_id, "refreshed_frame_id": frame_id,
                     "preview": str(preview), "overlay_preview": str(overlay_path)},
                    frame_id, before_digest, False)

        slot = refreshed.card_slot
        if slot is None or refreshed.card is None or refreshed.target_x is None or refreshed.target_y is None:
            raise ValueError("ClashAI recommendation is missing an action field")
        cost = card_costs.get(_cost_key(refreshed.card))
        if cost is None:
            raise ValueError(f"No configured elixir cost for {refreshed.card}")
        proposal = ActionProposal(
            frame_id=before_observation.frame_id,
            card_slot=slot,
            target_x=refreshed.target_x,
            target_y=refreshed.target_y,
            confidence=refreshed.confidence,
            reason=refreshed.reason,
            expected_card=refreshed.card,
            elixir_cost=cost,
        )
        plan = plan_confirmed_action(
            proposal, before_observation, calibration,
            window_handle=handle, window_bounds=bounds_after_capture,
            now_ms=time.monotonic_ns() // 1_000_000,
            confirmation=typed, max_age_ms=_CONFIRMATION_MAX_AGE_MS,
        )
        input_attempted = True
        sent = dispatch_confirmed_action(plan)
        time.sleep(0.55)
        after_source = capture_window(handle)
        after_digest = hashlib.sha256(after_source).hexdigest()
        after_frame = decode_image(after_source)
        after_observation, _, after_metadata = provider.analyze(
            after_frame, serial=serial, frame_id=frame_id + 1,
        )
        frame_id += 1
        elapsed_ms = after_observation.captured_at_ms - before_observation.captured_at_ms
        verification = verify_play_transition(
            before_observation.to_dict(), after_observation.to_dict(), slot, cost, elapsed_ms,
        )
        result = {
            "status": verification["status"],
            "input": sent,
            "action_proposal": {
                "card_slot": proposal.card_slot,
                "expected_card": proposal.expected_card,
                "target_x": proposal.target_x,
                "target_y": proposal.target_y,
            },
            "preview": str(preview),
            "overlay_preview": str(overlay_path),
            "before_frame_id": before_observation.frame_id,
            "after_frame_id": after_observation.frame_id,
            "before_sha256": before_digest,
            "after_sha256": after_digest,
            "before_observation": before_observation.to_dict(),
            "after_observation": after_observation.to_dict(),
            "verification": verification,
            "asset_provenance": after_metadata.get("asset_provenance"),
        }
        return result, frame_id, after_digest, verification["status"] != "consistent_with_play"
    except Exception as exc:
        return ({"status": "dispatch_or_verification_error", "error": str(exc), "input": sent,
                 "input_attempted": input_attempted, "frame_id": observation.frame_id,
                 "preview": str(preview), "overlay_preview": str(overlay_path)}, frame_id, None, True)


def watch_window(handle: int, serial: str, provider: VisionProvider, output_dir: Path,
                 duration_s: float, recommend: Callable[[PublicObservation], Recommendation],
                 policy_name: str, *, confirm_actions: bool = False,
                 record_verified_action: Callable[[dict], dict] | None = None,
                 calibration: Calibration | None = None,
                 card_costs: dict[str, int] | None = None) -> tuple[int, Path, Path | None]:
    if confirm_actions and (calibration is None or not card_costs):
        raise ValueError("Confirmed action mode requires calibration and card costs")
    if confirm_actions and not sys.stdin.isatty():
        raise RuntimeError("Confirmed action mode requires an interactive terminal (TTY)")
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.time_ns()
    output = output_dir / f"live-{stamp}.jsonl"
    actions_path = output_dir / f"actions-{stamp}.jsonl" if confirm_actions else None
    if actions_path is not None:
        actions_path.open("x", encoding="utf-8").close()
    deadline = time.monotonic() + duration_s
    frame_id = 0
    last_digest = None
    battle_seen = False
    last_battle_active = None
    inactive_frame_ids: list[int] = []
    with output.open("x", encoding="utf-8", buffering=1) as stream:
        while time.monotonic() < deadline:
            started = time.monotonic()
            source = capture_window(handle)
            captured_ms = time.monotonic_ns() // 1_000_000
            digest = hashlib.sha256(source).hexdigest()
            frame_id += 1
            match_end = False
            if digest == last_digest:
                row = {"status": "skipped", "reason": "unchanged frame"}
                if battle_seen and last_battle_active is False:
                    inactive_frame_ids.append(frame_id)
                    match_end = len(inactive_frame_ids) >= 3
                    if match_end:
                        row.update(status="battle_ended", result_review=_review_match_end(
                            output_dir, stamp, frame_id, source, digest, inactive_frame_ids,
                        ))
            else:
                frame = decode_image(source)
                observation, overlay, metadata = provider.analyze(frame, serial=serial, frame_id=frame_id)
                row = {
                    "observation": observation.to_dict(),
                    "track_class_switches": metadata.get("track_class_switches", []),
                    "track_reassociations": metadata.get("track_reassociations", []),
                }
                if "asset_provenance" in metadata:
                    row["asset_provenance"] = metadata["asset_provenance"]
                battle_evidence = observation.battle_active
                if (battle_evidence.value is True and battle_evidence.status != "unknown"
                        and battle_evidence.confidence >= 0.5):
                    battle_seen = True
                    inactive_frame_ids.clear()
                elif (battle_seen and battle_evidence.value is False and battle_evidence.status != "unknown"
                      and battle_evidence.confidence >= 0.5):
                    inactive_frame_ids.append(frame_id)
                elif battle_seen:
                    inactive_frame_ids.clear()

                if battle_evidence.value is False and battle_evidence.status != "unknown" and battle_evidence.confidence >= 0.5:
                    last_battle_active = False
                elif battle_evidence.value is True and battle_evidence.status != "unknown" and battle_evidence.confidence >= 0.5:
                    last_battle_active = True
                else:
                    last_battle_active = None
                match_end = len(inactive_frame_ids) >= 3
                suggestion = None
                fresh_battle = (battle_evidence.value is True and battle_evidence.status != "unknown"
                                and battle_evidence.confidence >= 0.5)
                current_ms = time.monotonic_ns() // 1_000_000
                next_card_ready = (not confirm_actions
                                   or _fresh_next_card(observation, current_ms, MAX_HINT_AGE_MS))
                if (fresh_battle and next_card_ready
                        and current_ms - captured_ms <= MAX_HINT_AGE_MS):
                    suggestion = recommend(observation)
                if match_end:
                    row.update(status="battle_ended", result_review=_review_match_end(
                        output_dir, stamp, frame_id, source, digest, inactive_frame_ids,
                    ))
                elif not fresh_battle:
                    row.update(status="skipped", reason="battle not confirmed active")
                    print(f"Frame {frame_id}: skipped — battle not confirmed active", flush=True)
                elif confirm_actions and not next_card_ready:
                    row.update(status="skipped", reason="next card not confidently observed")
                    print(f"Frame {frame_id}: skipped — next card not confidently observed", flush=True)
                elif suggestion is None or time.monotonic_ns() // 1_000_000 - captured_ms > MAX_HINT_AGE_MS:
                    row.update(status="skipped", reason="frame too old for a live hint")
                    print(f"Frame {frame_id}: skipped — frame too old for a live hint", flush=True)
                else:
                    row.update(status="analyzed", recommendation=suggestion.to_dict())
                    message = suggestion.reason
                    if suggestion.card_slot is not None:
                        message = (f"slot {suggestion.card_slot + 1} {suggestion.card} at "
                                   f"({suggestion.target_x:.2f}, {suggestion.target_y:.2f}) — {message}")
                    print(f"Frame {frame_id}: {suggestion.status} — {message}", flush=True)
                    if confirm_actions and suggestion.status == "suggest":
                        if actions_path is None:
                            raise RuntimeError("Confirmed action log is unavailable")
                        event, frame_id, refreshed_digest, stop = _confirmed_action(
                            handle, serial, provider, calibration, recommend, card_costs,
                            observation, suggestion, source, overlay, output_dir, actions_path, stamp, frame_id,
                        )
                        if event.get("status") == "consistent_with_play" and record_verified_action is not None:
                            try:
                                event["policy_history"] = record_verified_action(event)
                            except Exception as exc:
                                event["policy_history"] = {"status": "error", "error": str(exc)}
                                stop = True
                        row["action_event"] = {key: value for key, value in event.items()
                                                if key not in ("before_observation", "after_observation")}
                        with actions_path.open("a", encoding="utf-8") as action_stream:
                            action_stream.write(json.dumps(event, ensure_ascii=False) + "\n")
                        if refreshed_digest is not None:
                            last_digest = refreshed_digest
                        row.update(frame_id=observation.frame_id, captured_at_monotonic_ms=captured_ms, sha256=digest,
                                   policy=policy_name, latency_ms=round((time.monotonic() - started) * 1000, 1))
                        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                        last_digest = refreshed_digest or digest
                        if stop:
                            print(f"Stopping live runner: {event['status']}", flush=True)
                            break
                        time.sleep(max(0, min(deadline, started + 0.5) - time.monotonic()))
                        continue
            row.update(frame_id=frame_id, captured_at_monotonic_ms=captured_ms, sha256=digest, policy=policy_name,
                       latency_ms=round((time.monotonic() - started) * 1000, 1))
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            last_digest = digest
            if match_end:
                break
            time.sleep(max(0, min(deadline, started + 0.5) - time.monotonic()))
    return frame_id, output, actions_path
