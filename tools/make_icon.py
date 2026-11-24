"""生成 assets/app.ico（运行一次即可，改图标后重新执行）。"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "app.ico"


def main() -> None:
    size = 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # 深蓝底 + 圆角
    draw.rounded_rectangle([6, 6, size - 6, size - 6], radius=58,
                           fill=(22, 32, 58, 255))
    draw.rounded_rectangle([7, 7, size - 7, size - 7], radius=57,
                           outline=(77, 163, 255, 110), width=5)

    # 顶部高光
    draw.ellipse([-40, -120, size + 40, 90], fill=(31, 48, 86, 255))
    draw.rounded_rectangle([6, 6, size - 6, size - 6], radius=58,
                           outline=(77, 163, 255, 90), width=5)

    # 闪电
    bolt = [(152, 26), (82, 142), (126, 142), (100, 232), (186, 104), (140, 104)]
    draw.polygon(bolt, fill=(255, 198, 40, 255))
    draw.polygon([(146, 36), (96, 134), (128, 134), (110, 206), (172, 112),
                  (138, 112)], fill=(255, 224, 120, 255))

    # 底部小圆点（状态灯暗示）
    draw.ellipse([196, 196, 220, 220], fill=(61, 220, 151, 255))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                           (64, 64), (128, 128), (256, 256)])
    image.save(OUT.with_suffix(".png"))
    print("written", OUT)


if __name__ == "__main__":
    main()
