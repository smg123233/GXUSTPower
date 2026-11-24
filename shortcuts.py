"""桌面快捷方式创建/删除（通过 PowerShell 的 WScript.Shell）。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

NAME = "GXUST电费助手"


def desktop_dir() -> Path:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer"
                            r"\User Shell Folders") as key:
            value, _ = winreg.QueryValueEx(key, "Desktop")
            expanded = os.path.expandvars(value)
            if expanded:
                return Path(expanded)
    except Exception:
        pass
    return Path.home() / "Desktop"


def shortcut_path() -> Path:
    return desktop_dir() / f"{NAME}.lnk"


def _target() -> tuple[str, str, str]:
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        return str(exe), str(exe.parent), str(exe)
    script = Path(sys.argv[0]).resolve()
    return str(Path(sys.executable)), str(script.parent), str(script)


def exists() -> bool:
    return shortcut_path().exists()


def create() -> bool:
    target, workdir, _ = _target()
    icon = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "assets" / "app.ico"
    lnk = shortcut_path()
    script = (
        f'$ws = New-Object -ComObject WScript.Shell; '
        f'$s = $ws.CreateShortcut("{lnk}"); '
        f'$s.TargetPath = "{target}"; '
        f'$s.WorkingDirectory = "{workdir}"; '
        f'$s.IconLocation = "{icon}"; '
        f'$s.Description = "GXUST电费助手"; '
        f'$s.Save()'
    )
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                                capture_output=True, timeout=20,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return result.returncode == 0
    except Exception:
        return False


def remove() -> bool:
    try:
        shortcut_path().unlink(missing_ok=True)
        return True
    except OSError:
        return False
