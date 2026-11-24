"""配置与历史数据存储。

- config.json：账号、DPAPI 加密后的密码、刷新间隔、低电量阈值、房间信息、Web 端口
- data/history.csv：每次成功查询追加一行（时间戳, 房间, 余额, 单位）
"""
from __future__ import annotations

import base64
import csv
import json
import os
import sys
import time
from pathlib import Path

def _writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _default_base_dir() -> Path:
    """用户数据放在软件所在文件夹；不可写时退回 AppData。"""
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent
    exe_dir = Path(sys.executable).resolve().parent
    local_root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    roaming_root = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    candidates = [
        exe_dir,
        local_root / "GXUSTPower",
        roaming_root / "GXUSTPower",
    ]
    for candidate in candidates:
        if _writable_dir(candidate):
            return candidate
    return exe_dir


BASE_DIR = _default_base_dir()
CONFIG_PATH = BASE_DIR / "config.json"
DATA_DIR = BASE_DIR / "data"
HISTORY_PATH = DATA_DIR / "history.csv"
LOG_PATH = DATA_DIR / "app.log"

DEFAULT_ROOM = {
    "campus": "",
    "campusCode": "",
    "building": "",
    "buildingCode": "",
    "room": "",
    "roomCode": "",
}

DEFAULT_CONFIG = {
    "account": "",
    "password_enc": "",
    "refresh_seconds": 1800,
    "refresh_mode": "meter",
    "low_threshold": 20.0,
    "room": dict(DEFAULT_ROOM),
    "history_keep_days": 1095,
    "close_action": "ask",
    "autostart": False,
    "autostart_asked": False,
    "setup_done": False,
    "desktop_shortcut": True,
    "login_fail_count": 0,
    "login_frozen_until": 0.0,
}

REFRESH_MIN, REFRESH_MAX = 60, 7200
LOW_THRESHOLD_MIN, LOW_THRESHOLD_MAX = 1.0, 10000.0


# --------------------------------------------------------------------------
# Windows DPAPI（仅当前 Windows 用户可解密）
# --------------------------------------------------------------------------
def _dpapi_available() -> bool:
    return sys.platform == "win32"


def _dpapi_crypt(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    def make_blob(payload: bytes):
        buf = ctypes.create_string_buffer(payload, len(payload))
        blob = DATA_BLOB(len(payload), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        return blob, buf

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR,
                   ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
                   ctypes.c_void_p, wintypes.DWORD,
                   ctypes.POINTER(DATA_BLOB)]
    fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    in_blob, _keep = make_blob(data)
    out_blob = DATA_BLOB()
    ok = fn(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob))
    if not ok:
        raise OSError("DPAPI 调用失败")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))


def encrypt_password(plain: str) -> str:
    if not plain:
        return ""
    if _dpapi_available():
        try:
            return "dpapi:" + base64.b64encode(_dpapi_crypt(plain.encode("utf-8"), True)).decode("ascii")
        except Exception:
            pass
    return "plain:" + plain


def decrypt_password(stored: str) -> str:
    if not stored:
        return ""
    if stored.startswith("dpapi:"):
        payload = base64.b64decode(stored[len("dpapi:"):])
        return _dpapi_crypt(payload, False).decode("utf-8")
    if stored.startswith("plain:"):
        return stored[len("plain:"):]
    return stored


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
def _merge_config(raw: dict) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if not isinstance(raw, dict):
        return cfg
    for key in ("account", "password_enc"):
        if isinstance(raw.get(key), str):
            cfg[key] = raw[key]
    if isinstance(raw.get("refresh_seconds"), (int, float)):
        cfg["refresh_seconds"] = int(min(REFRESH_MAX, max(REFRESH_MIN, raw["refresh_seconds"])))
    if raw.get("refresh_mode") in ("meter", "interval"):
        cfg["refresh_mode"] = raw["refresh_mode"]
    if isinstance(raw.get("low_threshold"), (int, float)):
        cfg["low_threshold"] = float(min(LOW_THRESHOLD_MAX, max(LOW_THRESHOLD_MIN, raw["low_threshold"])))
    if isinstance(raw.get("history_keep_days"), (int, float)):
        cfg["history_keep_days"] = int(min(3650, max(7, raw["history_keep_days"])))
    if raw.get("close_action") in ("ask", "tray", "exit"):
        cfg["close_action"] = raw["close_action"]
    if isinstance(raw.get("autostart"), bool):
        cfg["autostart"] = raw["autostart"]
    if isinstance(raw.get("autostart_asked"), bool):
        cfg["autostart_asked"] = raw["autostart_asked"]
    if isinstance(raw.get("setup_done"), bool):
        cfg["setup_done"] = raw["setup_done"]
    if isinstance(raw.get("desktop_shortcut"), bool):
        cfg["desktop_shortcut"] = raw["desktop_shortcut"]
    if isinstance(raw.get("login_fail_count"), (int, float)):
        cfg["login_fail_count"] = max(0, int(raw["login_fail_count"]))
    if isinstance(raw.get("login_frozen_until"), (int, float)):
        cfg["login_frozen_until"] = float(raw["login_frozen_until"])
    room = raw.get("room")
    if isinstance(room, dict):
        for key in DEFAULT_ROOM:
            if isinstance(room.get(key), str):
                cfg["room"][key] = room[key]
    return cfg


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return json.loads(json.dumps(DEFAULT_CONFIG))
    return _merge_config(raw)


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    normalized = _merge_config(cfg)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)


# --------------------------------------------------------------------------
# 历史记录
# --------------------------------------------------------------------------
def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def append_sample(ts: float, room_key: str, balance: float, unit: str,
                  min_gap_seconds: int = 300) -> bool:
    """追加一条采样；与上一条同一房间、同一数值且间隔过近则跳过。

    返回是否真正写入。
    """
    _ensure_data_dir()
    last = _last_sample()
    if last and last["room_key"] == room_key and abs(last["balance"] - balance) < 1e-9:
        if ts - last["ts"] < min_gap_seconds:
            return False
    new_file = not HISTORY_PATH.exists()
    with HISTORY_PATH.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if new_file:
            writer.writerow(["timestamp", "room_key", "balance", "unit"])
        writer.writerow([f"{ts:.0f}", room_key, f"{balance:.2f}", unit])
    return True


def _last_sample() -> dict | None:
    if not HISTORY_PATH.exists():
        return None
    try:
        with HISTORY_PATH.open("r", newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    except OSError:
        return None
    if not rows:
        return None
    row = rows[-1]
    try:
        return {"ts": float(row["timestamp"]), "room_key": row["room_key"],
                "balance": float(row["balance"]), "unit": row.get("unit", "")}
    except (KeyError, ValueError):
        return None


def load_history(keep_days: int | None = None) -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    try:
        with HISTORY_PATH.open("r", newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    except OSError:
        return []
    cutoff = time.time() - keep_days * 86400 if keep_days else None
    result = []
    for row in rows:
        try:
            ts = float(row["timestamp"])
            balance = float(row["balance"])
        except (KeyError, ValueError):
            continue
        if cutoff is not None and ts < cutoff:
            continue
        result.append({"ts": ts, "room_key": row.get("room_key", ""),
                       "balance": balance, "unit": row.get("unit", "")})
    result.sort(key=lambda item: item["ts"])
    return result


def prune_history(keep_days: int) -> int:
    """删除超过保留期的历史行，返回删除条数。"""
    if not HISTORY_PATH.exists():
        return 0
    cutoff = time.time() - keep_days * 86400
    try:
        with HISTORY_PATH.open("r", newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    except OSError:
        return 0
    kept, dropped = [], 0
    for row in rows:
        try:
            ts = float(row["timestamp"])
        except (KeyError, ValueError):
            dropped += 1
            continue
        if ts < cutoff:
            dropped += 1
        else:
            kept.append(row)
    if dropped == 0:
        return 0
    tmp = HISTORY_PATH.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["timestamp", "room_key", "balance", "unit"])
        writer.writeheader()
        writer.writerows(kept)
    os.replace(tmp, HISTORY_PATH)
    return dropped


def room_key(cfg_room: dict) -> str:
    return f"{cfg_room.get('building', '')}/{cfg_room.get('room', '')}"
