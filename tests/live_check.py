"""真实联网冒烟测试：登录门户 → SSO → 查询 config 里的房间余额。

运行：python tests/live_check.py --live
不传 --live 时只打印提示（避免误触发联网）。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import store  # noqa: E402
from power_client import PowerClient, PowerError, Room  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="真正联网查询")
    parser.add_argument("--room", help="临时指定房间，如「文昌 南区12 412」")
    args = parser.parse_args(argv)
    if not args.live:
        print("这是联网测试，请加 --live 运行。")
        return 0

    cfg = store.load_config()
    account = cfg.get("account", "")
    password = store.decrypt_password(cfg.get("password_enc", ""))
    if not account or not password:
        print("config.json 里没有账号密码，请先运行软件在设置里填写。")
        return 2

    client = PowerClient(account, password)
    try:
        started = time.time()
        if args.room:
            room = client.resolve_room(args.room)
        else:
            saved = cfg.get("room") or {}
            room = Room(saved.get("campus", ""), saved.get("campusCode", ""),
                        saved.get("building", ""), saved.get("buildingCode", ""),
                        saved.get("room", ""), saved.get("roomCode", ""))
            if not room.room_code:
                print("配置里没有房间，请先运行 query_cli.py --room ... --save-room")
                return 2
        result = client.fetch_room(room)
        elapsed = time.time() - started
        print(f"OK  {client.user_name}  {room.label()}  {result.balance:.2f} {result.unit}"
              f"  ({elapsed:.1f}s)")
        if result.balance < 0:
            print("警告：余额为负，请尽快充值", file=sys.stderr)
        return 0
    except PowerError as exc:
        print(f"FAIL  {exc.message}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
