"""Draw the app icon (DataFrenzy mark: mint rounded square + dot on navy) and write
icon.png, icon.ico (Windows) and icon.icns (Mac, via iconutil when on a Mac)."""
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent
NAVY, MINT = (8, 15, 25, 255), (54, 227, 168, 255)


def draw(size: int) -> Image.Image:
    s = size / 1024
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([int(64 * s), int(64 * s), int(960 * s), int(960 * s)], radius=int(210 * s), fill=NAVY)
    d.rounded_rectangle([int(250 * s), int(250 * s), int(774 * s), int(774 * s)], radius=int(150 * s), outline=MINT,
                        width=max(2, int(54 * s)))
    r = 92 * s
    d.ellipse([512 * s - r, 512 * s - r, 512 * s + r, 512 * s + r], fill=MINT)
    return img


def main() -> None:
    big = draw(1024)
    big.save(HERE / "icon.png")
    big.save(HERE / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    if sys.platform == "darwin" and shutil.which("iconutil"):
        iconset = HERE / "icon.iconset"
        iconset.mkdir(exist_ok=True)
        for px in (16, 32, 128, 256, 512):
            draw(px).save(iconset / f"icon_{px}x{px}.png")
            draw(px * 2).save(iconset / f"icon_{px}x{px}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(HERE / "icon.icns")], check=True)
        shutil.rmtree(iconset)


if __name__ == "__main__":
    main()
