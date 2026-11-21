from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import replace
from pathlib import Path

from .adb import AdbClient, AdbError, find_adb
from .contracts import Evidence
from .recording import inspect_session, record_session, verified_frames
from .windows_capture import capture_window, resolve_window

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CALIBRATION = _PROJECT_ROOT / "configs" / "clashai-reference.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opencrbot")
    parser.add_argument("--adb", help="Path to adb.exe (defaults to standard discovery).")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("doctor", help="Show ADB version and connected devices.")
    commands.add_parser("devices", help="List ADB devices.")

    connect = commands.add_parser("connect", help="Connect to an ADB endpoint.")
    connect.add_argument("address", nargs="?", default="localhost:6520")

    screenshot = commands.add_parser("screenshot", help="Save a device screenshot as PNG.")
    target = screenshot.add_mutually_exclusive_group()
    target.add_argument("--serial", help="ADB serial; auto-selects only when one device is online.")
    target.add_argument("--window", help="Capture a Windows window by title or decimal HWND without ADB.")
    screenshot.add_argument("--out", type=Path, default=Path("runs/latest.png"))

    record = commands.add_parser("record", help="Capture a bounded, replayable screenshot session.")
    record_target = record.add_mutually_exclusive_group()
    record_target.add_argument("--serial", help="ADB serial; auto-selects only when one device is online.")
    record_target.add_argument("--window", help="Capture a Windows window by title or decimal HWND without ADB.")
    record.add_argument("--out", type=Path, help="New or unused output directory.")
    record.add_argument("--seconds", type=float, default=15.0)
    record.add_argument("--interval-ms", type=int, default=1000)

    verify = commands.add_parser("verify-session", help="Check a recorded session and its frame hashes.")
    verify.add_argument("directory", type=Path)

    replay = commands.add_parser("replay", help="Play a verified capture session in frame order.")
    replay.add_argument("directory", type=Path)
    replay.add_argument("--speed", type=float, default=1.0, help="Playback speed multiplier (default: 1).")

    evaluate = commands.add_parser("evaluate-hud", help="Compare analyzed session frames with manual HUD labels.")
    evaluate.add_argument("analysis", type=Path, help="JSONL output from analyze --session.")
    evaluate.add_argument("labels", type=Path, help="JSON object with a frames array of manual labels.")

    units = commands.add_parser("evaluate-units", help="Score unit detections and labeled identity continuity.")
    units.add_argument("analysis", type=Path)
    units.add_argument("labels", type=Path)

    verify_play = commands.add_parser("verify-play", help="Check recorded frames for evidence of a single card play.")
    verify_play.add_argument("analysis", type=Path)
    verify_play.add_argument("--before", type=int, required=True)
    verify_play.add_argument("--after", type=int, required=True)
    verify_play.add_argument("--slot", type=int, choices=range(1, 5), required=True)
    verify_play.add_argument("--cost", type=int, required=True)

    export = commands.add_parser("export-clashai", help="Export one analyzed session frame as ClashAI BoardState JSON.")
    export.add_argument("analysis", type=Path)
    export.add_argument("--frame", type=int, required=True)
    export.add_argument("--catalog", type=Path, required=True, help="ClashAI UNIT_VOCAB JSON array.")
    export.add_argument("--deck", nargs=8, required=True)
    export.add_argument("--out", type=Path, required=True)

    import_state = commands.add_parser("import-state", help="Convert one offline game-state record to OpenCRBOT JSON.")
    import_state.add_argument("format", choices=("cr-mem", "clashai"))
    import_state.add_argument("input", type=Path)
    import_state.add_argument("--catalog", type=Path, required=True)
    import_state.add_argument("--line", type=int, help="Read this 1-based line of a JSONL file.")
    import_state.add_argument("--out", type=Path, required=True)

    analyze = commands.add_parser("analyze", help="Analyze one screenshot with local YOLO and card-template assets.")
    source = analyze.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", type=Path, help="Analyze a saved screenshot without connecting to ADB.")
    source.add_argument("--session", type=Path, help="Analyze all frames in a verified session without ADB.")
    clock_mode = analyze.add_mutually_exclusive_group()
    clock_mode.add_argument("--clock-anchor", nargs=2, type=int, metavar=("FRAME", "ELAPSED_SECONDS"),
                         help="Anchor elapsed regulation time to a manually read frame in --session.")
    clock_mode.add_argument("--regulation-clock", action="store_true",
                            help="Derive elapsed time from OCR in captures known to be regulation only.")
    analyze.add_argument("--frames", nargs="+", type=int, metavar="FRAME",
                         help="Analyze only these frame IDs from --session, in capture order.")
    analyze.add_argument("--track", action="store_true", help="Track YOLO detections across a session or live watch.")
    analyze.add_argument("--watch-seconds", type=float, metavar="SECONDS",
                         help="Continuously analyze --window and report live policy hints for this duration.")
    analyze.add_argument("--confirm-actions", action="store_true",
                         help="In live Training Camp mode, require an exact operator phrase before each suggested play and verify the transition.")
    analyze.add_argument("--clashai-root", type=Path, help="ClashAI checkout for live hints.")
    analyze.add_argument("--clashai-checkpoint", type=Path, help="ClashAI S1 or Gen FV3 checkpoint for live hints.")
    analyze.add_argument("--clashai-deck", type=Path, help="Deck YAML/name for a generalist checkpoint, e.g. icebow.")
    analyze.add_argument("--clashai-costs", nargs=8, type=int, metavar="COST", help="Elixir costs in checkpoint deck order.")
    source.add_argument("--serial", help="Capture one screenshot from this ADB device before analysis.")
    source.add_argument("--window", help="Capture a Windows window by title or decimal HWND without ADB.")
    analyze.add_argument("--weights", type=Path, required=True, help="Local clash-ai YOLO .pt file; no model is downloaded.")
    analyze.add_argument("--templates", type=Path, required=True, help="Local card_templates.npz from the clash-ai checkout.")
    analyze.add_argument("--next-templates", type=Path,
                         help="Optional local template archive cropped from the GPGPC Next-card icon.")
    analyze.add_argument("--clock-templates", type=Path, help="Local JSON containing the HUD clock crop and digit templates.")
    analyze.add_argument("--rubble-template", type=Path, help="Local grayscale crop of a destroyed princess tower.")
    analyze.add_argument("--calibration", type=Path,
                         help=f"Normalized screen geometry JSON (default: {_DEFAULT_CALIBRATION}; required explicitly for --confirm-actions).")
    analyze.add_argument("--device", default="cpu", help="Ultralytics inference device, e.g. cpu or 0 (default: cpu).")
    analyze.add_argument("--out", type=Path, default=Path("runs/analysis"), help="Output directory for JSON and overlay PNG.")
    return parser


def _show_devices(client: AdbClient) -> int:
    devices = client.devices()
    if not devices:
        print("No ADB devices found.")
        return 0
    for device in devices:
        suffix = f" {device.details}" if device.details else ""
        print(f"{device.serial}\t{device.state}{suffix}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "evaluate-units":
            from .unit_evaluation import evaluate_units

            print(json.dumps(evaluate_units(args.analysis, args.labels), indent=2))
            return 0
        if args.command == "verify-play":
            from .play_verification import verify_recorded_play

            result = verify_recorded_play(args.analysis, args.before, args.after, args.slot - 1, args.cost)
            print(json.dumps(result, indent=2))
            return 0 if result["status"] == "consistent_with_play" else 2
        if args.command == "export-clashai":
            from .clashai_export import to_board_state

            vocabulary = json.loads(args.catalog.read_text(encoding="utf-8-sig"))
            if not isinstance(vocabulary, list) or not all(isinstance(name, str) for name in vocabulary):
                raise ValueError("Expected ClashAI UNIT_VOCAB as a JSON array of names.")
            with args.analysis.open(encoding="utf-8-sig") as stream:
                for line in stream:
                    observation = json.loads(line)["observation"]
                    if observation["frame_id"] == args.frame:
                        break
                else:
                    raise ValueError(f"Analysis has no frame {args.frame}.")
            board = to_board_state(observation, vocabulary, args.deck)
            with args.out.open("x", encoding="utf-8") as stream:
                json.dump(board, stream, indent=2)
                stream.write("\n")
            print(f"Saved ClashAI BoardState: {args.out.resolve()}")
            return 0
        if args.command == "evaluate-hud":
            from .hud_evaluation import evaluate_hud

            print(json.dumps(evaluate_hud(args.analysis, args.labels), indent=2))
            return 0
        if args.command == "import-state":
            from .policy import recommend
            from .state_import import import_state

            if args.line is None:
                raw = args.input.read_text(encoding="utf-8-sig")
            else:
                if args.line < 1:
                    raise ValueError("--line must be positive.")
                with args.input.open(encoding="utf-8-sig") as stream:
                    raw = next((row for number, row in enumerate(stream, 1) if number == args.line), None)
                if raw is None:
                    raise ValueError(f"Input has no line {args.line}.")
            catalog = json.loads(args.catalog.read_text(encoding="utf-8-sig"))
            observation = import_state(json.loads(raw), catalog, format=args.format)
            document = {"observation": observation.to_dict(), "recommendation": recommend(observation).to_dict()}
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open("x", encoding="utf-8") as stream:
                json.dump(document, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
            print(f"Saved imported observation: {args.out.resolve()}")
            return 0
        if args.command == "replay":
            from .recording import replay_session

            count = replay_session(args.directory, speed=args.speed)
            print(f"Displayed {count} frames.")
            return 0
        target = getattr(args, "window", None)
        window = resolve_window(target) if target is not None else None
        offline = args.command == "verify-session" or (args.command == "analyze" and (args.image or args.session))
        client = None if offline or window is not None else AdbClient(find_adb(args.adb))
        if args.command == "doctor":
            print(f"ADB: {client.executable}")
            print(client.version())
            return _show_devices(client)
        if args.command == "devices":
            return _show_devices(client)
        if args.command == "connect":
            print(client.connect(args.address))
            return 0
        if args.command == "screenshot":
            image = capture_window(window) if window is not None else client.screenshot(args.serial)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_bytes(image)
            print(f"Saved screenshot: {args.out.resolve()}")
            return 0
        if args.command == "record":
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            out = args.out or Path("runs/captures") / f"session-{stamp}-{time.time_ns() % 1_000_000_000:09d}"
            count, directory = record_session(
                client,
                serial=f"windows-window:{window}" if window is not None else args.serial,
                output_dir=out,
                duration_s=args.seconds,
                interval_ms=args.interval_ms,
                capture=(lambda: capture_window(window)) if window is not None else None,
            )
            print(f"Saved {count} frames and manifest: {directory.resolve()}")
            return 0
        if args.command == "verify-session":
            count, status, identical_ms = inspect_session(args.directory)
            print(f"Session verified: {count} frames, status={status}")
            print(f"Longest run of identical frames: {identical_ms / 1000:.3f} s")
            return 0
        if args.command == "analyze":
            from .policy import recommend
            from .vision import VisionProvider, decode_image, load_calibration

            if (args.clock_anchor or args.frames) and not args.session:
                raise ValueError("--clock-anchor and --frames require --session.")
            if args.track and args.frames:
                raise ValueError("--track does not support a frame selection.")
            if args.track and not (args.session or args.watch_seconds is not None):
                raise ValueError("--track requires --session or a live --watch-seconds window.")
            if args.regulation_clock and not args.clock_templates:
                raise ValueError("--regulation-clock requires --clock-templates.")
            if args.confirm_actions and not (args.watch_seconds and args.window and args.regulation_clock
                                             and args.next_templates
                                             and args.clashai_checkpoint and args.clashai_deck and args.calibration):
                raise ValueError("--confirm-actions requires --window, --watch-seconds, --regulation-clock, --next-templates, --clashai-checkpoint, --clashai-deck and an explicit --calibration.")
            if args.watch_seconds is not None and (
                window is None or not math.isfinite(args.watch_seconds) or args.watch_seconds <= 0
            ):
                raise ValueError("--watch-seconds requires --window and a positive finite duration.")
            clashai_options = (args.clashai_root, args.clashai_checkpoint, args.clashai_costs)
            if any(clashai_options) and not (all(clashai_options) and args.watch_seconds and args.regulation_clock):
                raise ValueError("ClashAI live hints require all three --clashai options, --watch-seconds and --regulation-clock.")
            if args.clashai_deck and not all(clashai_options):
                raise ValueError("--clashai-deck requires --clashai-root, --clashai-checkpoint and --clashai-costs.")
            if args.session:
                records = verified_frames(args.session)
                if not records:
                    raise ValueError("Session contains no frames to analyze.")
                serial = "offline-session"
                start_ms = None
                if args.clock_anchor:
                    anchor_id, elapsed_s = args.clock_anchor
                    if not 1 <= anchor_id <= len(records) or not 0 <= elapsed_s < 180:
                        raise ValueError("Clock anchor must identify a recorded frame and elapsed seconds in [0, 180).")
                    start_ms = records[anchor_id - 1]["captured_at_monotonic_ms"] - elapsed_s * 1000
                if args.frames:
                    selected = set(args.frames)
                    if min(selected) < 1 or max(selected) > len(records):
                        raise ValueError("--frames must identify recorded frame IDs.")
                    records = [record for record in records if record["frame_id"] in selected]
            elif args.image:
                image_data = args.image.read_bytes()
                serial = "offline-image"
            elif window is not None:
                if args.watch_seconds is None:
                    image_data = capture_window(window)
                serial = f"windows-window:{window}"
            else:
                image_data = client.screenshot(args.serial)
                serial = client.resolve_serial(args.serial)
            calibration = load_calibration(args.calibration or _DEFAULT_CALIBRATION)
            provider = VisionProvider(args.weights, args.templates, calibration, device=args.device,
                                      clock_templates=args.clock_templates, regulation_clock=args.regulation_clock,
                                      track=args.track, rubble_template=args.rubble_template,
                                      next_templates=args.next_templates)
            if args.watch_seconds is not None:
                from .live import watch_window

                policy_name = "defensive-baseline"
                record_verified_action = None
                if args.clashai_checkpoint:
                    from .clashai_policy import load_clashai_policy

                    policy = load_clashai_policy(*clashai_options, deck=args.clashai_deck)
                    recommend = policy.recommend
                    record_verified_action = getattr(policy, "record_verified_action", None)
                    policy_name = f"{policy.policy_name}:{policy.checkpoint_sha256}"
                if args.confirm_actions and record_verified_action is None:
                    raise ValueError("Confirmed ClashAI actions require verified-play history support.")
                card_costs = None
                if args.confirm_actions:
                    cards = tuple(getattr(getattr(policy, "deck", None), "cards", ()))
                    normalize_card = lambda name: "".join(char for char in str(name).casefold() if char.isalnum())
                    normalized_cards = tuple(normalize_card(card) for card in cards)
                    calibrated_deck = {normalize_card(card) for card in calibration.known_deck}
                    if (len(cards) != len(args.clashai_costs) or len(set(normalized_cards)) != len(cards)
                            or calibrated_deck != set(normalized_cards) or calibration.board_bounds is None):
                        raise ValueError("Confirmed actions require eight unique checkpoint deck cards and matching costs.")
                    card_costs = dict(zip(normalized_cards, args.clashai_costs))
                    if any(cost < 1 or cost > 10 for cost in card_costs.values()):
                        raise ValueError("Confirmed actions require card costs between 1 and 10 elixir.")
                count, output, actions = watch_window(
                    window, serial, provider, args.out, args.watch_seconds, recommend, policy_name,
                    confirm_actions=args.confirm_actions, record_verified_action=record_verified_action,
                    calibration=calibration, card_costs=card_costs,
                )
                print(f"Captured {count} live frames: {output.resolve()}")
                if actions is not None:
                    print(f"Action events: {actions.resolve()}")
                return 0
            if args.session:
                args.out.mkdir(parents=True, exist_ok=True)
                stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
                output = args.out / f"session-{stamp}-{time.time_ns() % 1_000_000_000:09d}.jsonl"
                with output.open("x", encoding="utf-8") as stream:
                    for record in records:
                        frame = decode_image((args.session / record["file"]).read_bytes())
                        observation, _, metadata = provider.analyze(frame, serial=serial, frame_id=record["frame_id"])
                        if start_ms is not None and observation.battle_active.value is True:
                            captured_ms = record["captured_at_monotonic_ms"]
                            elapsed_s = (captured_ms - start_ms) / 1000
                            if 0 <= elapsed_s < 180:
                                observation = replace(observation, battle_elapsed_s=Evidence(
                                    elapsed_s, "inferred", 0.6, "session.clock_anchor", captured_ms,
                                ))
                        document = {
                            **metadata,
                            "capture": record,
                            "observation": observation.to_dict(),
                            "recommendation": recommend(observation).to_dict(),
                        }
                        stream.write(json.dumps(document, ensure_ascii=False) + "\n")
                print(f"Analyzed {len(records)} frames: {output.resolve()}")
                return 0
            frame = decode_image(image_data)
            observation, overlay, metadata = provider.analyze(frame, serial=serial)
            suggestion = recommend(observation)

            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            output_dir = args.out / f"frame-{stamp}-{time.time_ns() % 1_000_000_000:09d}"
            output_dir.mkdir(parents=True, exist_ok=False)
            json_path = output_dir / "analysis.json"
            png_path = output_dir / "overlay.png"
            source_path = output_dir / "source.png"
            if not provider.cv2.imwrite(str(source_path), frame):
                raise OSError(f"could not write source image: {source_path}")
            if not provider.cv2.imwrite(str(png_path), overlay):
                raise OSError(f"could not write analysis overlay: {png_path}")
            document = {
                **metadata,
                "observation": observation.to_dict(),
                "recommendation": suggestion.to_dict(),
                "artifacts": {"source": "source.png", "overlay": "overlay.png"},
            }
            json_path.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"Saved analysis: {json_path.resolve()}")
            print(f"Saved overlay: {png_path.resolve()}")
            print(f"Recommendation: {suggestion.status} — {suggestion.reason}")
            print("No game input was sent.")
            return 0
    except (AdbError, OSError) as exc:
        print(f"opencrbot: error: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"opencrbot: error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"opencrbot: error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Capture interrupted.", file=sys.stderr)
        return 130
    return 2
