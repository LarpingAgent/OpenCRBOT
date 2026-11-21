from __future__ import annotations

import hashlib
import json
import math
import struct
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .adb import AdbClient

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_CAPTURE_FRAMES = 10_000


def png_dimensions(png: bytes) -> tuple[int, int]:
    if len(png) < 24 or png[:8] != PNG_SIGNATURE or png[12:16] != b"IHDR":
        raise ValueError("invalid PNG header")
    width, height = struct.unpack(">II", png[16:24])
    if width == 0 or height == 0:
        raise ValueError("PNG dimensions must be positive")
    return width, height


def _append_jsonl(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def record_session(
    client: AdbClient | None,
    *,
    serial: str | None,
    output_dir: Path,
    duration_s: float,
    interval_ms: int,
    capture: Callable[[], bytes] | None = None,
) -> tuple[int, Path]:
    if not math.isfinite(duration_s) or duration_s <= 0:
        raise ValueError("duration_s must be a positive finite number")
    if interval_ms <= 0:
        raise ValueError("interval_ms must be positive")
    estimated_frames = math.ceil(duration_s * 1000 / interval_ms)
    if estimated_frames > MAX_CAPTURE_FRAMES:
        raise ValueError(f"capture would exceed the {MAX_CAPTURE_FRAMES}-frame safety limit")

    source = {}
    if capture is None:
        if client is None:
            raise ValueError("Recording requires a capture source")
        selected_serial = client.resolve_serial(serial)
        source["adb"] = str(client.executable)

        def capture() -> bytes:
            return client.screenshot(selected_serial)
    else:
        selected_serial = serial
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    manifest = output_dir / "manifest.jsonl"
    start_ms = int(time.monotonic() * 1000)
    started_utc = datetime.now(timezone.utc).isoformat()
    _append_jsonl(manifest, {
        "type": "session_started",
        "schema": "opencrbot.capture.v1",
        "serial": selected_serial,
        **source,
        "started_at_utc": started_utc,
        "started_at_monotonic_ms": start_ms,
        "duration_s": duration_s,
        "interval_ms": interval_ms,
    })

    frame_id = 0
    status = "complete"
    error = None
    stop_at = time.monotonic() + duration_s
    next_capture_at = time.monotonic()
    try:
        while time.monotonic() < stop_at:
            if frame_id >= MAX_CAPTURE_FRAMES:
                raise ValueError(f"capture exceeded the {MAX_CAPTURE_FRAMES}-frame safety limit")
            capture_started_ms = int(time.monotonic() * 1000)
            png = capture()
            captured_at_ms = int(time.monotonic() * 1000)
            width, height = png_dimensions(png)
            frame_id += 1
            name = f"frame-{frame_id:06d}.png"
            frame_path = output_dir / name
            frame_path.write_bytes(png)
            _append_jsonl(manifest, {
                "type": "frame_captured",
                "frame_id": frame_id,
                "file": name,
                "captured_at_monotonic_ms": captured_at_ms,
                "capture_ms": captured_at_ms - capture_started_ms,
                "elapsed_ms": captured_at_ms - start_ms,
                "width": width,
                "height": height,
                "sha256": hashlib.sha256(png).hexdigest(),
            })
            next_capture_at += interval_ms / 1000
            delay = next_capture_at - time.monotonic()
            if delay > 0:
                time.sleep(min(delay, max(0.0, stop_at - time.monotonic())))
    except KeyboardInterrupt:
        status = "interrupted"
        raise
    except Exception as exc:
        status = "failed"
        error = str(exc)
        raise
    finally:
        _append_jsonl(manifest, {
            "type": "session_finished",
            "frame_count": frame_id,
            "status": status,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "error": error,
        })
    return frame_id, output_dir


def _inspect_session(session_dir: Path) -> tuple[list[dict], str, int]:
    """Verify a capture manifest, frame order, hashes and PNG dimensions."""
    session_dir = session_dir.resolve()
    manifest = session_dir / "manifest.jsonl"
    if not manifest.is_file():
        raise ValueError(f"capture manifest not found: {manifest}")
    try:
        records = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read capture manifest: {exc}") from exc
    if not all(isinstance(record, dict) for record in records):
        raise ValueError("every manifest line must contain a JSON object")
    if len(records) < 2 or records[0].get("type") != "session_started":
        raise ValueError("manifest must begin with session_started and contain session_finished")
    if records[0].get("schema") != "opencrbot.capture.v1":
        raise ValueError("unsupported capture schema")
    if records[-1].get("type") != "session_finished":
        raise ValueError("manifest is incomplete: session_finished is missing")

    frame_records = records[1:-1]
    previous_digest = None
    previous_ms = records[0].get("started_at_monotonic_ms")
    if type(previous_ms) is not int:
        raise ValueError("invalid session start timestamp")
    identical_ms = longest_identical_ms = 0
    for expected_id, record in enumerate(frame_records, start=1):
        if record.get("type") != "frame_captured" or record.get("frame_id") != expected_id:
            raise ValueError(f"invalid frame sequence at manifest entry {expected_id}")
        filename = record.get("file")
        if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".png"):
            raise ValueError(f"unsafe or invalid frame path at frame {expected_id}")
        frame_path = (session_dir / filename).resolve()
        if frame_path.parent != session_dir or not frame_path.is_file():
            raise ValueError(f"frame file missing or outside session directory: {filename}")
        data = frame_path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != record.get("sha256"):
            raise ValueError(f"SHA-256 mismatch for {filename}")
        if png_dimensions(data) != (record.get("width"), record.get("height")):
            raise ValueError(f"dimension mismatch for {filename}")
        captured_ms = record.get("captured_at_monotonic_ms")
        if type(captured_ms) is not int or captured_ms < previous_ms:
            raise ValueError(f"invalid capture timestamp for {filename}")
        if digest == previous_digest:
            identical_ms += captured_ms - previous_ms
            longest_identical_ms = max(longest_identical_ms, identical_ms)
        else:
            identical_ms = 0
        previous_digest = digest
        previous_ms = captured_ms
    finished = records[-1]
    if finished.get("frame_count") != len(frame_records):
        raise ValueError("session_finished frame_count does not match manifest")
    if finished.get("status") not in ("complete", "interrupted"):
        raise ValueError(f"capture session did not finish cleanly: {finished.get('status')}")
    return frame_records, str(finished["status"]), longest_identical_ms


def inspect_session(session_dir: Path) -> tuple[int, str, int]:
    frames, status, longest_identical_ms = _inspect_session(session_dir)
    return len(frames), status, longest_identical_ms


def verify_session(session_dir: Path) -> tuple[int, str]:
    count, status, _ = inspect_session(session_dir)
    return count, status


def verified_frames(session_dir: Path) -> list[dict]:
    frames, _, _ = _inspect_session(session_dir)
    return frames


def replay_session(session_dir: Path, *, speed: float = 1.0) -> int:
    """Display verified frames at the recorded cadence until the session ends or Esc is pressed."""
    if not math.isfinite(speed) or not 0.01 <= speed <= 100:
        raise ValueError("Replay speed must be between 0.01 and 100")
    frames, _, _ = _inspect_session(session_dir)
    if not frames:
        return 0
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError('Install replay with pip install -e ".[vision]".') from exc

    from .vision import decode_image

    title = "OpenCRBOT replay (Esc to stop)"
    cv2.namedWindow(title, cv2.WINDOW_NORMAL)
    try:
        for index, frame in enumerate(frames):
            image = decode_image((session_dir / frame["file"]).read_bytes())
            cv2.imshow(title, image)
            delay = 500
            if index + 1 < len(frames):
                delta_ms = frames[index + 1]["captured_at_monotonic_ms"] - frame["captured_at_monotonic_ms"]
                delay = max(1, round(delta_ms / speed))
            if cv2.waitKey(delay) == 27 or cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) < 1:
                return index + 1
    finally:
        cv2.destroyAllWindows()
    return len(frames)
