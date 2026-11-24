"""生成 MSI 免责声明（assets/license.rtf，中文，紧凑排版无空行）。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "license.rtf"

PARAGRAPHS = [
    ("GXUST电费助手 使用条款与免责声明", True),
    ("1. 本软件完全免费开源。如果你遇到需要付费才能使用的情况，说明软件遭到了修改，请立即删除！", False),
    ("2. 本软件与广西科技大学、贵阳瑞博科技有限公司无关，仅供学习使用。", False),
    ("3. 软件只读取本人账号有权查看的房间电量信息，不绕过学校权限，不代管任何资金。", False),
    ("4. 充值为实验功能，不一定成功；充值金额从校园卡余额扣除，请自行在校园卡中预留足够余额，结果以学校系统返回为准。", False),
    ("5. 电量、预计可用天数（按近 7 天估算，仅供参考）、充值记录等数据仅供参考；因学校接口调整、网络故障或数据延迟造成的任何影响，作者不承担责任。", False),
    ("6. 请遵守学校网络与信息系统使用规定，不要频繁刷新或恶意调用接口。", False),
    ("7. 作者 QQ：3104666011　GitHub：@smg123233", False),
    ("点击「我接受」表示你已阅读并同意以上条款。", False),
]


def escape(text: str) -> str:
    out = []
    for ch in text:
        if ch in "\\{}":
            out.append("\\" + ch)
        elif ord(ch) < 128:
            out.append(ch)
        else:
            out.append(f"\\u{ord(ch)}?")
    return "".join(out)


def main() -> None:
    header = (r"{\rtf1\ansi\ansicpg936\deff0\uc1"
              r"{\fonttbl{\f0\fnil\fcharset134 Microsoft YaHei;}}"
              r"\viewkind4\pard\lang2052\f0\fs18 ")
    body = []
    for paragraph, bold in PARAGRAPHS:
        if bold:
            body.append(r"\b " + escape(paragraph) + r"\b0\par")
        else:
            body.append(r"\pard\plain\f0\fs18\li0\ri0\fi0 " + escape("  " + paragraph) + r"\par")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(header + "".join(body) + "}", encoding="ascii")
    print("written", OUT)


if __name__ == "__main__":
    main()
