from pathlib import Path
from urllib.request import Request, urlopen
from PIL import Image, ImageDraw, ImageFont
import io

root = Path(__file__).resolve().parent
refs = root / "refs"
refs.mkdir(exist_ok=True)
items = {
    "discord": "https://is1-ssl.mzstatic.com/image/thumb/PurpleSource221/v4/31/a1/f8/31a1f8cb-d54c-7bd1-4a5f-bf9efcfd7b0a/1_GroupChat_Tile.png/600x1300bb.webp",
    "playstation": "https://is1-ssl.mzstatic.com/image/thumb/PurpleSource211/v4/2a/26/80/2a2680bc-dea8-9c2c-e1a5-f338dcf34cde/iphone_01.png/600x1300bb.webp",
}
images = []
for name, url in items.items():
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=45) as response:
        payload = response.read()
    image = Image.open(io.BytesIO(payload)).convert("RGB")
    image.save(refs / f"{name}-official.webp")
    images.append((name, image))
    print(name, image.size)

board = Image.new("RGB", (1440, 900), "#eef2f7")
draw = ImageDraw.Draw(board)
font = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 26)
small = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 18)
for index, (name, image) in enumerate(images):
    image.thumbnail((560, 740), Image.Resampling.LANCZOS)
    x = 100 + index * 690
    y = 88
    board.paste(image, (x, y))
    draw.text((x, 28), "Discord · Group Chat" if name == "discord" else "PlayStation App · Play", fill="#0f172a", font=font)
    draw.text((x, 838), "Official App Store screenshot", fill="#475569", font=small)
board.save(refs / "board.jpg", quality=92)
