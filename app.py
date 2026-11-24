"""程序入口：单实例、日志、轮询线程、Web 服务、桌面窗口。"""
from __future__ import annotations

import ctypes
import datetime as dt
import logging
import os
import re
import shutil
import sys
from pathlib import Path
import threading
import time
from logging.handlers import RotatingFileHandler

import store
from power_client import PowerClient, PowerError, Room
from stats import summarize, usage_between

APP_TITLE = "GXUST电费助手"

ROOM_ERROR_HINTS = ("房间", "层级", "表计", "没有相关数据")


def freeze_seconds(message: str) -> float:
    """学校登录保护：解析「冻结 N 小时/分钟」，返回需要暂停查询的秒数。"""
    match = re.search(r"冻结\s*(\d+)\s*小时", message)
    if match:
        return int(match.group(1)) * 3600 + 60
    match = re.search(r"冻结\s*(\d+)\s*分钟", message)
    if match:
        return int(match.group(1)) * 60 + 5
    if "冻结" in message or "锁定" in message:
        return 3600
    return 0.0


# --------------------------------------------------------------------------
# 日志
# --------------------------------------------------------------------------
def setup_logging() -> None:
    store.DATA_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(store.LOG_PATH, maxBytes=1024 * 1024,
                                  backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        root.addHandler(handler)


# --------------------------------------------------------------------------
# Windows 辅助
# --------------------------------------------------------------------------
def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def acquire_single_instance(activate: bool = True):
    """返回互斥体句柄；已有实例则（可选）激活其窗口并返回 None。"""
    try:
        kernel32 = ctypes.windll.kernel32
        mutex = kernel32.CreateMutexW(None, False, "Local\\DormPowerGui")
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            if activate:
                user32 = ctypes.windll.user32
                hwnd = user32.FindWindowW(None, APP_TITLE)
                if hwnd:
                    user32.ShowWindow(hwnd, 5)
                    user32.SetForegroundWindow(hwnd)
            return None
        return mutex
    except Exception:
        return object()


# --------------------------------------------------------------------------
# 轮询
# --------------------------------------------------------------------------
class Poller:
    def __init__(self, cfg: dict):
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cfg = cfg
        self._room = self._room_from_cfg(cfg)
        self._blocked_until = float(cfg.get("login_frozen_until") or 0.0)
        self._state = {
            "ok": False,
            "balance": None,
            "unit": "度",
            "roomLabel": self._room.label(),
            "updatedAt": None,
            "error": "",
            "stale": False,
            "nextAt": None,
            "refreshSeconds": cfg["refresh_seconds"],
            "threshold": cfg["low_threshold"],
            "low": False,
            "refreshing": False,
            "failCount": 0,
            "history": [],
            "stats": summarize([], time.time(), None),
            "accountName": "",
        }

    # -- 基础 -------------------------------------------------------------
    @staticmethod
    def _room_from_cfg(cfg: dict) -> Room:
        saved = cfg.get("room") or {}
        return Room(
            campus=saved.get("campus", ""), campus_code=saved.get("campusCode", ""),
            building=saved.get("building", ""), building_code=saved.get("buildingCode", ""),
            room=saved.get("room", ""), room_code=saved.get("roomCode", ""),
        )

    def state(self) -> dict:
        with self._lock:
            return dict(self._state)

    def config(self) -> dict:
        with self._lock:
            return self._cfg

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=3)

    def trigger(self) -> None:
        self._wake.set()

    # -- 配置变更 ---------------------------------------------------------
    def apply_settings(self, cfg: dict, refresh: bool = True) -> None:
        with self._lock:
            self._cfg = cfg
            self._room = self._room_from_cfg(cfg)
            self._state["refreshSeconds"] = cfg["refresh_seconds"]
            self._state["threshold"] = cfg["low_threshold"]
            self._state["roomLabel"] = self._room.label()
            self._blocked_until = float(cfg.get("login_frozen_until") or 0.0)
        if refresh:
            self.trigger()

    def apply_room(self, room: Room) -> None:
        cfg = store.load_config()
        cfg["room"] = {
            "campus": room.campus, "campusCode": room.campus_code,
            "building": room.building, "buildingCode": room.building_code,
            "room": room.room, "roomCode": room.room_code,
        }
        store.save_config(cfg)
        with self._lock:
            self._cfg = cfg
            self._room = room
            self._state["roomLabel"] = room.label()
            self._state["balance"] = None
            self._state["history"] = []
            self._state["error"] = ""
        self.trigger()

    # -- 主循环 -----------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            self._refresh_once()
            if self._stop.is_set():
                break
            delay = self._delay_seconds()
            with self._lock:
                self._state["nextAt"] = time.time() + delay
            self._wake.wait(delay)
            self._wake.clear()

    def _delay_seconds(self) -> int:
        now = time.time()
        with self._lock:
            base = max(60, int(self._cfg.get("refresh_seconds", 1800)))
            fails = self._state["failCount"]
            blocked = max(self._blocked_until,
                          float(self._cfg.get("login_frozen_until") or 0.0))
            mode = self._cfg.get("refresh_mode", "meter")
        if blocked > now:
            return int(blocked - now) + 1
        if fails > 0:
            return min(1800, max(600, base))
        if mode == "meter":
            return self._seconds_to_meter(now)
        return base

    @staticmethod
    def _seconds_to_meter(now: float) -> int:
        """电表在每小时前 10 分钟上传，安排在 :02 和 :07 各查一次。"""
        local = dt.datetime.fromtimestamp(now)
        for hour_add in range(0, 3):
            for minute in (2, 7):
                candidate = (local + dt.timedelta(hours=hour_add)).replace(
                    minute=minute, second=0, microsecond=0)
                remaining = candidate.timestamp() - now
                if remaining > 30:
                    return int(remaining)
        return 3600

    def _refresh_once(self) -> None:
        if self._blocked_until > time.time():
            remain = max(0, int(self._blocked_until - time.time()))
            with self._lock:
                self._state["error"] = f"学校临时冻结登录，约 {max(1, remain // 60)} 分钟后自动重试"
                self._state["stale"] = self._state["balance"] is not None
                self._state["nextAt"] = self._blocked_until
            return
        with self._lock:
            cfg = self._cfg
            room = self._room
            self._state["refreshing"] = True
        started = time.time()
        try:
            password = store.decrypt_password(cfg.get("password_enc", ""))
            account = cfg.get("account", "")
            if not account or not password:
                raise PowerError("还没有填写账号和密码，请点击「设置」")
            client = PowerClient(account, password)
            if not room.room_code:
                hint = self._room_label_hint()
                if not hint:
                    raise PowerError("还没有选择房间，请点击「选择房间」")
                room = client.resolve_room(hint)
                self.apply_room(room)
                client = PowerClient(account, password)
            try:
                result = client.fetch_room(room)
            except PowerError as first_error:
                if not room.room_code or not any(
                        hint in first_error.message for hint in ROOM_ERROR_HINTS):
                    raise
                logging.info("房间码可能失效，重新解析：%s", first_error.message)
                fresh = client.resolve_room(self._room_label_hint())
                self.apply_room(fresh)
                client = PowerClient(account, password)
                result = client.fetch_room(fresh)
            self._on_success(result, time.time())
            with self._lock:
                self._state["accountName"] = client.user_name
            logging.info("刷新成功：%s %.2f %s", room.label(), result.balance, result.unit)
        except PowerError as exc:
            self._on_failure(exc.message)
            logging.warning("刷新失败：%s", exc.message)
        except Exception as exc:  # noqa: BLE001 - 兜底，避免线程退出
            self._on_failure(f"未知错误：{exc}")
            logging.exception("刷新异常")
        finally:
            with self._lock:
                self._state["refreshing"] = False
                if self._state["nextAt"] is None:
                    self._state["nextAt"] = time.time() + self._delay_seconds()
            logging.debug("刷新耗时 %.1fs", time.time() - started)

    def _room_label_hint(self) -> str:
        with self._lock:
            cfg = self._cfg
        saved = cfg.get("room") or {}
        parts = [saved.get("campus"), saved.get("building"), saved.get("room")]
        return " ".join(p for p in parts if p)

    def _on_success(self, result, now: float) -> None:
        with self._lock:
            cfg = self._cfg
            room = result.room
            threshold = float(cfg.get("low_threshold", 20.0))
            self._state["ok"] = True
            self._state["balance"] = result.balance
            self._state["unit"] = result.unit
            self._state["roomLabel"] = room.label()
            self._state["updatedAt"] = now
            self._state["nextAt"] = now + self._delay_seconds()
            self._state["error"] = ""
            self._state["stale"] = False
            self._state["failCount"] = 0
            self._state["low"] = result.balance < threshold
            self._blocked_until = float(cfg.get("login_frozen_until") or 0.0)
            self._room = room
        try:
            store.append_sample(now, room.key(), result.balance, result.unit)
        except OSError:
            logging.exception("写入历史失败")
        samples = self._history_for_room(room)
        with self._lock:
            self._state["history"] = [{"ts": s["ts"], "balance": s["balance"]} for s in samples]
            self._state["stats"] = summarize(samples, now, result.balance)

    def _on_failure(self, message: str) -> None:
        blocked = freeze_seconds(message)
        if blocked:
            self._blocked_until = max(self._blocked_until, time.time() + blocked)
            logging.warning("学校登录保护触发，%d 分钟内不再自动重试", int(blocked // 60))
        elif "密码" in message and "错误" in message:
            # 密码错误时也等几分钟，避免连续错误触发冻结
            self._blocked_until = max(self._blocked_until, time.time() + 300)
        with self._lock:
            self._state["failCount"] = self._state["failCount"] + 1
            self._state["error"] = message
            self._state["stale"] = self._state["balance"] is not None
            self._state["nextAt"] = time.time() + self._delay_seconds()

    def _history_for_room(self, room: Room) -> list[dict]:
        with self._lock:
            keep = int(self._cfg.get("history_keep_days", 365))
        try:
            samples = store.load_history(keep)
        except OSError:
            return []
        key = room.key()
        return [s for s in samples if s["room_key"] == key]


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def uninstall_data_prompt() -> int:
    """卸载时询问是否保留用户数据（由 MSI 自定义动作调用）。"""
    import tkinter as tk

    keep = {"value": True}
    root = tk.Tk()
    root.title("GXUST电费助手 卸载")
    root.configure(bg="#0f1524")
    root.geometry("440x250")
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    tk.Label(root, text="是否保留用户数据？", bg="#0f1524", fg="#eef3ff",
             font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w", padx=20, pady=(18, 6))
    tk.Label(root, text="账号配置、历史用量保存在软件文件夹。\n"
                        "保留后下次安装可继续使用；删除后无法恢复。",
             bg="#0f1524", fg="#8fa0c4", justify="left",
             font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=20)

    def choose(value: bool) -> None:
        keep["value"] = value
        root.destroy()

    buttons = tk.Frame(root, bg="#0f1524")
    buttons.pack(fill="x", padx=20, pady=18)
    tk.Button(buttons, text="保留数据", command=lambda: choose(True),
              bg="#4da3ff", fg="#0b1220", relief="flat",
              font=("Microsoft YaHei UI", 10, "bold"), padx=14, pady=8).pack(side="left")
    tk.Button(buttons, text="删除数据", command=lambda: choose(False),
              bg="#ff5c6c", fg="#ffffff", relief="flat",
              font=("Microsoft YaHei UI", 10), padx=14, pady=8).pack(side="left", padx=(8, 0))
    tk.Button(buttons, text="取消（保留）", command=lambda: choose(True),
              bg="#1d2a47", fg="#eef3ff", relief="flat",
              font=("Microsoft YaHei UI", 9), padx=12, pady=8).pack(side="left", padx=(8, 0))
    root.mainloop()

    if not keep["value"]:
        targets = [store.CONFIG_PATH, store.DATA_DIR]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            targets.extend([Path(local) / "DormPower", Path(local) / "GXUSTPower"])
        for target in targets:
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
            except OSError:
                pass
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--uninstall-data-prompt" in args:
        return uninstall_data_prompt()
    background = "--background" in args
    setup_logging()
    enable_dpi_awareness()
    mutex = acquire_single_instance(activate=not background)
    if mutex is None:
        return 0
    cfg = store.load_config()
    try:
        store.prune_history(int(cfg.get("history_keep_days", 1095)))
    except OSError:
        logging.exception("清理历史失败")
    poller = Poller(cfg)
    poller.start()
    try:
        from gui import run_app
        run_app(poller, background=background)
    finally:
        poller.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
