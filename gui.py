"""tkinter 桌面窗口：概览、用量图表、充值、设置、托盘。"""
from __future__ import annotations

import datetime as dt
import ctypes
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, ttk

import logging

import autostart
import notify
import stats
import store
from power_client import (CARD_APP_ID, ELEC_APP_ID, PowerClient, PowerError, Room,
                          decorate_campuses, sort_nodes)
from tray import Tray

BG = "#0f1524"
CARD = "#18223a"
CARD2 = "#1d2a47"
TEXT = "#eef3ff"
MUTED = "#8fa0c4"
ACCENT = "#4da3ff"
GOOD = "#3ddc97"
WARN = "#ffb020"
BAD = "#ff5c6c"
FONT = "Microsoft YaHei UI"
PORTAL_URL = "https://wsjgg02.gxust.edu.cn/Mobile/index.html#/index"
DISCLAIMER = "充值金额从校园卡余额扣除，请预留足够余额；本软件不代管资金，结果以学校系统为准。"


def open_external(url: str) -> bool:
    """优先用 Windows 默认程序打开链接，失败再退回 webbrowser。"""
    try:
        os.startfile(url)  # type: ignore[attr-defined]
        return True
    except Exception:
        pass
    try:
        subprocess.Popen(["cmd", "/c", "start", "", url],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                         close_fds=True)
        return True
    except Exception:
        pass
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


def disable_ime(widget) -> None:
    """关闭指定控件的输入法，避免密码框与中文输入法候选窗冲突。"""
    if sys.platform != "win32":
        return

    def apply(_event=None):
        try:
            ctypes.windll.imm32.ImmAssociateContext(widget.winfo_id(), 0)
        except Exception:
            pass

    widget.bind("<FocusIn>", apply, add="+")
    widget.after(120, apply)


def _font(size: int, bold: bool = False) -> tuple:
    return (FONT, size, "bold") if bold else (FONT, size)


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


class App:
    def __init__(self, root: tk.Tk, poller, background: bool = False):
        self.root = root
        self.poller = poller
        self.background = background
        self._ui_queue: queue.Queue = queue.Queue()
        self._tray_queue: queue.Queue = queue.Queue()
        self._usage_mode = "day"
        self._history_sig = None
        self._prev_low = False
        self._boot_notified = False
        self._card_info = None
        self._history_records: list = []
        self._recharge_busy = False
        self._closing = False
        self._card_loaded_at = 0.0

        cfg = poller.config()
        self._tray = Tray(resource_path("assets/app.png"),
                          on_show=lambda: self._tray_queue.put("show"),
                          on_refresh=lambda: self._tray_queue.put("refresh"),
                          on_exit=lambda: self._tray_queue.put("exit"))
        self._build()
        if background:
            self.root.withdraw()
        self.root.after(50, self._tick)
        self._select_tab(0)
        self._tray.start("GXUST电费助手")
        if not cfg.get("account") or not cfg.get("password_enc"):
            self.root.after(400, self.open_setup)

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = self.root
        root.title("GXUST电费助手")
        root.configure(bg=BG)
        root.geometry("600x748")
        root.minsize(560, 660)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TNotebook", background=BG, borderwidth=0)
        try:
            style.layout("TNotebook", [("Notebook.client", {"sticky": "nswe"})])
        except tk.TclError:
            pass
        style.configure("TNotebook.Tab", background=CARD2, foreground=MUTED,
                        padding=(18, 12), font=_font(10))
        style.map("TNotebook.Tab",
                  background=[("selected", CARD)],
                  foreground=[("selected", TEXT)])
        style.configure("TCombobox", fieldbackground=CARD2, background=CARD2,
                        foreground=TEXT, arrowcolor=TEXT, bordercolor=CARD2)
        style.map("TCombobox", fieldbackground=[("readonly", CARD2)],
                  foreground=[("readonly", TEXT)])
        style.configure("TSpinbox", fieldbackground=CARD2, background=CARD2, foreground=TEXT)
        style.configure("TCheckbutton", background=BG, foreground=TEXT)
        style.configure("TRadiobutton", background=BG, foreground=TEXT)
        style.configure("Treeview", background=CARD2, fieldbackground=CARD2,
                        foreground=TEXT, rowheight=24, borderwidth=0)
        style.configure("Treeview.Heading", background=CARD, foreground=MUTED,
                        font=_font(9))

        top = tk.Frame(root, bg=BG)
        top.pack(fill="x", padx=20, pady=(14, 4))
        self.room_label = tk.Label(top, text="—", bg=BG, fg=MUTED, font=_font(11),
                                   anchor="w", justify="left", wraplength=380)
        self.room_label.pack(side="left", fill="x", expand=True)
        tk.Button(top, text="隐藏到后台", command=self._hide_to_tray, bg=BG, fg=ACCENT,
                  activebackground=BG, relief="flat", bd=0, font=_font(9),
                  cursor="hand2").pack(side="right", padx=(8, 0))
        self.dot = tk.Label(top, text="●", bg=BG, fg=MUTED, font=_font(13))
        self.dot.pack(side="right")

        self.tabbar = tk.Frame(root, bg=BG)
        self.tabbar.pack(fill="x", padx=14, pady=(2, 0))
        self.tab_buttons = {}
        for index, label in enumerate(("概览", "用量", "充值")):
            button = tk.Button(self.tabbar, text=label, relief="flat", bd=0,
                               font=_font(10, True), padx=10, pady=7,
                               command=lambda i=index: self._select_tab(i))
            button.pack(side="left", fill="x", expand=True, padx=(0 if index == 0 else 4, 0))
            self.tab_buttons[index] = button

        self.body = tk.Frame(root, bg=BG)
        self.body.pack(fill="both", expand=True, padx=14, pady=(4, 6))
        self.pages = []
        self._build_overview()
        self._build_usage()
        self._build_recharge()

        footer = tk.Frame(root, bg=BG)
        footer.pack(fill="x", padx=20, pady=(0, 10))
        self.footer_label = tk.Label(footer, text="", bg=BG, fg="#5d6f95",
                                     font=_font(9), anchor="w")
        self.footer_label.pack(side="left")
        tk.Button(footer, text="打开学校门户", command=lambda: open_external(PORTAL_URL),
                  bg=BG, fg=ACCENT, activebackground=BG, relief="flat", bd=0,
                  font=_font(9), cursor="hand2").pack(side="right")

    # -- 概览 -----------------------------------------------------------
    def _build_overview(self) -> None:
        tab = tk.Frame(self.body, bg=BG)
        self.pages.append(tab)

        card = tk.Frame(tab, bg=CARD)
        card.pack(fill="x", padx=2, pady=(8, 6))
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=20, pady=16)

        self.status_text = tk.Label(inner, text="正在读取…", bg=CARD, fg=MUTED,
                                    font=_font(10), anchor="w")
        self.status_text.pack(fill="x")
        row = tk.Frame(inner, bg=CARD)
        row.pack(fill="x", pady=(2, 4))
        self.balance_label = tk.Label(row, text="--", bg=CARD, fg=TEXT,
                                      font=(FONT, 44, "bold"))
        self.balance_label.pack(side="left")
        self.unit_label = tk.Label(row, text="度", bg=CARD, fg=MUTED, font=_font(15))
        self.unit_label.pack(side="left", anchor="s", padx=(8, 0), pady=(0, 10))
        self.meta_label = tk.Label(inner, text="", bg=CARD, fg=MUTED, font=_font(9),
                                   anchor="w")
        self.meta_label.pack(fill="x")

        grid = tk.Frame(inner, bg=CARD)
        grid.pack(fill="x", pady=(12, 0))
        self.stat_values = {}
        items = (("usage24h", "近 24 小时用量"), ("yesterday", "昨日用量"),
                 ("dailyAvg", "日均用量"), ("daysLeft", "预计可用"))
        for col, (key, title) in enumerate(items):
            box = tk.Frame(grid, bg=CARD2)
            box.pack(side="left", fill="x", expand=True, padx=(0 if col == 0 else 5, 0))
            value = tk.Label(box, text="—", bg=CARD2, fg=TEXT, font=_font(13, True))
            value.pack(pady=(7, 0))
            tk.Label(box, text=title, bg=CARD2, fg=MUTED, font=_font(8)).pack(pady=(0, 6))
            self.stat_values[key] = value
        self.estimate_note = tk.Label(inner, text="预计可用按近 7 天数据估算，仅供参考",
                                      bg=CARD, fg="#5d6f95", font=_font(8), anchor="w")
        self.estimate_note.pack(fill="x", pady=(8, 0))

        self.banner = tk.Label(inner, text="", bg=CARD, fg=BAD, font=_font(10),
                               anchor="w", justify="left", wraplength=520)
        self.banner.pack(fill="x", pady=(8, 0))

        actions = tk.Frame(tab, bg=BG)
        actions.pack(fill="x", padx=2, pady=(6, 4))
        tk.Button(actions, text="立即刷新", command=self._manual_refresh,
                  bg=ACCENT, fg="#0b1220", activebackground=ACCENT, relief="flat",
                  bd=0, font=_font(11, True), padx=14, pady=9).pack(side="left", fill="x", expand=True)
        for text, cmd in (("选择房间", self.open_room_picker),
                          ("设置", self.open_settings)):
            tk.Button(actions, text=text, command=cmd, bg=CARD2, fg=TEXT,
                      activebackground=CARD2, relief="flat", bd=0, font=_font(10),
                      padx=10, pady=9).pack(side="left", padx=(6, 0))

        self._overview_tip = tk.Label(
            tab, text="电表在每小时前 10 分钟上传，软件在 :02、:07 自动查询（可在设置改为固定间隔）。",
            bg=BG, fg="#5d6f95", font=_font(9), anchor="w")
        self._overview_tip.pack(fill="x", padx=4)

        info = tk.Frame(tab, bg=CARD)
        info.pack(fill="x", padx=2, pady=(8, 4))
        tk.Label(info, text="使用说明", bg=CARD, fg=MUTED,
                 font=_font(9, True), anchor="w").pack(fill="x", padx=14, pady=(8, 2))
        self._overview_info = tk.Label(
            info,
            text="预计可用：累计满 7 天后按近 7 天用电习惯估算，仅供参考。\n"
                 "充值：实验功能，不一定成功；金额从校园卡余额扣除。\n"
                 "数据：保存在软件所在文件夹，卸载时可选择保留或删除。",
            bg=CARD, fg="#8fa0c4", font=_font(8), justify="left", anchor="w")
        self._overview_info.pack(fill="x", padx=14, pady=(0, 8))

    # -- 用量 -----------------------------------------------------------
    def _build_usage(self) -> None:
        tab = tk.Frame(self.body, bg=BG)
        self.pages.append(tab)
        head = tk.Frame(tab, bg=BG)
        head.pack(fill="x", padx=2, pady=(8, 2))
        tk.Label(head, text="用电统计", bg=BG, fg=MUTED, font=_font(10)).pack(side="left")
        seg = tk.Frame(head, bg=BG)
        seg.pack(side="right")
        self.mode_buttons = {}
        for key, label in (("balance", "余额曲线"), ("day", "日"), ("month", "月"), ("year", "年")):
            btn = tk.Button(seg, text=label, command=lambda k=key: self._set_mode(k),
                            bg=ACCENT if key == self._usage_mode else CARD2,
                            fg=TEXT, activebackground=ACCENT, relief="flat", bd=0,
                            padx=8, pady=3, font=_font(9))
            btn.pack(side="left", padx=3)
            self.mode_buttons[key] = btn

        chart_card = tk.Frame(tab, bg=CARD)
        chart_card.pack(fill="both", expand=True, padx=2, pady=6)
        self.usage_canvas = tk.Canvas(chart_card, bg=CARD, highlightthickness=0)
        self.usage_canvas.pack(fill="both", expand=True, padx=10, pady=10)
        self.usage_canvas.bind("<Configure>", lambda _e: self._draw_usage(force=True))

        self.usage_summary = tk.Label(tab, text="", bg=BG, fg=MUTED, font=_font(9),
                                      anchor="w", justify="left")
        self.usage_summary.pack(fill="x", padx=4, pady=(2, 4))

    # -- 充值 -----------------------------------------------------------
    def _build_recharge(self) -> None:
        tab = tk.Frame(self.body, bg=BG)
        self.pages.append(tab)

        card = tk.Frame(tab, bg=CARD)
        card.pack(fill="x", padx=2, pady=(8, 6))
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=18, pady=14)
        head = tk.Frame(inner, bg=CARD)
        head.pack(fill="x")
        tk.Label(head, text="校园卡", bg=CARD, fg=MUTED, font=_font(10)).pack(side="left")
        tk.Button(head, text="刷新", command=self._load_recharge_data, bg=CARD, fg=ACCENT,
                  activebackground=CARD, relief="flat", bd=0, font=_font(9)).pack(side="right")
        self.card_label = tk.Label(inner, text="点「刷新」读取校园卡余额", bg=CARD,
                                   fg=TEXT, font=_font(13, True), anchor="w")
        self.card_label.pack(fill="x", pady=(4, 2))
        self.card_sub = tk.Label(inner, text="", bg=CARD, fg=MUTED, font=_font(9), anchor="w")
        self.card_sub.pack(fill="x")

        tk.Label(tab, text="⚠ 实验功能，不一定成功！", bg=BG, fg=BAD,
                 font=_font(10, True), anchor="w").pack(fill="x", padx=4, pady=(2, 0))
        warn = tk.Label(tab, text="⚠ " + DISCLAIMER, bg=BG, fg=WARN, font=_font(9),
                        anchor="w", justify="left", wraplength=620)
        warn.pack(fill="x", padx=4, pady=(2, 4))

        wx_row = tk.Frame(tab, bg=BG)
        wx_row.pack(fill="x", padx=4, pady=(0, 4))
        tk.Button(wx_row, text="微信打开充值", command=lambda: self._wechat_qr("power"),
                  bg=CARD2, fg=TEXT, activebackground=CARD2, relief="flat", bd=0,
                  font=_font(9), padx=10, pady=4).pack(side="left")
        tk.Button(wx_row, text="给校园卡充值", command=self._portal_qr,
                  bg=CARD2, fg=TEXT, activebackground=CARD2, relief="flat", bd=0,
                  font=_font(9), padx=10, pady=4).pack(side="left", padx=(6, 0))
        tk.Label(wx_row, text="链接 5 分钟内有效", bg=BG,
                 fg="#5d6f95", font=_font(8)).pack(side="left", padx=(8, 0))

        amount_row = tk.Frame(tab, bg=BG)
        amount_row.pack(fill="x", padx=2, pady=2)
        tk.Label(amount_row, text="充值金额(元)", bg=BG, fg=MUTED, font=_font(10)).pack(side="left")
        self.amount_var = tk.StringVar(value="10")
        self.amount_entry = tk.Entry(amount_row, textvariable=self.amount_var, width=8,
                                     bg=CARD2, fg=TEXT, insertbackground=TEXT,
                                     relief="flat", font=_font(11))
        self.amount_entry.pack(side="left", padx=(6, 10), ipady=4)
        for value in (1, 5, 10, 20):
            tk.Button(amount_row, text=str(value), command=lambda v=value: self.amount_var.set(str(v)),
                      bg=CARD2, fg=TEXT, activebackground=CARD2, relief="flat", bd=0,
                      padx=8, pady=3, font=_font(9)).pack(side="left", padx=2)
        self.recharge_btn = tk.Button(amount_row, text="提交充值", command=self._submit_recharge,
                                      bg=ACCENT, fg="#0b1220", activebackground=ACCENT,
                                      relief="flat", bd=0, font=_font(10, True),
                                      padx=12, pady=5)
        self.recharge_btn.pack(side="right")

        self.recharge_result = tk.Label(tab, text="", bg=BG, fg=MUTED, font=_font(9),
                                        anchor="w", justify="left", wraplength=540)
        self.recharge_result.pack(fill="x", padx=4, pady=(4, 6))

        hist_head = tk.Frame(tab, bg=BG)
        hist_head.pack(fill="x", padx=2, pady=(2, 2))
        tk.Label(hist_head, text="充值记录", bg=BG, fg=MUTED, font=_font(10)).pack(side="left")
        tk.Button(hist_head, text="刷新记录", command=self._load_recharge_data, bg=BG,
                  fg=ACCENT, activebackground=BG, relief="flat", bd=0,
                  font=_font(9)).pack(side="right")

        columns = ("time", "room", "money", "result")
        self.history_tree = ttk.Treeview(tab, columns=columns, show="headings", height=9)
        for col, title, width in (("time", "时间", 150), ("room", "房间", 170),
                                  ("money", "金额", 70), ("result", "结果", 120)):
            self.history_tree.heading(col, text=title)
            self.history_tree.column(col, width=width, anchor="w")
        self.history_tree.pack(fill="both", expand=True, padx=2, pady=(2, 6))


    # ------------------------------------------------------------------
    # 周期刷新界面
    # ------------------------------------------------------------------
    def _tick(self) -> None:
        try:
            self._drain_tray()
            self._drain_ui()
            self._render()
        except Exception:  # noqa: BLE001
            pass
        self.root.after(1000, self._tick)

    def _post(self, func, *args) -> None:
        self._ui_queue.put((func, args))

    def _select_tab(self, index: int) -> None:
        if not 0 <= index < len(getattr(self, "pages", [])):
            return
        for i, page in enumerate(self.pages):
            if i == index:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()
        for i, button in self.tab_buttons.items():
            button.configure(bg=ACCENT if i == index else CARD2,
                             fg="#0b1220" if i == index else TEXT)
        if index == 2 and (not self._card_info or time.time() - self._card_loaded_at > 300):
            self._load_recharge_data()

    def _drain_ui(self) -> None:
        while True:
            try:
                func, args = self._ui_queue.get_nowait()
            except queue.Empty:
                return
            try:
                func(*args)
            except Exception:  # noqa: BLE001
                pass

    def _drain_tray(self) -> None:
        while True:
            try:
                action = self._tray_queue.get_nowait()
            except queue.Empty:
                return
            if action == "show":
                self._show_window()
            elif action == "refresh":
                self._manual_refresh()
            elif action == "exit":
                self._quit()

    def _render(self) -> None:
        state = self.poller.state()
        now = time.time()
        balance = state["balance"]
        unit = state["unit"] or "度"
        stats_data = state.get("stats") or {}

        self.room_label.configure(text=state["roomLabel"] or "还没有选择房间")
        self.unit_label.configure(text=unit)
        if balance is None:
            self.balance_label.configure(text="--", fg=TEXT)
        else:
            self.balance_label.configure(text=f"{balance:.2f}",
                                         fg=BAD if state["low"] else TEXT)
        self.meta_label.configure(
            text=f"更新于 {self._fmt(state['updatedAt'])}    "
                 f"下次刷新 {self._countdown(state['nextAt'], now)}")

        self.stat_values["usage24h"].configure(
            text=f"{stats_data['usage24h']:.2f}" if "usage24h" in stats_data else "—")
        self.stat_values["yesterday"].configure(
            text=f"{stats_data['yesterday']:.2f}" if stats_data.get("yesterday") is not None else "—")
        daily_avg = stats_data.get("dailyAvg")
        self.stat_values["dailyAvg"].configure(
            text=f"{daily_avg:.2f}" if daily_avg is not None else "—")
        if stats_data.get("reliable") and stats_data.get("daysLeft") is not None:
            days = stats_data["daysLeft"]
            text = f"{days:.1f} 天*" if days >= 1 else f"{days * 24:.0f} 小时*"
            self.stat_values["daysLeft"].configure(text=text, fg=WARN if days < 3 else TEXT)
            self.estimate_note.configure(
                text="* 预计可用：按近 7 天用电习惯估算，仅供参考（每间宿舍用电情况不同）")
        else:
            self.stat_values["daysLeft"].configure(text="样本不足", fg=MUTED)
            reason = stats_data.get("reason") or "需要累计至少 7 天数据"
            self.estimate_note.configure(text=f"* 预计可用：{reason}，仅供参考")

        if state["error"]:
            self.status_text.configure(text=state["error"],
                                       fg=WARN if state["stale"] else BAD)
            self.dot.configure(fg=WARN if state["stale"] else BAD)
        elif state["refreshing"]:
            self.status_text.configure(text="正在刷新…", fg=MUTED)
            self.dot.configure(fg=MUTED)
        elif state["ok"]:
            self.status_text.configure(text="数据正常", fg=GOOD)
            self.dot.configure(fg=GOOD)
        else:
            self.status_text.configure(text="等待首次查询…", fg=MUTED)

        self.banner.configure(text="")
        if state["low"]:
            self.banner.configure(text=f"⚠ 电量低于 {state['threshold']:.0f} {unit}，记得及时充值")
        elif state["stale"]:
            self.banner.configure(text="当前显示的是上一次成功数据，可能已经过期")

        footer = state.get("accountName") or ""
        self.footer_label.configure(text=footer)

        self._draw_usage()
        self._update_tray_tooltip(state)
        self._handle_low_alert(state)
        self._maybe_boot_notification(state)

    def _update_tray_tooltip(self, state: dict) -> None:
        if state.get("balance") is None:
            tooltip = "GXUST电费助手 · 等待数据"
        else:
            tooltip = f"GXUST电费助手 {state['balance']:.2f} {state['unit'] or '度'}"
            if state.get("roomLabel"):
                tooltip += f" · {state['roomLabel']}"
        self._tray.update(tooltip)

    def _maybe_boot_notification(self, state: dict) -> None:
        if not self.background or self._boot_notified:
            return
        if not state.get("ok") or state.get("balance") is None:
            return
        self._boot_notified = True
        unit = state["unit"] or "度"
        st = state.get("stats") or {}
        lines = [f"当前 {state['balance']:.2f} {unit}"]
        if st.get("yesterday") is not None:
            lines.append(f"昨日用电 {st['yesterday']:.2f} {unit}")
        if st.get("reliable") and st.get("daysLeft") is not None:
            lines.append(f"预计可用 {st['daysLeft']:.1f} 天（仅供参考）")
        def send():
            ok = notify.show_toast("GXUST电费助手", lines)
            logging.info("开机通知已发送：%s %s", ok, lines)
        threading.Thread(target=send, daemon=True).start()

    def _handle_low_alert(self, state: dict) -> None:
        low = bool(state["low"])
        if low and not self._prev_low:
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception:
                pass
            self.root.title("⚠ GXUST电费助手 · 余额偏低")
        elif not low:
            self.root.title("GXUST电费助手")
        self._prev_low = low

    @staticmethod
    def _fmt(ts: float | None) -> str:
        if not ts:
            return "—"
        return time.strftime("%m-%d %H:%M:%S", time.localtime(ts))

    @staticmethod
    def _countdown(next_at: float | None, now: float) -> str:
        if not next_at:
            return "—"
        remain = int(next_at - now)
        if remain <= 0:
            return "即将刷新"
        minutes, seconds = divmod(remain, 60)
        if minutes >= 60:
            hours, minutes = divmod(minutes, 60)
            return f"{hours}小时{minutes:02d}分"
        return f"{minutes}分{seconds:02d}秒" if minutes else f"{seconds}秒"

    # ------------------------------------------------------------------
    # 图表
    # ------------------------------------------------------------------
    def _set_mode(self, mode: str) -> None:
        self._usage_mode = mode
        for key, button in self.mode_buttons.items():
            button.configure(bg=ACCENT if key == mode else CARD2)
        self._draw_usage(force=True)

    def _history(self) -> list[dict]:
        return self.poller.state().get("history") or []

    def _draw_usage(self, force: bool = False) -> None:
        data = self._history()
        signature = (self._usage_mode, len(data),
                     round(data[-1]["ts"], 1) if data else 0,
                     round(data[-1]["balance"], 2) if data else 0,
                     self.usage_canvas.winfo_width())
        if not force and signature == self._history_sig:
            return
        self._history_sig = signature

        canvas = self.usage_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 260)
        height = max(canvas.winfo_height(), 200)
        now = time.time()

        if self._usage_mode == "balance":
            self._draw_balance_line(canvas, data, width, height)
            return
        if self._usage_mode == "day":
            series = stats.daily_series(data, now, days=30)
            labels_every = 5
            summary = self._usage_summary_day(series)
        elif self._usage_mode == "month":
            series = stats.monthly_series(data, now, months=12)
            labels_every = 1
            summary = self._usage_summary_series(series, "近 12 个月")
        else:
            series = stats.yearly_series(data, now, years=5)
            labels_every = 1
            summary = self._usage_summary_series(series, "近 5 年")
        self._draw_bars(canvas, series, width, height, labels_every)
        self.usage_summary.configure(text=summary)

    def _usage_summary_day(self, series: list[dict]) -> str:
        values = [p["value"] for p in series]
        total = sum(values)
        last7 = sum(values[-7:])
        st = self.poller.state().get("stats") or {}
        if st.get("reliable"):
            return (f"近 30 天合计 {total:.2f} 度，近 7 天合计 {last7:.2f} 度；"
                    f"近 7 天日均 {st['dailyAvg']:.2f} 度（预计可用仅供参考）")
        return f"近 30 天合计 {total:.2f} 度；累计满 7 天后才会给出预计可用（仅供参考）"

    @staticmethod
    def _usage_summary_series(series: list[dict], label: str) -> str:
        total = sum(point["value"] for point in series)
        return f"{label}合计 {total:.2f} 度（数据越久越完整）"

    def _draw_bars(self, canvas: tk.Canvas, series: list[dict], width: int,
                   height: int, labels_every: int) -> None:
        values = [point["value"] for point in series]
        if not values or max(values) <= 0:
            canvas.create_text(width / 2, height / 2, text="还没有足够的用电数据",
                               fill=MUTED, font=_font(9))
            return
        pad_left, pad_right, pad_top, pad_bottom = 40, 10, 24, 30
        plot_w = max(10, width - pad_left - pad_right)
        plot_h = max(10, height - pad_top - pad_bottom)
        peak = max(values)
        slot = plot_w / len(values)
        bar_w = max(2, slot * 0.62)
        canvas.create_line(pad_left, height - pad_bottom, width - pad_right,
                           height - pad_bottom, fill="#2a3a5f")
        canvas.create_text(pad_left - 6, pad_top + 2, text=f"{peak:.1f}", anchor="e",
                           fill=MUTED, font=_font(8))
        canvas.create_text(pad_left - 6, height - pad_bottom - 2, text="0", anchor="e",
                           fill=MUTED, font=_font(8))
        for index, point in enumerate(series):
            value = point["value"]
            bar_h = plot_h * value / peak if peak else 0
            x0 = pad_left + slot * index + (slot - bar_w) / 2
            y0 = height - pad_bottom - bar_h
            color = ACCENT if value > 0 else "#24314f"
            canvas.create_rectangle(x0, y0, x0 + bar_w, height - pad_bottom,
                                    fill=color, outline="")
            if len(series) <= 12 and value > 0:
                canvas.create_text(x0 + bar_w / 2, y0 - 6, text=f"{value:.1f}",
                                   fill=MUTED, font=_font(7))
            if index % labels_every == 0:
                canvas.create_text(x0 + bar_w / 2, height - pad_bottom + 12,
                                   text=point["label"], fill=MUTED, font=_font(7))

    def _draw_balance_line(self, canvas: tk.Canvas, history: list[dict],
                           width: int, height: int) -> None:
        now = time.time()
        points = [point for point in history if point["ts"] >= now - 7 * 86400]
        if len(points) < 2:
            canvas.create_text(width / 2, height / 2,
                               text="样本不足，多刷新几次后就有余额曲线了",
                               fill=MUTED, font=_font(9))
            self.usage_summary.configure(text="余额曲线：最近 7 天")
            return
        self.usage_summary.configure(text="余额曲线：最近 7 天（阶梯上升表示充值）")
        pad_left, pad_right, pad_top, pad_bottom = 44, 12, 16, 28
        xs = [p["ts"] for p in points]
        ys = [p["balance"] for p in points]
        min_x, max_x = min(xs), max(xs)
        data_min, data_max = min(ys), max(ys)
        min_y, max_y = data_min, data_max
        if max_y - min_y < 1:
            mid = (min_y + max_y) / 2
            min_y, max_y = mid - 0.5, mid + 0.5
        pad_y = (max_y - min_y) * 0.18
        min_y -= pad_y
        max_y += pad_y

        def sx(value: float) -> float:
            return pad_left + (value - min_x) / (max_x - min_x or 1) * (width - pad_left - pad_right)

        def sy(value: float) -> float:
            return height - pad_bottom - (value - min_y) / (max_y - min_y or 1) * (height - pad_top - pad_bottom)

        canvas.create_line(pad_left, height - pad_bottom, width - pad_right,
                           height - pad_bottom, fill="#2a3a5f")
        canvas.create_text(pad_left - 6, sy(max_y) + 4, text=f"{data_max:.2f}", anchor="e",
                           fill=MUTED, font=_font(8))
        if data_max - data_min >= 0.05:
            canvas.create_text(pad_left - 6, sy(min_y) - 2, text=f"{data_min:.2f}", anchor="e",
                               fill=MUTED, font=_font(8))
        coords = []
        for point in points:
            coords.extend((sx(point["ts"]), sy(point["balance"])))
        canvas.create_line(*coords, fill=ACCENT, width=2)
        canvas.create_oval(coords[-2] - 3, coords[-1] - 3, coords[-2] + 3, coords[-1] + 3,
                           fill=TEXT, outline="")

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def _manual_refresh(self) -> None:
        self.poller.trigger()
        self.status_text.configure(text="正在刷新…", fg=MUTED)

    def _show_window(self) -> None:
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    def _hide_to_tray(self) -> None:
        try:
            self.root.withdraw()
            self._tray.update("GXUST电费助手 · 后台运行中")
        except Exception:
            pass

    def _on_close(self) -> None:
        action = self.poller.config().get("close_action", "ask")
        if action == "tray":
            self._hide_to_tray()
        elif action == "exit":
            self._quit()
        else:
            self._ask_close()

    def _ask_close(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("关闭窗口")
        dialog.configure(bg=BG)
        dialog.geometry("430x230")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        tk.Label(dialog, text="关闭窗口后要怎么做？", bg=BG, fg=TEXT,
                 font=_font(12, True)).pack(anchor="w", padx=20, pady=(18, 6))
        tk.Label(dialog, text="最小化到后台：程序继续定时查询电量，可从托盘图标重新打开。\n"
                              "直接退出：停止查询，电量不再更新。",
                 bg=BG, fg=MUTED, font=_font(9), justify="left").pack(anchor="w", padx=20)
        remember = tk.BooleanVar(value=True)
        tk.Checkbutton(dialog, text="记住我的选择（以后不再提示，可在设置里修改）",
                       variable=remember, bg=BG, fg=TEXT, selectcolor=CARD2,
                       activebackground=BG, activeforeground=TEXT,
                       font=_font(9)).pack(anchor="w", padx=20, pady=(10, 0))
        buttons = tk.Frame(dialog, bg=BG)
        buttons.pack(fill="x", padx=20, pady=14)

        def choose(action: str) -> None:
            if remember.get():
                cfg = store.load_config()
                cfg["close_action"] = action
                store.save_config(cfg)
                self.poller.apply_settings(cfg)
            dialog.destroy()
            if action == "tray":
                self._hide_to_tray()
            else:
                self._quit()

        tk.Button(buttons, text="最小化到后台", command=lambda: choose("tray"),
                  bg=ACCENT, fg="#0b1220", relief="flat", font=_font(10, True),
                  padx=14, pady=8).pack(side="left")
        tk.Button(buttons, text="直接退出", command=lambda: choose("exit"),
                  bg=CARD2, fg=TEXT, relief="flat", font=_font(10), padx=14,
                  pady=8).pack(side="left", padx=(8, 0))
        tk.Button(buttons, text="取消", command=dialog.destroy, bg=BG, fg=MUTED,
                  relief="flat", font=_font(9)).pack(side="right")

    def _quit(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self._tray.stop()
        finally:
            self.root.destroy()

    # ------------------------------------------------------------------
    # 充值
    # ------------------------------------------------------------------
    def _load_recharge_data(self) -> None:
        if self._recharge_busy:
            return
        self._recharge_busy = True
        self.card_label.configure(text="正在读取校园卡…", fg=MUTED)
        self.card_sub.configure(text="")
        cfg = self.poller.config()

        def work():
            password = store.decrypt_password(cfg.get("password_enc", ""))
            if not cfg.get("account") or not password:
                raise PowerError("还没有填写账号和密码，请点击「设置」")
            client = PowerClient(cfg["account"], password)
            info = client.card_info()
            history = client.session().recharge_history()
            return info, history

        def runner():
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001
                self._post(self._on_recharge_error, getattr(exc, "message", str(exc)))
            else:
                self._post(self._on_recharge_loaded, result[0], result[1])

        threading.Thread(target=runner, daemon=True).start()

    def _on_recharge_loaded(self, info, history) -> None:
        self._recharge_busy = False
        self._card_info = info
        self._history_records = history
        self._card_loaded_at = time.time()
        self.card_label.configure(text=f"余额 {info.balance:.2f} 元", fg=TEXT)
        self.card_sub.configure(text=f"{info.name}  {info.student_number}   （余额来自校园卡充值页面）")
        self.history_tree.delete(*self.history_tree.get_children())
        for record in history:
            self.history_tree.insert("", "end", values=(
                record.time, record.room, f"{record.money:.2f}",
                record.result or ("成功" if record.success else "失败")))
        self.recharge_result.configure(text="", fg=MUTED)

    def _on_recharge_error(self, message: str) -> None:
        self._recharge_busy = False
        self.card_label.configure(text="读取失败", fg=BAD)
        self.card_sub.configure(text=message)
        self.recharge_result.configure(text=message, fg=BAD)

    def _portal_qr(self) -> None:
        self._show_qr("portal", PORTAL_URL)

    def _wechat_qr(self, kind: str = "power") -> None:
        cfg = self.poller.config()
        self.recharge_result.configure(text="正在生成二维码…", fg=MUTED)
        app_id = CARD_APP_ID if kind == "card" else ELEC_APP_ID

        def work():
            password = store.decrypt_password(cfg.get("password_enc", ""))
            client = PowerClient(cfg["account"], password)
            return client.app_link(app_id)

        def runner():
            try:
                link = work()
            except Exception as exc:  # noqa: BLE001
                message = getattr(exc, "message", str(exc))
                self._post(lambda: self.recharge_result.configure(text=message, fg=BAD))
            else:
                self._post(self._show_qr, kind, link)

        threading.Thread(target=runner, daemon=True).start()

    def _show_qr(self, kind: str, link: str) -> None:
        self.recharge_result.configure(text="", fg=MUTED)
        dialog = tk.Toplevel(self.root)
        dialog.title("微信扫码")
        dialog.configure(bg=BG)
        dialog.geometry("430x600")
        dialog.resizable(False, False)
        if kind == "card":
            tk.Label(dialog, text="用微信「扫一扫」打开校园卡充值", bg=BG, fg=TEXT,
                     font=_font(11, True)).pack(pady=(14, 2))
            tk.Label(dialog, text="微信支付充值到校园卡；到账后可用于宿舍电费充值。\n"
                                  "若提示 openid 为空，请改用学校微信公众号入口。",
                     bg=BG, fg=WARN, font=_font(8)).pack()
        elif kind == "portal":
            tk.Label(dialog, text="用微信「扫一扫」打开学校掌上电控", bg=BG, fg=TEXT,
                     font=_font(11, True)).pack(pady=(14, 2))
            tk.Label(dialog, text="扫码后进入掌上电控首页，可查看电费；\n"
                                  "校园卡充值等入口以学校页面为准。",
                     bg=BG, fg=MUTED, font=_font(8)).pack()
        else:
            tk.Label(dialog, text="用微信「扫一扫」打开学校充值页面", bg=BG, fg=TEXT,
                     font=_font(11, True)).pack(pady=(14, 2))
            tk.Label(dialog, text="实验功能，不一定成功；链接含一次性登录票据，请尽快扫码。",
                     bg=BG, fg=WARN, font=_font(8)).pack()
        try:
            import qrcode
            from PIL import ImageTk
            qr = qrcode.QRCode(box_size=6, border=2)
            qr.add_data(link)
            qr.make(fit=True)
            image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            photo = ImageTk.PhotoImage(image)
            label = tk.Label(dialog, image=photo, bg="white")
            label.image = photo
            label.pack(pady=10)
        except Exception:
            tk.Label(dialog, text="二维码组件不可用，请使用下方链接", bg=BG, fg=BAD,
                     font=_font(9)).pack(pady=20)
        link_box = tk.Text(dialog, height=3, wrap="char", bg=CARD2, fg=TEXT,
                           relief="flat", bd=0, font=_font(8), padx=6, pady=4)
        link_box.insert("1.0", link)
        link_box.configure(state="disabled")
        link_box.pack(fill="x", padx=18, pady=(4, 0))
        row = tk.Frame(dialog, bg=BG)
        row.pack(pady=10)
        tk.Button(row, text="复制链接", command=lambda: (dialog.clipboard_clear(),
                  dialog.clipboard_append(link)), bg=ACCENT, fg="#0b1220",
                  relief="flat", font=_font(9), padx=12, pady=5).pack(side="left")
        tk.Button(row, text="重新生成",
                  command=lambda: (dialog.destroy(),
                                   self._portal_qr() if kind == "portal" else self._wechat_qr(kind)),
                  bg=CARD2, fg=TEXT, relief="flat", font=_font(9),
                  padx=12, pady=5).pack(side="left", padx=8)
        tk.Button(row, text="关闭", command=dialog.destroy, bg=BG, fg=MUTED,
                  relief="flat", font=_font(9)).pack(side="left")
        if kind == "card":
            footer = "扫码后进入校园卡充值页面，用微信支付；到账后再用于宿舍电费充值。"
        elif kind == "portal":
            footer = "扫码后进入学校掌上电控首页；校园卡充值等入口以学校页面为准。"
        else:
            footer = "扫码后进入掌上电控首页，点「充值」即可；\n充值金额从校园卡余额扣除，请自行预留余额。"
        tk.Label(dialog, text=footer,
                 bg=BG, fg="#5d6f95", font=_font(8)).pack(pady=(4, 0))

    def _submit_recharge(self) -> None:
        if self._recharge_busy:
            return
        try:
            amount = float(self.amount_var.get())
        except ValueError:
            messagebox.showerror("充值", "充值金额必须是数字", parent=self.root)
            return
        if amount <= 0:
            messagebox.showerror("充值", "充值金额必须大于 0", parent=self.root)
            return
        if amount > 500:
            messagebox.showerror("充值", "单次充值金额过大", parent=self.root)
            return

        cfg = self.poller.config()
        room = Room(cfg["room"]["campus"], cfg["room"]["campusCode"],
                    cfg["room"]["building"], cfg["room"]["buildingCode"],
                    cfg["room"]["room"], cfg["room"]["roomCode"])
        card_text = (f"校园卡余额约 {self._card_info.balance:.2f} 元"
                     if self._card_info else "校园卡余额未知")
        text = (f"向「{room.label()}」充值 {amount:g} 元？\n\n"
                f"{card_text}\n{DISCLAIMER}")
        if not messagebox.askyesno("确认充值", text, parent=self.root, icon="warning"):
            return

        self._recharge_busy = True
        self.recharge_btn.configure(state="disabled")
        self.recharge_result.configure(text="正在提交充值…", fg=MUTED)

        def work():
            password = store.decrypt_password(cfg.get("password_enc", ""))
            client = PowerClient(cfg["account"], password)
            session = client.session()
            message = session.recharge(room, amount)
            return message

        def runner():
            try:
                message = work()
            except Exception as exc:  # noqa: BLE001
                self._post(self._recharge_done, False, getattr(exc, "message", str(exc)))
            else:
                self._post(self._recharge_done, True, message)

        threading.Thread(target=runner, daemon=True).start()

    def _recharge_done(self, success: bool, message: str) -> None:
        self._recharge_busy = False
        self.recharge_btn.configure(state="normal")
        self.recharge_result.configure(text=message, fg=GOOD if success else BAD)
        if success:
            self.poller.trigger()
            self.root.after(4000, self._load_recharge_data)

    # ------------------------------------------------------------------
    # 对话框
    # ------------------------------------------------------------------
    def open_room_picker(self) -> None:
        RoomPicker(self.root, self.poller)

    def open_settings(self) -> None:
        SettingsDialog(self.root, self.poller)

    def open_setup(self) -> None:
        SetupDialog(self.root, self.poller)


# --------------------------------------------------------------------------
# 首次配置向导（必须滚动到底部才能同意，并在本地限制密码错误次数）
# --------------------------------------------------------------------------
SETUP_DISCLAIMER = """GXUST电费助手 · 使用条款与免责声明

1. 本软件完全免费开源。如果你遇到需要付费才能使用的情况，说明软件遭到了修改，请立即删除！

2. 本软件与广西科技大学、贵阳瑞博科技有限公司无关，仅供学习使用。

3. 软件只读取本人账号有权查看的房间电量信息，不绕过学校权限，不代管任何资金。

4. 充值为实验功能，不一定成功；充值金额从校园卡余额扣除，请自行在校园卡中预留足够余额，充值结果以学校系统返回为准。

5. 电量、预计可用天数（按近 7 天估算，仅供参考）、充值记录等数据仅供参考。因学校接口调整、网络故障或数据延迟造成的任何影响，作者不承担责任。

6. 请遵守学校网络与信息系统使用规定，不要频繁刷新或恶意调用接口。

7. 作者 QQ：3104666011　GitHub：@smg123233"""


class SetupDialog(tk.Toplevel):
    def __init__(self, master, poller):
        super().__init__(master)
        self.poller = poller
        self.title("GXUST电费助手 · 首次配置")
        self.configure(bg=BG)
        self.geometry("700x820")
        self.resizable(False, True)
        self.transient(master)
        self._agreed = tk.BooleanVar(value=False)
        self._bottom = False
        self._queue: queue.Queue = queue.Queue()
        self._busy = False
        self._frozen_until = 0.0
        self._picker = None
        self._build()
        self._load_freeze_state()
        self.after(200, self._drain)
        self.after(300, self._check_bottom)

    def _build(self) -> None:
        tk.Label(self, text="欢迎使用 GXUST电费助手", bg=BG, fg=TEXT,
                 font=_font(15, True)).pack(anchor="w", padx=22, pady=(14, 2))
        tk.Label(self, text="首次使用请阅读条款并填写学校账号", bg=BG, fg=MUTED,
                 font=_font(9)).pack(anchor="w", padx=22)

        box = tk.Frame(self, bg=CARD)
        box.pack(fill="both", expand=True, padx=18, pady=8)
        text_frame = tk.Frame(box, bg=CARD)
        text_frame.pack(fill="both", expand=True, padx=12, pady=(10, 0))
        scrollbar = tk.Scrollbar(text_frame, orient="vertical")
        self.text = tk.Text(text_frame, height=10, wrap="char", bg=CARD, fg=TEXT,
                            insertbackground=TEXT, relief="flat", font=_font(9),
                            yscrollcommand=scrollbar.set, padx=6, pady=4)
        scrollbar.configure(command=self.text.yview)
        self.text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.text.insert("1.0", SETUP_DISCLAIMER)
        self.text.configure(state="disabled")
        for event in ("<MouseWheel>", "<Button-4>", "<Button-5>",
                      "<KeyRelease>", "<ButtonRelease-1>"):
            self.text.bind(event, self._check_bottom, add="+")

        self.scroll_hint = tk.Label(
            box, text="请向下滑动阅读到底部，之后才能勾选同意", bg=CARD, fg=WARN,
            font=_font(8), anchor="w")
        self.scroll_hint.pack(anchor="w", padx=12, pady=(4, 0))
        self.agree = tk.Checkbutton(
            box, text="我已阅读并同意以上条款", variable=self._agreed,
            state="disabled", bg=CARD, fg=TEXT, selectcolor=CARD2,
            activebackground=CARD, activeforeground=TEXT, font=_font(10))
        self.agree.pack(anchor="w", padx=12, pady=(2, 8))

        form = tk.Frame(self, bg=BG)
        form.pack(fill="x", padx=22)
        tk.Label(form, text="学校账号（学号）", bg=BG, fg=MUTED,
                 font=_font(10)).pack(anchor="w")
        self.account = tk.Entry(form, bg=CARD2, fg=TEXT, insertbackground=TEXT,
                                relief="flat", font=_font(11))
        self.account.pack(fill="x", ipady=6, pady=(2, 6))
        tk.Label(form, text="密码", bg=BG, fg=MUTED, font=_font(10)).pack(anchor="w")
        self.password = tk.Entry(form, bg=CARD2, fg=TEXT, insertbackground=TEXT,
                                 relief="flat", font=_font(11), show="*")
        self.password.pack(fill="x", ipady=6, pady=(2, 2))
        disable_ime(self.password)
        tk.Label(form, text="提示：初始密码为 Aa@身份证后六位（X 用数字 0 代替）；"
                            "如已修改请填新密码。连续 3 次密码错误会冻结 1 小时。",
                 bg=BG, fg=WARN, font=_font(8), justify="left",
                 wraplength=620).pack(anchor="w")

        room_row = tk.Frame(self, bg=BG)
        self.room_row = room_row
        tk.Label(room_row, text="房间", bg=BG, fg=MUTED, font=_font(10)).pack(side="left")
        self.room_label = tk.Label(room_row, text=self._room_text(), bg=BG, fg=TEXT,
                                   font=_font(10), anchor="w")
        self.room_label.pack(side="left", padx=(8, 12))
        self.room_btn = tk.Button(room_row, text="选择房间", command=self._pick_room,
                                  bg=CARD2, fg=TEXT, relief="flat", font=_font(9),
                                  padx=10, pady=4, state="disabled")
        self.room_btn.pack(side="left")

        opts = tk.Frame(self, bg=BG)
        opts.pack(fill="x", padx=22, pady=(4, 0))
        self.autostart = tk.BooleanVar(value=True)
        tk.Checkbutton(opts, text="开机自动启动（后台静默运行，开机时用 Windows 通知显示电量和昨日用量）",
                       variable=self.autostart, bg=BG, fg=TEXT, selectcolor=CARD2,
                       activebackground=BG, activeforeground=TEXT,
                       font=_font(9)).pack(anchor="w")

        self.status = tk.Label(self, text="", bg=BG, fg=MUTED, font=_font(9),
                               anchor="w", justify="left", wraplength=650)
        self.status.pack(fill="x", padx=22, pady=(6, 0))

        buttons = tk.Frame(self, bg=BG)
        buttons.pack(fill="x", padx=22, pady=12)
        self.save_btn = tk.Button(buttons, text="验证账号并保存", command=self._save,
                                  bg=ACCENT, fg="#0b1220", relief="flat",
                                  font=_font(11, True), padx=18, pady=9)
        self.save_btn.pack(side="right")
        tk.Button(buttons, text="稍后配置", command=self.destroy, bg=CARD2, fg=TEXT,
                  relief="flat", font=_font(10), padx=18, pady=9).pack(side="right", padx=(0, 8))

    # -- 状态 ---------------------------------------------------------
    def _room_text(self) -> str:
        room = self.poller.config().get("room") or {}
        parts = [room.get("campus"), room.get("building"), room.get("room")]
        text = " · ".join(p for p in parts if p)
        return text or "未选择（验证账号后请选择房间）"

    def _load_freeze_state(self) -> None:
        cfg = store.load_config()
        self._frozen_until = float(cfg.get("login_frozen_until") or 0.0)
        if self._frozen_until > time.time():
            self._tick_freeze()
        else:
            self._frozen_until = 0.0

    def _tick_freeze(self) -> None:
        remain = int(self._frozen_until - time.time())
        if remain > 0:
            self.save_btn.configure(state="disabled")
            self.status.configure(
                text=f"密码错误次数过多，已冻结登录，剩余 {remain // 60} 分 {remain % 60:02d} 秒",
                fg=BAD)
            self.after(1000, self._tick_freeze)
        else:
            self._frozen_until = 0.0
            self.save_btn.configure(state="normal")
            self.status.configure(text="已解除冻结，可以重试。", fg=MUTED)

    def _check_bottom(self, _event=None) -> None:
        if self._bottom:
            return
        try:
            _first, last = self.text.yview()
        except tk.TclError:
            return
        if last >= 0.999:
            self._bottom = True
            self.agree.configure(state="normal")
            self.scroll_hint.configure(text="已阅读到底部，请勾选同意")

    # -- 操作 ---------------------------------------------------------
    def _pick_room(self) -> None:
        if self._picker is not None:
            try:
                self._picker.destroy()
            except Exception:
                pass
        self.withdraw()
        self._picker = RoomPicker(self, self.poller,
                                  on_confirm=self._room_confirmed,
                                  on_cancel=self._room_cancelled)

    def _room_confirmed(self) -> None:
        self.room_label.configure(text=self._room_text())
        self._finalize()

    def _room_cancelled(self) -> None:
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except Exception:
            pass
        self.status.configure(text="已取消选择房间，请点「选择房间」重新选择。", fg=WARN)

    def _save(self) -> None:
        if self._busy:
            return
        if not self._agreed.get():
            messagebox.showwarning("首次配置", "请先向下滑动阅读条款并勾选同意。", parent=self)
            return
        account = self.account.get().strip()
        password = self.password.get()
        if not account or not password:
            messagebox.showwarning("首次配置", "请填写学校账号和密码。", parent=self)
            return
        now = time.time()
        cfg = store.load_config()
        self._frozen_until = float(cfg.get("login_frozen_until") or 0.0)
        if self._frozen_until > now:
            self._tick_freeze()
            return

        if not cfg.get("setup_done") or cfg.get("account") != account:
            cfg["room"] = dict(store.DEFAULT_ROOM)
        cfg["account"] = account
        cfg["password_enc"] = store.encrypt_password(password)
        store.save_config(cfg)

        self._busy = True
        self.save_btn.configure(state="disabled")
        self.status.configure(text="正在验证账号密码…", fg=MUTED)
        self.room_btn.configure(state="disabled")

        def work():
            client = PowerClient(account, password)
            client.login()
            return client.user_name

        def runner():
            try:
                name = work()
            except Exception as exc:  # noqa: BLE001
                self._queue.put(("fail", getattr(exc, "message", str(exc))))
            else:
                self._queue.put(("ok", name))

        threading.Thread(target=runner, daemon=True).start()

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "ok":
                    self._login_ok(payload)
                else:
                    self._login_failed(payload)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(150, self._drain)

    def _login_ok(self, name: str) -> None:
        cfg = store.load_config()
        cfg["login_fail_count"] = 0
        cfg["login_frozen_until"] = 0.0
        store.save_config(cfg)
        self.poller.apply_settings(cfg, refresh=False)
        self._busy = False
        self.save_btn.configure(state="normal")
        self.room_btn.configure(state="normal")
        self.status.configure(text=f"账号验证成功（{name or '已登录'}），请选择房间。", fg=GOOD)
        if not self.room_row.winfo_ismapped():
            self.room_row.pack(fill="x", padx=22, pady=(8, 2), before=self.status)
        room = cfg.get("room") or {}
        if room.get("roomCode"):
            self._finalize()
        else:
            self._pick_room()

    def _login_failed(self, message: str) -> None:
        self._busy = False
        cfg = store.load_config()
        count = int(cfg.get("login_fail_count") or 0) + 1
        if count >= 3:
            cfg["login_fail_count"] = 3
            cfg["login_frozen_until"] = time.time() + 3600
            store.save_config(cfg)
            self._frozen_until = cfg["login_frozen_until"]
            self.status.configure(text=f"密码错误 3 次，已冻结登录 1 小时：{message}", fg=BAD)
            self.save_btn.configure(state="disabled")
            self._tick_freeze()
        else:
            cfg["login_fail_count"] = count
            store.save_config(cfg)
            self.save_btn.configure(state="normal")
            self.status.configure(
                text=f"密码错误（第 {count}/3 次），再错 {3 - count} 次将冻结 1 小时：{message}",
                fg=BAD)

    def _finalize(self) -> None:
        cfg = store.load_config()
        room = cfg.get("room") or {}
        if not room.get("roomCode"):
            self.status.configure(text="请先点「选择房间」选择房间。", fg=WARN)
            return
        cfg["setup_done"] = True
        cfg["autostart"] = bool(self.autostart.get())
        store.save_config(cfg)
        autostart.set_enabled(bool(self.autostart.get()))
        self.poller.apply_settings(cfg)
        self.destroy()


# --------------------------------------------------------------------------
# 房间选择对话框
# --------------------------------------------------------------------------
class RoomPicker(tk.Toplevel):
    def __init__(self, master, poller, on_confirm=None, on_cancel=None):
        super().__init__(master)
        self.poller = poller
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel
        self._confirmed = False
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.cfg = store.load_config()
        self._alive = True
        self.title("选择房间")
        self.configure(bg=BG)
        self.geometry("440x330")
        self.resizable(False, False)
        self.queue: queue.Queue = queue.Queue()
        self.campus_nodes: list[dict] = []
        self.building_nodes: list[dict] = []
        self.room_nodes: list[dict] = []
        self.session = None
        self._build()
        self._load_campus()
        self.after(100, self._drain)

    def _build(self) -> None:
        tk.Label(self, text="按顺序选择校区 → 楼栋 → 房间", bg=BG, fg=MUTED,
                 font=_font(10)).pack(anchor="w", padx=20, pady=(18, 4))
        current = (store.load_config().get("room") or {})
        self._has_current = bool(current.get("roomCode"))
        if self._has_current:
            tk.Label(self, text="⚠ 更换宿舍后，新宿舍的用电量、预计可用等数据将从零开始统计；\n"
                                "旧宿舍数据不会迁移，请不要轻易更换宿舍。",
                     bg=BG, fg=WARN, font=_font(8), justify="left",
                     wraplength=400).pack(anchor="w", padx=20, pady=(0, 6))
        form = tk.Frame(self, bg=BG)
        form.pack(fill="x", padx=20)
        self.campus_box = self._row(form, "校区", 0)
        self.building_box = self._row(form, "楼栋", 1)
        self.room_box = self._row(form, "房间", 2)
        self.building_box.configure(state="disabled")
        self.building_box.set("请选择")
        self.room_box.configure(state="disabled")
        self.room_box.set("请选择")
        self.campus_box.bind("<<ComboboxSelected>>", lambda _e: self._load_building())
        self.building_box.bind("<<ComboboxSelected>>", lambda _e: self._load_room())
        self.status = tk.Label(self, text="", bg=BG, fg=MUTED, font=_font(9),
                               anchor="w", justify="left", wraplength=390)
        self.status.pack(fill="x", padx=20, pady=(10, 0))
        buttons = tk.Frame(self, bg=BG)
        buttons.pack(fill="x", padx=20, pady=14)
        tk.Button(buttons, text="确定", command=self._confirm, bg=ACCENT, fg="#0b1220",
                  relief="flat", font=_font(10, True), padx=16, pady=8).pack(side="right")
        tk.Button(buttons, text="取消", command=self._cancel, bg=CARD2, fg=TEXT,
                  relief="flat", font=_font(10), padx=16, pady=8).pack(side="right", padx=(0, 8))

    def _row(self, parent, label: str, row: int) -> ttk.Combobox:
        tk.Label(parent, text=label, bg=BG, fg=TEXT, font=_font(10), width=5,
                 anchor="w").grid(row=row, column=0, sticky="w", pady=6)
        box = ttk.Combobox(parent, state="readonly", font=_font(10))
        box.grid(row=row, column=1, sticky="ew", pady=6, padx=(8, 0))
        parent.grid_columnconfigure(1, weight=1)
        box.set("加载中…")
        return box

    def _client(self) -> PowerClient:
        password = store.decrypt_password(self.cfg.get("password_enc", ""))
        account = self.cfg.get("account", "")
        if not account or not password:
            raise PowerError("请先在「设置」里填写账号和密码")
        return PowerClient(account, password)

    def _bg(self, work, label: str, done) -> None:
        self._set_status(label)

        def runner():
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001
                self.queue.put((done, None, exc))
            else:
                self.queue.put((done, result, None))

        threading.Thread(target=runner, daemon=True).start()

    def _drain(self) -> None:
        if not getattr(self, "_alive", False):
            return
        try:
            while True:
                done, result, error = self.queue.get_nowait()
                if not self._alive:
                    return
                if error is not None:
                    try:
                        self._set_status(f"加载失败：{getattr(error, 'message', error)}")
                    except tk.TclError:
                        return
                else:
                    try:
                        done(result)
                    except tk.TclError:
                        return
        except queue.Empty:
            pass
        try:
            if self._alive and self.winfo_exists():
                self.after(120, self._drain)
        except tk.TclError:
            pass

    def destroy(self) -> None:
        self._alive = False
        super().destroy()

    def _set_status(self, text: str) -> None:
        self.status.configure(text=text)

    def _ensure_session(self):
        if self.session is None:
            self.session = self._client().session()
        return self.session

    def _load_campus(self) -> None:
        self._bg(lambda: self._ensure_session().tree(1),
                 "正在获取校区列表…", self._fill_campus)

    def _fill_campus(self, nodes: list[dict]) -> None:
        self.campus_nodes = decorate_campuses(nodes)
        labels = [n["name"] for n in self.campus_nodes]
        self.campus_box.configure(values=labels)
        self.campus_box.set("请选择")
        self.building_box.configure(values=[], state="disabled")
        self.building_box.set("请选择")
        self.room_box.configure(values=[], state="disabled")
        self.room_box.set("请选择")
        if not labels:
            self._set_status("学校没有返回校区列表")
            return
        self._set_status("请选择校区")

    def _load_building(self) -> None:
        index = self.campus_box.current()
        self.building_box.configure(values=[], state="disabled")
        self.building_box.set("请选择")
        self.room_box.configure(values=[], state="disabled")
        self.room_box.set("请选择")
        if index < 0 or index >= len(self.campus_nodes):
            self._set_status("请选择校区")
            return
        campus = self.campus_nodes[index]
        self._bg(lambda: self._ensure_session().tree(2, campus_code=campus["code"]),
                 f"正在获取「{campus['name']}」的楼栋…", self._fill_building)

    def _fill_building(self, nodes: list[dict]) -> None:
        nodes = sort_nodes(nodes)
        self.building_nodes = nodes
        self.room_nodes = []
        self.room_box.configure(values=[], state="disabled")
        self.room_box.set("请选择")
        labels = self._dedupe([n["name"] for n in nodes])
        self.building_box.configure(values=labels, state="readonly")
        self.building_box.set("请选择")
        self.room_box.set("")
        self.room_box.configure(values=[])
        if not labels:
            self._set_status("该校区没有楼栋")
            return
        self._set_status("请选择楼栋")

    def _load_room(self) -> None:
        index = self.building_box.current()
        self.room_box.configure(values=[], state="disabled")
        self.room_box.set("请选择")
        if index < 0 or index >= len(self.building_nodes):
            self._set_status("请选择楼栋")
            return
        building = self.building_nodes[index]
        self.room_box.configure(state="readonly")
        self._bg(lambda: self._ensure_session().tree(3, building_code=building["code"]),
                 f"正在获取「{building['name']}」的房间…", self._fill_room)

    def _fill_room(self, nodes: list[dict]) -> None:
        nodes = sort_nodes(nodes)
        self.room_nodes = nodes
        labels = self._dedupe([n["name"] for n in nodes])
        self.room_box.configure(values=labels, state="readonly")
        self.room_box.set("请选择")
        if not labels:
            self._set_status("该楼栋没有房间")
            return
        self._set_status(f"共 {len(nodes)} 个房间，请选择房间")

    @staticmethod
    def _dedupe(names: list[str]) -> list[str]:
        counts: dict[str, int] = {}
        result = []
        for name in names:
            counts[name] = counts.get(name, 0) + 1
            result.append(name if counts[name] == 1 else f"{name} ({counts[name]})")
        return result

    def _confirm(self) -> None:
        ci, bi, ri = self.campus_box.current(), self.building_box.current(), self.room_box.current()
        if ci < 0 or bi < 0 or ri < 0:
            self._set_status("请先按顺序选择校区、楼栋、房间")
            return
        campus = self.campus_nodes[ci]
        building = self.building_nodes[bi]
        room = self.room_nodes[ri]
        if getattr(self, "_has_current", False):
            old = store.load_config().get("room") or {}
            changed = (old.get("roomCode") != room["code"]
                       or old.get("buildingCode") != building["code"]
                       or old.get("campusCode") != campus["code"])
            if changed and not messagebox.askyesno(
                    "更换宿舍",
                    f"确定更换到「{campus['name']} · {building['name']} · {room['name']}」？\n\n"
                    "更换后新宿舍的用电量、预计可用等统计将从零开始重新计算，"
                    "旧宿舍数据不会迁移。\n请不要轻易更换宿舍。",
                    parent=self, icon="warning"):
                return
        self.poller.apply_room(Room(
            campus=campus["name"], campus_code=campus["code"],
            building=building["name"], building_code=building["code"],
            room=room["name"], room_code=room["code"],
        ))
        self._confirmed = True
        callback = getattr(self, "on_confirm", None)
        if callable(callback):
            try:
                callback()
            except Exception:
                pass
        self.destroy()

    def _cancel(self) -> None:
        if not getattr(self, "_confirmed", False):
            callback = getattr(self, "on_cancel", None)
            if callable(callback):
                try:
                    callback()
                except Exception:
                    pass
        self.destroy()


# --------------------------------------------------------------------------
# 设置对话框
# --------------------------------------------------------------------------
class SettingsDialog(tk.Toplevel):
    def __init__(self, master, poller):
        super().__init__(master)
        self.poller = poller
        self.cfg = store.load_config()
        self._queue: queue.Queue = queue.Queue()
        self._busy = False
        self._frozen_until = float(self.cfg.get("login_frozen_until") or 0.0)
        self.title("设置")
        self.configure(bg=BG)
        self.geometry("470x950")
        self.resizable(False, False)
        self._build()
        self.after(150, self._drain)
        self.after(300, self._load_freeze_state)

    def _build(self) -> None:
        cfg = self.cfg
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=20, pady=(14, 0))
        tk.Label(body, text="账号", bg=BG, fg=MUTED, font=_font(10)).pack(anchor="w")
        self.account = self._entry(body)
        self.account.insert(0, cfg.get("account", ""))
        tk.Label(body, text="密码（Windows DPAPI 加密保存在本机）", bg=BG, fg=MUTED,
                 font=_font(10)).pack(anchor="w", pady=(8, 2))
        self.password = self._entry(body, show="*")
        disable_ime(self.password)
        try:
            self.password.insert(0, store.decrypt_password(cfg.get("password_enc", "")))
        except Exception:
            pass
        tk.Label(body, text="提示：初始密码为 Aa@身份证后六位（X 用数字 0 代替）；"
                            "如已修改请填新密码。连续 3 次密码错误会冻结 1 小时。",
                 bg=BG, fg=WARN, font=_font(8), justify="left",
                 wraplength=430).pack(anchor="w")

        row = tk.Frame(body, bg=BG)
        row.pack(fill="x", pady=8)
        tk.Label(row, text="刷新间隔(分钟)", bg=BG, fg=MUTED, font=_font(10)).pack(side="left")
        self.interval = ttk.Spinbox(row, from_=1, to=120, width=6, font=_font(10))
        self.interval.set(str(max(1, int(cfg.get("refresh_seconds", 1800) // 60))))
        self.interval.pack(side="left", padx=(6, 16))
        tk.Label(row, text="低电量阈值(度)", bg=BG, fg=MUTED, font=_font(10)).pack(side="left")
        self.threshold = ttk.Spinbox(row, from_=1, to=1000, width=6, font=_font(10))
        self.threshold.set(str(int(cfg.get("low_threshold", 20))))
        self.threshold.pack(side="left", padx=(6, 0))

        mode_row = tk.Frame(body, bg=BG)
        mode_row.pack(fill="x", pady=(2, 0))
        tk.Label(mode_row, text="查询节奏", bg=BG, fg=MUTED, font=_font(10)).pack(anchor="w")
        self.refresh_mode = tk.StringVar(value=cfg.get("refresh_mode", "meter"))
        tk.Radiobutton(mode_row, text="智能抄表：每小时 :02、:07 查询（推荐）",
                       variable=self.refresh_mode, value="meter", bg=BG, fg=TEXT,
                       selectcolor=CARD2, activebackground=BG, activeforeground=TEXT,
                       font=_font(9)).pack(anchor="w")
        tk.Radiobutton(mode_row, text="固定间隔：按上方分钟数查询",
                       variable=self.refresh_mode, value="interval", bg=BG, fg=TEXT,
                       selectcolor=CARD2, activebackground=BG, activeforeground=TEXT,
                       font=_font(9)).pack(anchor="w")

        self.autostart = tk.BooleanVar(value=autostart.is_enabled())
        tk.Checkbutton(body, text="开机自动启动", variable=self.autostart, bg=BG,
                       fg=TEXT, selectcolor=CARD2, activebackground=BG,
                       activeforeground=TEXT, font=_font(10)).pack(anchor="w")
        tk.Label(body, text="开机后在后台静默运行，并通过 Windows 通知显示当前电量和昨日用电量。",
                 bg=BG, fg="#5d6f95", font=_font(8), justify="left",
                 wraplength=400).pack(anchor="w", padx=(24, 0))

        close_row = tk.Frame(body, bg=BG)
        close_row.pack(fill="x", pady=(8, 2))
        tk.Label(close_row, text="关闭窗口时", bg=BG, fg=MUTED, font=_font(10)).pack(side="left")
        self.close_action = ttk.Combobox(close_row, state="readonly", width=16, font=_font(9))
        self.close_action.configure(values=["每次询问", "最小化到后台", "直接退出"])
        mapping = {"ask": "每次询问", "tray": "最小化到后台", "exit": "直接退出"}
        self.close_action.set(mapping.get(cfg.get("close_action", "ask"), "每次询问"))
        self.close_action.pack(side="left", padx=(6, 0))

        about = tk.Frame(body, bg=BG)
        about.pack(fill="x", pady=(10, 0))
        tk.Label(about, text="作者联系方式", bg=BG, fg=MUTED,
                 font=_font(9)).pack(anchor="w")
        tk.Label(about, text="QQ：3104666011　GitHub：@smg123233",
                 bg=BG, fg=TEXT, font=_font(9)).pack(anchor="w")
        links = tk.Frame(about, bg=BG)
        links.pack(anchor="w", pady=(2, 0))
        tk.Button(links, text="添加 QQ", command=self._add_qq,
                  bg=CARD2, fg=TEXT, relief="flat", font=_font(8),
                  padx=8, pady=2).pack(side="left")
        tk.Button(links, text="打开 GitHub",
                  command=lambda: self._open_link("https://github.com/smg123233"),
                  bg=CARD2, fg=TEXT, relief="flat", font=_font(8),
                  padx=8, pady=2).pack(side="left", padx=(6, 0))
        tk.Label(about, text="本软件完全免费开源；如需付费，说明软件被修改，请立即删除。",
                 bg=BG, fg="#5d6f95", font=_font(8), wraplength=400,
                 justify="left").pack(anchor="w", pady=(4, 0))

        support = tk.Frame(body, bg=BG)
        support.pack(fill="x", pady=(12, 0))
        tk.Label(support, text="支持作者", bg=BG, fg=MUTED,
                 font=_font(9, True)).pack(anchor="w")
        tk.Label(support,
                 text="如果喜欢这个软件可以请作者吃根热狗，作者喜欢吃热狗喵。",
                 bg=BG, fg=TEXT, font=_font(9), justify="left",
                 wraplength=420).pack(anchor="w", pady=(2, 6))
        qr_row = tk.Frame(support, bg=BG)
        qr_row.pack(anchor="w")
        self._pay_images = []
        for asset, caption in (("wechat_pay.png", "微信支付"), ("alipay.png", "支付宝")):
            column = tk.Frame(qr_row, bg=BG)
            column.pack(side="left", padx=(0, 12))
            try:
                image = tk.PhotoImage(file=str(resource_path("assets/" + asset)))
                self._pay_images.append(image)
                tk.Label(column, image=image, bg=BG).pack()
            except tk.TclError:
                pass
            tk.Label(column, text=caption, bg=BG, fg=MUTED,
                     font=_font(8)).pack(pady=(2, 0))

        self.status = tk.Label(self, text="", bg=BG, fg=MUTED, font=_font(9),
                               anchor="w", justify="left", wraplength=430)
        self.status.pack(fill="x", padx=20, pady=(4, 0))
        buttons = tk.Frame(self, bg=BG)
        buttons.pack(fill="x", padx=20, pady=14)
        self.save_btn = tk.Button(buttons, text="验证并保存", command=self._save,
                                  bg=ACCENT, fg="#0b1220", relief="flat",
                                  font=_font(10, True), padx=18, pady=8)
        self.save_btn.pack(side="right")
        tk.Button(buttons, text="取消", command=self.destroy, bg=CARD2, fg=TEXT,
                  relief="flat", font=_font(10), padx=18, pady=8).pack(side="right", padx=(0, 8))

    def _entry(self, parent, show: str | None = None) -> tk.Entry:
        entry = tk.Entry(parent, bg=CARD2, fg=TEXT, insertbackground=TEXT,
                         relief="flat", font=_font(11), show=show or "")
        entry.pack(fill="x", ipady=6)
        return entry

    def _add_qq(self) -> None:
        url = ("tencent://AddContact/?fromId=45&fromSubId=1"
               "&subcmd=all&uin=3104666011")
        try:
            os.startfile(url)  # type: ignore[attr-defined]
            return
        except Exception:
            pass
        try:
            self.clipboard_clear()
            self.clipboard_append("3104666011")
        except tk.TclError:
            pass
        messagebox.showinfo(
            "添加 QQ",
            "已复制 QQ 号 3104666011。\n请打开 QQ，在「加好友」里搜索该号码添加。",
            parent=self)

    def _open_link(self, url: str) -> None:
        if open_external(url):
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(url)
        except tk.TclError:
            pass
        messagebox.showinfo("链接", f"无法自动打开浏览器，链接已复制：\n{url}", parent=self)

    def _load_freeze_state(self) -> None:
        cfg = store.load_config()
        self._frozen_until = float(cfg.get("login_frozen_until") or 0.0)
        if self._frozen_until > time.time():
            self._tick_freeze()

    def _tick_freeze(self) -> None:
        remain = int(self._frozen_until - time.time())
        if remain > 0:
            self.save_btn.configure(state="disabled")
            self.status.configure(
                text=f"密码错误次数过多，已冻结登录，剩余 {remain // 60} 分 {remain % 60:02d} 秒",
                fg=BAD)
            self.after(1000, self._tick_freeze)
        else:
            self._frozen_until = 0.0
            self.save_btn.configure(state="normal")
            self.status.configure(text="已解除冻结，可以重试。", fg=MUTED)

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "ok":
                    self._save_ok(payload)
                else:
                    self._save_failed(payload)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(150, self._drain)

    def _save(self) -> None:
        if self._busy:
            return
        account = self.account.get().strip()
        password = self.password.get()
        try:
            minutes = max(1, min(120, int(float(self.interval.get()))))
            threshold = max(1.0, min(1000.0, float(self.threshold.get())))
        except ValueError:
            messagebox.showerror("设置", "刷新间隔 / 阈值必须是数字", parent=self)
            return
        if not account or not password:
            messagebox.showerror("设置", "账号和密码不能为空", parent=self)
            return
        cfg = store.load_config()
        self._frozen_until = float(cfg.get("login_frozen_until") or 0.0)
        if self._frozen_until > time.time():
            self._tick_freeze()
            return
        mapping = {"每次询问": "ask", "最小化到后台": "tray", "直接退出": "exit"}
        self._pending = {
            "account": account,
            "password": password,
            "minutes": minutes,
            "threshold": threshold,
            "close_action": mapping.get(self.close_action.get(), "ask"),
            "refresh_mode": self.refresh_mode.get(),
            "autostart": bool(self.autostart.get()),
        }
        self._busy = True
        self.save_btn.configure(state="disabled")
        self.status.configure(text="正在验证账号密码…", fg=MUTED)

        def work():
            client = PowerClient(account, password)
            client.login()
            return client.user_name

        def runner():
            try:
                name = work()
            except Exception as exc:  # noqa: BLE001
                self._queue.put(("fail", getattr(exc, "message", str(exc))))
            else:
                self._queue.put(("ok", name))

        threading.Thread(target=runner, daemon=True).start()

    def _save_failed(self, message: str) -> None:
        self._busy = False
        cfg = store.load_config()
        count = int(cfg.get("login_fail_count") or 0) + 1
        if count >= 3:
            cfg["login_fail_count"] = 3
            cfg["login_frozen_until"] = time.time() + 3600
            store.save_config(cfg)
            self._frozen_until = cfg["login_frozen_until"]
            self.status.configure(text=f"密码错误 3 次，已冻结登录 1 小时：{message}", fg=BAD)
            self.save_btn.configure(state="disabled")
            self._tick_freeze()
        else:
            cfg["login_fail_count"] = count
            store.save_config(cfg)
            self.save_btn.configure(state="normal")
            self.status.configure(
                text=f"密码错误（第 {count}/3 次），再错 {3 - count} 次将冻结 1 小时：{message}",
                fg=BAD)

    def _save_ok(self, name: str) -> None:
        self._busy = False
        pending = self._pending
        cfg = store.load_config()
        old_account = cfg.get("account", "")
        cfg["account"] = pending["account"]
        cfg["password_enc"] = store.encrypt_password(pending["password"])
        cfg["refresh_seconds"] = pending["minutes"] * 60
        cfg["low_threshold"] = pending["threshold"]
        cfg["close_action"] = pending["close_action"]
        cfg["refresh_mode"] = pending["refresh_mode"]
        cfg["autostart"] = pending["autostart"]
        cfg["login_fail_count"] = 0
        cfg["login_frozen_until"] = 0.0
        room_cleared = pending["account"] != old_account
        if room_cleared:
            cfg["room"] = dict(store.DEFAULT_ROOM)
        store.save_config(cfg)
        autostart.set_enabled(pending["autostart"])
        self.poller.apply_settings(cfg)
        self.destroy()
        text = "已保存。"
        if room_cleared:
            text += "\n账号已更换，请回到主窗口重新选择宿舍。"
        messagebox.showinfo("设置", text, parent=self.master)

def ctypes_get_dc():
    user32 = ctypes.windll.user32
    hdc = user32.GetDC(0)
    return hdc if hdc else 0


def run_app(poller, background: bool = False) -> None:
    root = tk.Tk()
    try:
        icon_path = resource_path("assets/app.ico")
        if icon_path.exists():
            root.iconbitmap(default=str(icon_path))
    except Exception:
        pass
    try:
        hdc = ctypes_get_dc()
        if hdc:
            dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
            if dpi:
                root.tk.call("tk", "scaling", max(1.0, dpi / 72.0))
    except Exception:
        pass
    App(root, poller, background=background)
    root.mainloop()
