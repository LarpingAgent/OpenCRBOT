from __future__ import annotations

import sys
from io import BytesIO


def resolve_window(target: str) -> int:
    if target.isdecimal():
        return int(target)
    if sys.platform != "win32":
        raise RuntimeError("Window capture requires Windows.")

    from ctypes import WINFUNCTYPE, WinDLL, c_int, create_unicode_buffer
    from ctypes.wintypes import BOOL, HWND, LPARAM, LPWSTR

    needle = target.strip().casefold()
    if not needle:
        raise ValueError("Specify a window title or decimal HWND.")
    user32 = WinDLL("user32", use_last_error=True)
    user32.IsWindowVisible.argtypes = (HWND,)
    user32.GetWindowTextLengthW.argtypes = (HWND,)
    user32.GetWindowTextW.argtypes = (HWND, LPWSTR, c_int)
    callback = WINFUNCTYPE(BOOL, HWND, LPARAM)
    user32.EnumWindows.argtypes = (callback, LPARAM)
    matches: list[tuple[int, str]] = []

    @callback
    def visit(handle: int, _: int) -> bool:
        length = user32.GetWindowTextLengthW(handle)
        if length and user32.IsWindowVisible(handle):
            buffer = create_unicode_buffer(length + 1)
            user32.GetWindowTextW(handle, buffer, len(buffer))
            if needle in buffer.value.casefold():
                matches.append((handle, buffer.value))
        return True

    if not user32.EnumWindows(visit, 0):
        raise OSError("Could not enumerate Windows windows.")
    if not matches:
        raise ValueError(f"No visible window matches {target!r}.")
    if len(matches) > 1:
        choices = ", ".join(f"{handle} ({title})" for handle, title in matches)
        raise ValueError(f"Multiple windows match {target!r}. Use a decimal HWND: {choices}")
    return matches[0][0]


def capture_window(handle: int) -> bytes:
    if sys.platform != "win32":
        raise RuntimeError("Window capture requires Windows.")
    if not 0 < handle <= sys.maxsize:
        raise ValueError("Window handle must be a positive HWND.")

    from ctypes import WinDLL, c_void_p

    user32 = WinDLL("user32")
    for name in ("IsWindow", "IsWindowVisible", "IsIconic"):
        getattr(user32, name).argtypes = (c_void_p,)
    if not user32.IsWindow(handle) or not user32.IsWindowVisible(handle):
        raise ValueError(f"No visible window for HWND {handle}.")
    if user32.IsIconic(handle):
        raise ValueError("Restore the window before capturing it.")

    try:
        from PIL import ImageGrab
    except ImportError as exc:
        raise RuntimeError('Install window capture with pip install -e ".[windows]".') from exc

    user32.GetForegroundWindow.restype = c_void_p
    if user32.GetForegroundWindow() == handle:
        # Desktop composition includes GPU-rendered content that direct window capture may miss.
        region = window_frame_bounds(handle)
        image = ImageGrab.grab(bbox=region, all_screens=True)
        if user32.GetForegroundWindow() != handle:
            image.close()
            raise OSError("The foreground window changed during capture. Try again.")
    else:
        image = ImageGrab.grab(window=handle)

    with image, BytesIO() as output:
        if image.getbbox() is None:
            raise OSError("Capture returned black. Bring the game to the foreground and try again.")
        image.save(output, format="PNG")
        return output.getvalue()


def window_frame_bounds(handle: int) -> tuple[int, int, int, int]:
    """Return the same extended frame bounds used for foreground window capture."""
    if sys.platform != "win32":
        raise RuntimeError("Window bounds require Windows.")
    if not 0 < handle <= sys.maxsize:
        raise ValueError("Window handle must be a positive HWND.")

    from ctypes import WinDLL, byref, c_void_p, sizeof
    from ctypes.wintypes import DWORD, RECT

    user32 = WinDLL("user32", use_last_error=True)
    user32.IsWindow.argtypes = (c_void_p,)
    user32.IsWindowVisible.argtypes = (c_void_p,)
    user32.IsIconic.argtypes = (c_void_p,)
    if not user32.IsWindow(handle) or not user32.IsWindowVisible(handle):
        raise ValueError(f"No visible window for HWND {handle}.")
    if user32.IsIconic(handle):
        raise ValueError("Restore the window before interacting with it.")

    bounds = RECT()
    get_bounds = WinDLL("dwmapi", use_last_error=True).DwmGetWindowAttribute
    get_bounds.argtypes = (c_void_p, DWORD, c_void_p, DWORD)
    if get_bounds(handle, 9, byref(bounds), sizeof(bounds)) != 0:
        raise OSError("Could not get the extended frame bounds of the window.")
    return bounds.left, bounds.top, bounds.right, bounds.bottom
