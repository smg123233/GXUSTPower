"""广西科技大学「掌上电控」查询客户端（纯标准库）。

调用链：
    wsjgg02 统一门户登录 -> 绑定站点 -> 取「掌上电控」SSO 链接
    -> zywxhd02 换取 Cookie/防伪令牌 -> 房间树 -> 房间余额

本模块包含查询、充值入口、充值历史和校园卡信息接口；软件不代管资金。
"""
from __future__ import annotations

import html
import http.cookiejar
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

PORTAL_BASE = "https://wsjgg02.gxust.edu.cn"
ELEC_BASE = "https://zywxhd02.gxust.edu.cn"
ELEC_APP_ID = "73e35bc9a89f4d4393c2ee6327f20fca"
CARD_APP_ID = "52f1a607832f40eb8ee4d349b4e8686c"
DEFAULT_MECHANISM_ID = "3eb34b8a7d2a4a5d9af320846906987c"
DEFAULT_SITES_ID = "78d97774caa1419a9704d7ecd3e36f01"

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)

CAMPUS_HINTS = ("文昌", "柳石", "箭盘", "柳东")

FORM_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Referer": ELEC_BASE + "/",
    "Origin": ELEC_BASE,
}


class PowerError(Exception):
    """对用户可读的查询错误。"""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail


@dataclass
class Room:
    campus: str
    campus_code: str
    building: str
    building_code: str
    room: str
    room_code: str

    def key(self) -> str:
        return f"{self.building}/{self.room}"

    def label(self) -> str:
        parts = [p for p in (self.campus, self.building, self.room) if p]
        return " · ".join(parts)


@dataclass
class QueryResult:
    balance: float
    unit: str
    balance_text: str
    room: Room
    raw: dict


@dataclass
class CardInfo:
    name: str
    student_number: str
    balance: float
    balance_text: str


@dataclass
class RechargeRecord:
    month: str
    room: str
    result: str
    success: bool
    money: float
    time: str


# --------------------------------------------------------------------------
# 纯函数（便于单元测试）
# --------------------------------------------------------------------------
def parse_antiforgery(page: str) -> str:
    for match in re.finditer(r"<input\b[^>]*>", page, re.IGNORECASE):
        tag = match.group(0)
        if "__RequestVerificationToken" in tag:
            value = re.search(r'value="([^"]*)"', tag)
            if value:
                return html.unescape(value.group(1))
    return ""


def parse_student_number(page: str) -> str:
    match = re.search(r'id="StudentNumber"[^>]*value="([^"]*)"', page, re.IGNORECASE)
    if not match:
        match = re.search(r'name="StudentNumber"[^>]*value="([^"]*)"', page, re.IGNORECASE)
    return html.unescape(match.group(1)) if match else ""


def normalize_name(text: str) -> str:
    if not text:
        return ""
    text = text.replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", "", text).lower()


def digits_of(text: str) -> str:
    return "".join(re.findall(r"\d+", text or ""))


def parse_unit(balance_type: str) -> str:
    text = (balance_type or "").strip()
    if not text:
        return "度"
    match = re.search(r"[（(]([^）)]+)[）)]", text)
    if match:
        return match.group(1).strip() or "度"
    if "电量" in text or "度" in text:
        return "度"
    if "金额" in text or "费" in text or "元" in text:
        return "元"
    return text


def parse_room_query(text: str) -> dict:
    """把「文昌 南区3栋 101」/「南12-412」解析成校区/楼栋/房间提示。"""
    result = {"campus": None, "building": None, "room": None}
    if not text:
        return result
    cleaned = re.sub(r"[,，、/\\|;；]+", " ", text)
    tokens = [tok for tok in cleaned.split() if tok]
    if len(tokens) == 1:
        token = tokens[0]
        if "-" in token:
            tokens = [part for part in token.split("-") if part]
        elif "－" in token:
            tokens = [part for part in token.split("－") if part]
        else:
            groups = list(re.finditer(r"\d+", token))
            if len(groups) >= 2 and groups[-1].end() == len(token):
                split_at = groups[-1].start()
                prefix = token[:split_at]
                if prefix and not prefix[-1].isdigit():
                    tokens = [prefix, groups[-1].group(0)]
    campus_tokens, other = [], []
    for token in tokens:
        if token.endswith("校区") or any(hint in token for hint in CAMPUS_HINTS):
            campus_tokens.append(token)
        else:
            other.append(token)
    if campus_tokens:
        result["campus"] = campus_tokens[0]
    numeric = [tok for tok in other if tok.isdigit()]
    if numeric:
        result["room"] = numeric[-1]
    building_tokens = [tok for tok in other if not tok.isdigit()]
    if building_tokens:
        result["building"] = building_tokens[0]
    return result


def match_campus(nodes: list[dict], hint: str | None) -> list[dict]:
    if not hint:
        return list(nodes)
    norm_hint = normalize_name(hint)
    hits = [n for n in nodes if normalize_name(n.get("name", "")) == norm_hint]
    if hits:
        return hits
    hits = [n for n in nodes if norm_hint in normalize_name(n.get("name", ""))]
    if hits:
        return hits
    keyword = next((h for h in CAMPUS_HINTS if h in hint), None)
    if keyword:
        return [n for n in nodes if keyword in n.get("name", "")]
    return []


def match_building(nodes: list[dict], hint: str | None) -> list[dict]:
    if not hint:
        return list(nodes)
    norm_hint = normalize_name(hint)
    hits = [n for n in nodes if normalize_name(n.get("name", "")) == norm_hint]
    if hits:
        return hits
    hits = [n for n in nodes if norm_hint in normalize_name(n.get("name", ""))]
    if hits:
        return hits
    hint_digits = digits_of(hint)
    region = "南" if "南" in hint else ("北" if "北" in hint else "")
    candidates = []
    for node in nodes:
        name = node.get("name", "")
        if hint_digits and digits_of(name) == hint_digits:
            if not region or region in name:
                candidates.append(node)
    return candidates


def match_room(nodes: list[dict], hint: str | None) -> list[dict]:
    if not hint:
        return list(nodes)
    norm_hint = normalize_name(hint)
    hits = [n for n in nodes if normalize_name(n.get("name", "")) == norm_hint]
    if hits:
        return hits
    return [n for n in nodes if norm_hint in normalize_name(n.get("name", ""))]


def parse_card_info(page: str) -> CardInfo:
    def field(label: str) -> str:
        pattern = (rf'<div class="name">{re.escape(label)}</div>\s*'
                   rf'<div class="info">([^<]*)</div>')
        match = re.search(pattern, page)
        return html.unescape(match.group(1)).strip() if match else ""

    balance_text = field("余额")
    match = re.search(r"-?\d+(?:\.\d+)?", balance_text)
    balance = float(match.group(0)) if match else 0.0
    return CardInfo(name=field("姓名"), student_number=field("学工号"),
                    balance=balance, balance_text=balance_text)


def parse_recharge_history(data) -> list[RechargeRecord]:
    if isinstance(data, str):
        try:
            data = json.loads(data) if data.strip() else []
        except ValueError:
            return []
    if not isinstance(data, list):
        return []
    records: list[RechargeRecord] = []
    for month in data:
        if not isinstance(month, dict):
            continue
        month_name = str(month.get("YearsMonth") or "")
        for item in month.get("RechargeHistorys") or []:
            if not isinstance(item, dict):
                continue
            try:
                money = float(item.get("Money") or 0)
            except (TypeError, ValueError):
                money = 0.0
            records.append(RechargeRecord(
                month=month_name,
                room=str(item.get("RoomInfo") or ""),
                result=str(item.get("RechargeRsult") or ""),
                success=bool(item.get("IsEntry")),
                money=money,
                time=str(item.get("DateTime") or ""),
            ))
    records.sort(key=lambda r: r.time, reverse=True)
    return records


# --------------------------------------------------------------------------
# HTTP 基础
# --------------------------------------------------------------------------
def _error_message(body: str) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return ""
    message = data.get("message") or data.get("Message") or ""
    if isinstance(message, str):
        return message.strip()
    return ""


class PowerClient:
    def __init__(self, account: str, password: str, timeout: int = 20):
        self.account = (account or "").strip()
        self.password = password or ""
        self.timeout = timeout
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar)
        )
        self._mechanism_id: str | None = None
        self._portal_token: str | None = None
        self._sites_id: str | None = None
        self.user_name = ""

    # -- 底层请求 ---------------------------------------------------------
    def _open(self, request: urllib.request.Request) -> str:
        try:
            with self._opener.open(request, timeout=self.timeout) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, "replace")
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")
            except Exception:
                pass
            message = _error_message(body)
            if not message:
                message = f"学校服务器返回 HTTP {exc.code}"
            raise PowerError(message, body[:500]) from None
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if "timed out" in str(reason).lower() or "timeout" in str(reason).lower():
                raise PowerError("连接学校服务器超时，请检查网络后重试") from None
            raise PowerError("网络连接失败，请检查网络后重试") from None
        except TimeoutError:
            raise PowerError("连接学校服务器超时，请检查网络后重试") from None

    def _post_json(self, path: str, payload: dict, token: str | None = None) -> dict:
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": USER_AGENT,
            "Referer": PORTAL_BASE + "/Mobile/index.html",
        }
        if token:
            headers["token"] = token
        request = urllib.request.Request(
            PORTAL_BASE + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        text = self._open(request)
        try:
            data = json.loads(text)
        except ValueError:
            raise PowerError("门户返回内容无法解析，学校接口可能已改版", text[:300]) from None
        if not data.get("isSuccess", False):
            message = (data.get("message") or "").strip() or "门户接口返回失败"
            raise PowerError(message, text[:300])
        return data

    def _get_text(self, url: str) -> str:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml,*/*",
                "User-Agent": USER_AGENT,
            },
        )
        return self._open(request)

    def _post_form(self, path: str, fields: dict) -> dict:
        body = urllib.parse.urlencode(fields).encode("utf-8")
        headers = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                   "Accept": "application/json, text/javascript, */*; q=0.01"}
        headers.update(FORM_HEADERS)
        request = urllib.request.Request(ELEC_BASE + path, data=body, headers=headers,
                                         method="POST")
        text = self._open(request)
        try:
            data = json.loads(text)
        except ValueError:
            raise PowerError("电控页面返回内容无法解析，学校接口可能已改版", text[:300]) from None
        if not data.get("isSuccess", False):
            message = (data.get("message") or "").strip() or "电控接口返回失败"
            if "层级" in message:
                message = "房间层级不正确"
            raise PowerError(message, text[:300])
        return data

    # -- 门户登录 ---------------------------------------------------------
    def mechanism_id(self) -> str:
        if self._mechanism_id:
            return self._mechanism_id
        try:
            data = self._post_json("/ViewControllers/MobileView/GetMechanismInfos", {})
            items = data.get("dataModel") or []
            if items and items[0].get("id"):
                self._mechanism_id = items[0]["id"]
                return self._mechanism_id
        except PowerError:
            pass
        self._mechanism_id = DEFAULT_MECHANISM_ID
        return self._mechanism_id

    def login(self) -> str:
        if self._portal_token:
            return self._portal_token
        if not self.account or not self.password:
            raise PowerError("请先在「设置」里填写学号和密码")
        data = self._post_json(
            "/ViewControllers/MobileView/UserLogin",
            {
                "account": self.account,
                "password": self.password,
                "mechanismId": self.mechanism_id(),
                "openId": "",
            },
        )
        model = data.get("dataModel") or {}
        token = model.get("token")
        if not token:
            raise PowerError("登录失败：未获取到 token")
        user = model.get("userInfo") or {}
        self._sites_id = user.get("sitesId")
        self.user_name = user.get("name") or ""
        self._portal_token = token
        if not self._sites_id:
            self._sites_id = self._fetch_sites_id()
        if self._sites_id:
            self._bind_token(token)
        return token

    def _fetch_sites_id(self) -> str:
        try:
            data = self._post_json(
                "/ViewControllers/MobileView/GetSitesInfos",
                {"mechanismId": self.mechanism_id()},
                token=self._portal_token,
            )
            items = data.get("dataModel") or []
            if items and items[0].get("id"):
                return items[0]["id"]
        except PowerError:
            pass
        return DEFAULT_SITES_ID

    def _bind_token(self, token: str) -> None:
        try:
            self._post_json(
                "/ViewControllers/MobileView/UpdateUserToken",
                {"token": token, "mechanismId": self.mechanism_id(),
                 "sitesId": self._sites_id, "openId": ""},
                token=token,
            )
        except PowerError:
            # 绑定失败不影响后续 SSO，绝大多数情况下仍然可用
            pass

    # -- 电控会话 ---------------------------------------------------------
    def app_link(self, app_id: str) -> str:
        token = self.login()
        data = self._post_json(
            "/ViewControllers/MobileView/GetApplicationRedirectUrl",
            {"applicationId": app_id, "sitesId": self._sites_id},
            token=token,
        )
        link = (data.get("dataModel") or {}).get("linkUrl") or ""
        if not link:
            raise PowerError("未能获取应用入口，请稍后重试")
        return link

    def session(self) -> "ElecSession":
        link = self.app_link(ELEC_APP_ID)
        page = self._get_text(link)
        antiforgery = parse_antiforgery(page)
        student_number = parse_student_number(page)
        if not antiforgery or not student_number:
            raise PowerError("电控页面结构可能已更新，暂时无法解析登录信息")
        return ElecSession(self, antiforgery, student_number)

    def card_info(self) -> CardInfo:
        """读取校园卡余额（校园门户 -> 卡片充值 页面）。"""
        page = self._get_text(self.app_link(CARD_APP_ID))
        info = parse_card_info(page)
        if not info.student_number and not info.name:
            raise PowerError("校园卡页面解析失败，学校页面可能已改版")
        return info

    def set_cookie(self, base_url: str, name: str, value: str) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        cookie = http.cookiejar.Cookie(
            version=0, name=name, value=urllib.parse.quote(value, safe=""),
            port=None, port_specified=False, domain=parsed.hostname or "",
            domain_specified=True, domain_initial_dot=False, path="/",
            path_specified=True, secure=parsed.scheme == "https", expires=None,
            discard=False, comment=None, comment_url=None, rest={}, rfc2109=False,
        )
        self._jar.set_cookie(cookie)

    # -- 高层查询 ---------------------------------------------------------
    def fetch_room(self, room: Room) -> QueryResult:
        session = self.session()
        return session.balance(room)

    def resolve_room(self, label: str) -> Room:
        hints = parse_room_query(label)
        if not hints["campus"] and not hints["building"] and not hints["room"]:
            raise PowerError("房间格式无法识别，请写成「文昌 南区3栋 101」这种形式")
        session = self.session()
        campuses = decorate_campuses(session.tree(1))
        candidates = match_campus(campuses, hints["campus"])
        if not candidates:
            names = "、".join(c["name"] for c in campuses) or "（空）"
            raise PowerError(f"没有找到校区「{hints['campus'] or label}」，可选：{names}")

        chosen: tuple[dict, list[dict]] | None = None
        fallback: tuple[dict, list[dict]] | None = None
        for campus in candidates:
            buildings = sort_nodes(session.tree(2, campus_code=campus["code"]))
            if hints["building"]:
                matched = match_building(buildings, hints["building"])
                if matched:
                    chosen = (campus, buildings)
                    hints["building"] = matched[0]["name"]
                    break
                if fallback is None:
                    fallback = (campus, buildings)
            else:
                dorm_like = [b for b in buildings if "公寓" in b.get("name", "")]
                if dorm_like:
                    chosen = (campus, buildings)
                    hints["building"] = dorm_like[0]["name"]
                    break
                if fallback is None:
                    fallback = (campus, buildings)
        if chosen is None:
            chosen = fallback
        if chosen is None:
            raise PowerError("没有找到可用楼栋")

        campus, buildings = chosen
        if not hints["building"]:
            raise PowerError("没有识别出楼栋，请写成「文昌 南区3栋 101」这种形式")
        matched_buildings = match_building(buildings, hints["building"])
        if not matched_buildings:
            names = "、".join(b.get("name", "") for b in buildings[:30])
            raise PowerError(f"在「{campus['name']}」下没有找到楼栋「{hints['building']}」，可选：{names}")
        building = matched_buildings[0]

        rooms = sort_nodes(session.tree(3, building_code=building["code"]))
        if not hints["room"]:
            raise PowerError("没有识别出房间号，请写成「文昌 南区3栋 101」这种形式")
        matched_rooms = match_room(rooms, hints["room"])
        if not matched_rooms:
            sample = "、".join(r.get("name", "") for r in rooms[:40])
            raise PowerError(f"楼栋「{building['name']}」下没有找到房间「{hints['room']}」，可选：{sample}")
        room = matched_rooms[0]
        return Room(
            campus=campus["name"], campus_code=campus["code"],
            building=building["name"], building_code=building["code"],
            room=room["name"], room_code=room["code"],
        )


class ElecSession:
    """一次 SSO 会话，可连续查询房间树与余额。"""

    def __init__(self, client: PowerClient, antiforgery: str, student_number: str):
        self.client = client
        self.antiforgery = antiforgery
        self.student_number = student_number

    def _fields(self, **extra) -> dict:
        fields = {
            "studentNumber": self.student_number,
            "__RequestVerificationToken": self.antiforgery,
        }
        fields.update(extra)
        return fields

    def tree(self, level: int, campus_code: str | None = None,
             building_code: str | None = None) -> list[dict]:
        fields = self._fields(level=str(level))
        if campus_code:
            fields["campusCode"] = campus_code
        if building_code:
            fields["buildingCode"] = building_code
        data = self.client._post_form("/Home/GetRoomTree", fields)
        return data.get("dataModel") or []

    def _page(self, path: str) -> str:
        request = urllib.request.Request(
            ELEC_BASE + path,
            headers={"Accept": "text/html,application/xhtml+xml,*/*",
                     "User-Agent": USER_AGENT,
                     "Referer": ELEC_BASE + "/"},
        )
        return self.client._open(request)

    def _prepare_room_cookies(self, room: Room) -> None:
        self.client.set_cookie(ELEC_BASE, "100030",
                               f"{room.campus}/{room.building}/{room.room}")
        self.client.set_cookie(ELEC_BASE, "100030campusCode", room.campus_code)
        self.client.set_cookie(ELEC_BASE, "100030buildingCode", room.building_code)
        self.client.set_cookie(ELEC_BASE, "100030roomCode", room.room_code)

    def recharge(self, room: Room, amount: float, password: str = "") -> str:
        """向指定房间充值（金额从校园卡余额扣除）。返回学校提示语。"""
        if amount <= 0:
            raise PowerError("充值金额必须大于 0")
        if amount > 500:
            raise PowerError("单次充值金额过大，请先联系学校确认")
        page = self._page("/RechargeAndQuery/Index")
        antiforgery = parse_antiforgery(page) or self.antiforgery
        self._prepare_room_cookies(room)
        data = self.client._post_form("/RechargeAndQuery/Recharge", {
            "roomCode": room.room_code,
            "rechargeNumber": f"{amount:g}",
            "studentNumber": self.student_number,
            "__RequestVerificationToken": antiforgery,
            "password": password,
        })
        return str(data.get("message") or "充值请求已提交").strip()

    def recharge_history(self) -> list[RechargeRecord]:
        page = self._page("/RechargeAndQuery/HistoricalRechargeRecord")
        antiforgery = parse_antiforgery(page) or self.antiforgery
        data = self.client._post_form(
            "/RechargeAndQuery/GetHistoricalRechargeInfos",
            {"StudentNumber": self.student_number,
             "__RequestVerificationToken": antiforgery},
        )
        return parse_recharge_history(data.get("dataModel"))

    def balance(self, room: Room) -> QueryResult:
        fields = self._fields(roomCode=room.room_code)
        data = self.client._post_form("/Home/GetRoomBanlance", fields)
        model = data.get("dataModel") or {}
        text = str(model.get("balance") if model.get("balance") is not None else "").strip()
        try:
            balance = float(text)
        except ValueError:
            raise PowerError(f"学校返回的电量格式异常：{text or '空'}") from None
        unit = parse_unit(model.get("balanceType") or "")
        return QueryResult(balance=balance, unit=unit, balance_text=text,
                           room=room, raw=model)

# 校区显示名与顺序（学校返回的 7 个校区里，前两个「文昌/柳石」其实是商铺表计）
CAMPUS_DISPLAY = {
    "fA6ntT6CsXaJycVTXrE9Su/d53kRtXgQ0Y0aQXaK5cI=": "文昌校区",
    "xLXgxx2ykzWQ2Nzd0pgGMOHZE5EWQ4Rd9JnyXJ5ajEE=": "柳石校区",
    "RspIRIZKdcWsSX6/5Ddcsw==": "柳东校区",
    "wdD04b8MHpgYnkQFAiWy6A==": "箭盘校区",
    "p/UDwdzIoQ2z26CqcnbL0A==": "文昌校区商铺、学生服务中心、北区14栋",
    "RRkK+8mYIAFf3jaTJO94lw==": "柳石商铺",
    "i5MRRFHARQEkLHRyX20CWg==": "商业、教学、教职工用电",
}
CAMPUS_ORDER = [
    "文昌校区", "柳东校区", "柳石校区", "箭盘校区",
    "文昌校区商铺、学生服务中心、北区14栋", "柳石商铺", "商业、教学、教职工用电",
]


def natural_key(text: str):
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text or "")]


def sort_nodes(nodes: list[dict]) -> list[dict]:
    return sorted(nodes, key=lambda node: natural_key(node.get("name", "")))


def decorate_campuses(nodes: list[dict]) -> list[dict]:
    """给校区加显示名并按约定顺序排列；未知校区排在最后。"""
    used: dict[str, int] = {}
    items = []
    for index, node in enumerate(nodes):
        raw = node.get("name", "")
        name = CAMPUS_DISPLAY.get(node.get("code", ""), raw)
        used[name] = used.get(name, 0) + 1
        if used[name] > 1:
            name = f"{name} ({used[name]})"
        items.append({**node, "name": name, "rawName": raw, "index": index})
    order = {name: pos for pos, name in enumerate(CAMPUS_ORDER)}
    items.sort(key=lambda node: (order.get(node["name"], 99), node["index"]))
    return items
