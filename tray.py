"""系统托盘图标（pystray，可选依赖）。"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

log = logging.getLogger("tray")


class Tray:
    def __init__(self, icon_path: Path, on_show, on_refresh, on_exit):
        self.icon_path = icon_path
        self.on_show = on_show
        self.on_refresh = on_refresh
        self.on_exit = on_exit
        self.icon = None
        self._thread: threading.Thread | None = None

    def start(self, tooltip: str = "宿舍电费") -> bool:
        try:
            from PIL import Image
            import pystray
        except Exception as exc:  # pragma: no cover
            log.warning("托盘不可用：%s", exc)
            return False
        try:
            image = Image.open(self.icon_path)
            menu = pystray.Menu(
                pystray.MenuItem("显示主窗口", self._show, default=True),
                pystray.MenuItem("立即刷新", self._refresh),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", self._exit),
            )
            self.icon = pystray.Icon("DormPower", image, tooltip, menu)
            self._thread = threading.Thread(target=self.icon.run, name="tray", daemon=True)
            self._thread.start()
            return True
        except Exception as exc:  # pragma: no cover
            log.warning("托盘启动失败：%s", exc)
            return False

    def _show(self, icon=None, item=None):
        try:
            self.on_show()
        except Exception:
            pass

    def _refresh(self, icon=None, item=None):
        try:
            self.on_refresh()
        except Exception:
            pass

    def _exit(self, icon=None, item=None):
        try:
            self.on_exit()
        except Exception:
            pass

    def update(self, tooltip: str) -> None:
        if self.icon is not None:
            try:
                self.icon.title = tooltip
            except Exception:
                pass

    def stop(self) -> None:
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass
            self.icon = None
