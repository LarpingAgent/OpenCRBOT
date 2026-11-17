from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class AdbError(RuntimeError):
    """Raised when ADB is unavailable or a device command fails."""


@dataclass(frozen=True)
class Device:
    serial: str
    state: str
    details: str = ""


def find_adb(explicit: str | None = None) -> Path:
    """Find ADB from an explicit path, PATH, Android SDK, or GPGPC Dev Emulator."""
    if explicit:
        explicit_path = Path(explicit).expanduser()
        if explicit_path.is_file():
            return explicit_path.resolve()
        located = shutil.which(explicit)
        if located:
            return Path(located).resolve()
        raise AdbError(f"ADB path does not exist or is not executable: {explicit}")

    candidates: list[Path] = []

    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    emulator_dir = program_files / "Google" / "Play Games Developer Emulator" / "current" / "emulator"
    candidates.extend((emulator_dir / "adb.exe", emulator_dir / "adb"))

    for env_name in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        sdk_root = os.environ.get(env_name)
        if sdk_root:
            candidates.extend((Path(sdk_root) / "platform-tools" / name for name in ("adb.exe", "adb")))

    from_path = shutil.which("adb")
    if from_path:
        candidates.append(Path(from_path))

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    raise AdbError(
        "ADB was not found. Add adb to PATH, set ANDROID_SDK_ROOT, or install "
        "Google Play Games on PC Developer Emulator."
    )


class AdbClient:
    def __init__(self, executable: Path) -> None:
        self.executable = executable

    def _run(self, *args: str, timeout: float = 30) -> bytes:
        command = [str(self.executable), *args]
        try:
            result = subprocess.run(command, check=False, capture_output=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AdbError(f"ADB command failed to start or timed out: {exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise AdbError(detail or f"ADB exited with status {result.returncode}")
        return result.stdout

    def version(self) -> str:
        return self._run("version").decode("utf-8", errors="replace").strip()

    def devices(self) -> list[Device]:
        output = self._run("devices", "-l").decode("utf-8", errors="replace")
        found: list[Device] = []
        for line in output.splitlines():
            line = line.strip()
            if not line or line.startswith("List of devices"):
                continue
            fields = line.split()
            if len(fields) >= 2:
                found.append(Device(fields[0], fields[1], " ".join(fields[2:])))
        return found

    def connect(self, address: str) -> str:
        return self._run("connect", address).decode("utf-8", errors="replace").strip()

    def resolve_serial(self, serial: str | None) -> str:
        if serial:
            return serial
        available = [device for device in self.devices() if device.state == "device"]
        if len(available) == 1:
            return available[0].serial
        if not available:
            raise AdbError(
                "No ADB device is connected. Start the Developer Emulator or run "
                "`opencrbot connect localhost:6520`."
            )
        choices = ", ".join(device.serial for device in available)
        raise AdbError(f"More than one ADB device is connected ({choices}); pass --serial.")

    def screenshot(self, serial: str | None = None) -> bytes:
        device_serial = self.resolve_serial(serial)
        image = self._run("-s", device_serial, "exec-out", "screencap", "-p", timeout=60)
        if not image.startswith(b"\x89PNG\r\n\x1a\n"):
            raise AdbError("The device returned data that is not a PNG screenshot.")
        return image
