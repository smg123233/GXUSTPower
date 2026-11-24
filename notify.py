"""Windows 通知（Toast）封装：通过 PowerShell 调用 Windows.UI.Notifications。"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

POWERSHELL_AUMID = ("{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}"
                    "\\WindowsPowerShell\\v1.0\\powershell.exe")

SCRIPT = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml(@'
{payload}
'@)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("{aumid}")
$notifier.Show($toast)
"""


def show_toast(title: str, lines: list[str]) -> bool:
    """弹出系统通知，失败返回 False（不抛异常）。"""
    try:
        body = "".join(
            f"<text>{escape(str(line))}</text>" for line in [title, *lines] if str(line).strip()
        )
        payload = ("<toast><visual><binding template='ToastGeneric'>"
                   f"{body}</binding></visual></toast>")
        script = SCRIPT.format(payload=payload, aumid=POWERSHELL_AUMID)
        path = Path(tempfile.gettempdir()) / "dormpower-toast.ps1"
        path.write_text(script, encoding="utf-8-sig")
        creation = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creation = subprocess.CREATE_NO_WINDOW
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(path)],
            capture_output=True, timeout=15, creationflags=creation,
        )
        return True
    except Exception:
        return False
