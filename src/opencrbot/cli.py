from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .adb import AdbClient, AdbError, find_adb
from .recording import record_session, verify_session

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
    screenshot.add_argument("--serial", help="ADB serial; auto-selects only when one device is online.")
    screenshot.add_argument("--out", type=Path, default=Path("runs/latest.png"))

    record = commands.add_parser("record", help="Capture a bounded, replayable screenshot session.")
    record.add_argument("--serial", help="ADB serial; auto-selects only when one device is online.")
    record.add_argument("--out", type=Path, help="New or unused output directory.")
    record.add_argument("--seconds", type=float, default=15.0)
    record.add_argument("--interval-ms", type=int, default=1000)

    verify = commands.add_parser("verify-session", help="Check a recorded session and its frame hashes.")
    verify.add_argument("directory", type=Path)

    analyze = commands.add_parser("analyze", help="Analyze one screenshot with local YOLO and card-template assets.")
    source = analyze.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", type=Path, help="Analyze a saved screenshot without connecting to ADB.")
    source.add_argument("--serial", help="Capture one screenshot from this ADB device before analysis.")
    analyze.add_argument("--weights", type=Path, required=True, help="Local clash-ai YOLO .pt file; no model is downloaded.")
    analyze.add_argument("--templates", type=Path, required=True, help="Local card_templates.npz from the clash-ai checkout.")
    analyze.add_argument("--calibration", type=Path, default=_DEFAULT_CALIBRATION,
                         help=f"Normalized screen geometry JSON (default: {_DEFAULT_CALIBRATION}).")
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
        client = None if args.command == "analyze" and args.image else AdbClient(find_adb(args.adb))
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
            image = client.screenshot(args.serial)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_bytes(image)
            print(f"Saved screenshot: {args.out.resolve()}")
            return 0
        if args.command == "record":
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            out = args.out or Path("runs/captures") / f"session-{stamp}-{time.time_ns() % 1_000_000_000:09d}"
            count, directory = record_session(
                client,
                serial=args.serial,
                output_dir=out,
                duration_s=args.seconds,
                interval_ms=args.interval_ms,
            )
            print(f"Saved {count} frames and manifest: {directory.resolve()}")
            return 0
        if args.command == "verify-session":
            count, status = verify_session(args.directory)
            print(f"Session verified: {count} frames, status={status}")
            return 0
        if args.command == "analyze":
            from .policy import recommend
            from .vision import VisionProvider, decode_image, load_calibration

            if args.image:
                image_data = args.image.read_bytes()
                serial = "offline-image"
            else:
                image_data = client.screenshot(args.serial)
                serial = client.resolve_serial(args.serial)
            frame = decode_image(image_data)
            calibration = load_calibration(args.calibration)
            provider = VisionProvider(args.weights, args.templates, calibration, device=args.device)
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
