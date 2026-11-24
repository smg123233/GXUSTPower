"""命令行查询（便于验证 / 以后接入计划任务）。

示例：
    python query_cli.py                        # 用 config.json 里保存的房间
    python query_cli.py --room "文昌 南区3栋 101"
    python query_cli.py --room "南3 101" --save-room
    python query_cli.py --tree 1               # 列出校区
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import store
from power_client import PowerClient, PowerError, Room


def _client_from_config(cfg: dict, args) -> PowerClient:
    account = args.account or cfg.get("account", "")
    password = args.password
    if password is None:
        password = store.decrypt_password(cfg.get("password_enc", ""))
    if not account or not password:
        raise PowerError("没有可用账号密码，请先在设置里填写或使用 --account/--password")
    return PowerClient(account, password)


def _room_from_config(cfg: dict) -> Room:
    saved = cfg.get("room") or {}
    return Room(
        campus=saved.get("campus", ""), campus_code=saved.get("campusCode", ""),
        building=saved.get("building", ""), building_code=saved.get("buildingCode", ""),
        room=saved.get("room", ""), room_code=saved.get("roomCode", ""),
    )


def _save_room(room: Room) -> None:
    cfg = store.load_config()
    cfg["room"] = {
        "campus": room.campus, "campusCode": room.campus_code,
        "building": room.building, "buildingCode": room.building_code,
        "room": room.room, "roomCode": room.room_code,
    }
    store.save_config(cfg)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="广西科技大学宿舍电费查询（只读）")
    parser.add_argument("--room", help="房间，如「文昌 南区3栋 101」")
    parser.add_argument("--account", help="覆盖配置里的学号")
    parser.add_argument("--password", help="覆盖配置里的密码")
    parser.add_argument("--tree", type=int, choices=[1, 2, 3], help="列出房间树层级")
    parser.add_argument("--campus", help="配合 --tree 2 使用的校区名称")
    parser.add_argument("--building", help="配合 --tree 3 使用的楼栋名称")
    parser.add_argument("--save-room", action="store_true", help="把解析出的房间写回配置")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = parser.parse_args(argv)

    cfg = store.load_config()
    try:
        client = _client_from_config(cfg, args)
        if args.tree:
            session = client.session()
            if args.tree == 1:
                nodes = session.tree(1)
            elif args.tree == 2:
                campuses = session.tree(1)
                target = next((c for c in campuses if args.campus and args.campus in c["name"]),
                              campuses[0] if campuses else None)
                if not target:
                    raise PowerError("没有可用校区")
                nodes = session.tree(2, campus_code=target["code"])
            else:
                room = client.resolve_room(args.building or args.campus or "文昌 南区3栋 101")
                session = client.session()
                nodes = session.tree(3, building_code=room.building_code)
            for node in nodes:
                print(f"{node.get('name', ''):<24} {node.get('code', '')}")
            return 0

        if args.room:
            room = client.resolve_room(args.room)
            if args.save_room:
                _save_room(room)
        else:
            room = _room_from_config(cfg)
            if not room.room_code:
                raise PowerError("配置里还没有房间，请先运行 --room \"文昌 南区3栋 101\" --save-room")
        result = client.fetch_room(room)
        payload = {
            "ok": True,
            "room": room.label(),
            "balance": result.balance,
            "unit": result.unit,
            "name": client.user_name,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"{room.label()}  {result.balance:.2f} {result.unit}"
                  f"    （{payload['time']}）")
        return 0
    except PowerError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": exc.message}, ensure_ascii=False))
        else:
            print(f"查询失败：{exc.message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
