#!/usr/bin/env python3
"""Render nearby ADSB.lol traffic to a 128x64 RGB888 framebuffer."""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


WIDTH = 128
HEIGHT = 64
BG = (2, 4, 9)
CYAN = (20, 236, 255)
WHITE = (232, 246, 255)
MUTED = (88, 132, 150)
AMBER = (255, 176, 32)
RED = (255, 62, 82)


@dataclass
class Settings:
    latitude: float = 51.5074
    longitude: float = -0.1278
    radius_nm: float = 25.0
    refresh_seconds: float = 10.0
    output_rgb: str = "output/frame.rgb"
    output_png: str = "output/frame.png"
    demo_on_failure: bool = True


DEMO = {
    "ac": [
        {
            "hex": "406b82",
            "flight": "BAW283 ",
            "r": "G-STBH",
            "t": "B77W",
            "lat": 51.533,
            "lon": -0.094,
            "alt_baro": 11875,
            "gs": 287.4,
            "track": 276.0,
            "squawk": "6352",
            "seen": 0.2,
            "seen_pos": 0.4,
            "category": "A5",
        }
    ]
}


def load_settings(path: Path) -> Settings:
    if not path.exists():
        return Settings()
    raw = json.loads(path.read_text(encoding="utf-8"))
    allowed = Settings.__dataclass_fields__.keys()
    return Settings(**{k: raw[k] for k in allowed if k in raw})


def fetch_aircraft(settings: Settings) -> list[dict[str, Any]]:
    url = (
        "https://api.adsb.lol/v2/point/"
        f"{settings.latitude}/{settings.longitude}/{settings.radius_nm}"
    )
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Over-Head/0.1 (+personal wall display)"},
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        payload = json.load(response)
    return payload.get("ac", [])


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_nm = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius_nm * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def select_nearest(
    aircraft: list[dict[str, Any]], settings: Settings
) -> tuple[dict[str, Any] | None, float | None]:
    candidates: list[tuple[float, dict[str, Any]]] = []
    for plane in aircraft:
        lat, lon = plane.get("lat"), plane.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        if float(plane.get("seen_pos", 999)) > 20:
            continue
        distance = haversine_nm(settings.latitude, settings.longitude, float(lat), float(lon))
        candidates.append((distance, plane))
    if not candidates:
        return None, None
    distance, plane = min(candidates, key=lambda item: item[0])
    return plane, distance


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    paths = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    )
    for path in paths:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def text_fit(draw: ImageDraw.ImageDraw, value: str, max_width: int, size: int) -> ImageFont.ImageFont:
    while size > 5:
        candidate = font(size)
        if draw.textbbox((0, 0), value, font=candidate)[2] <= max_width:
            return candidate
        size -= 1
    return font(5)


def format_altitude(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{round(float(value) / 1000, 1):g}K FT"
    return "ALT N/A"


def draw_plane(draw: ImageDraw.ImageDraw, cx: int, cy: int, heading: float, color: tuple[int, int, int]) -> None:
    shape = [(0, -9), (2, -2), (9, 3), (9, 5), (2, 3), (2, 8), (5, 10), (5, 11), (0, 9), (-5, 11), (-5, 10), (-2, 8), (-2, 3), (-9, 5), (-9, 3), (-2, -2)]
    angle = math.radians(heading)
    points = []
    for x, y in shape:
        rx = x * math.cos(angle) - y * math.sin(angle)
        ry = x * math.sin(angle) + y * math.cos(angle)
        points.append((round(cx + rx), round(cy + ry)))
    draw.polygon(points, fill=color)


def render(plane: dict[str, Any] | None, distance_nm: float | None, status: str) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), outline=(18, 53, 67))
    draw.line((0, 14, WIDTH, 14), fill=(18, 53, 67))
    draw.text((4, 3), "OVERHEAD", font=font(8), fill=CYAN)
    draw.text((89, 3), status.upper()[:8], font=font(7), fill=MUTED)

    if plane is None:
        draw.text((14, 25), "NO AIRCRAFT", font=font(12), fill=WHITE)
        draw.text((28, 44), "IN RANGE", font=font(8), fill=MUTED)
        return image

    callsign = str(plane.get("flight") or plane.get("r") or plane.get("hex") or "UNKNOWN").strip()
    registration = str(plane.get("r") or "").strip()
    aircraft_type = str(plane.get("t") or "TYPE N/A").strip()
    squawk = str(plane.get("squawk") or "")
    alert = squawk in {"7500", "7600", "7700"}
    accent = RED if alert else CYAN

    draw.text((4, 17), callsign, font=text_fit(draw, callsign, 83, 15), fill=WHITE)
    detail = "  ".join(part for part in (registration, aircraft_type) if part)
    draw.text((4, 34), detail[:22], font=text_fit(draw, detail[:22], 82, 8), fill=MUTED)
    draw.text((4, 47), format_altitude(plane.get("alt_baro")), font=font(8), fill=AMBER)
    speed = plane.get("gs")
    speed_text = f"{round(float(speed))} KT" if isinstance(speed, (int, float)) else "SPD N/A"
    distance_text = f"{distance_nm:.1f} NM" if distance_nm is not None else "DIST N/A"
    draw.text((48, 47), speed_text, font=font(8), fill=WHITE)
    draw.text((91, 54), distance_text, font=text_fit(draw, distance_text, 34, 7), fill=MUTED)
    draw_plane(draw, 105, 32, float(plane.get("track") or 0), accent)
    return image


def save_frame(image: Image.Image, settings: Settings) -> None:
    rgb_path = Path(settings.output_rgb)
    png_path = Path(settings.output_png)
    rgb_path.parent.mkdir(parents=True, exist_ok=True)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(png_path)
    rgb_path.write_bytes(image.tobytes("raw", "RGB"))


def fetch_snapshot(
    settings: Settings, demo: bool = False
) -> tuple[dict[str, Any] | None, float | None, str, int]:
    status = "demo" if demo else "live"
    try:
        aircraft = DEMO["ac"] if demo else fetch_aircraft(settings)
    except (OSError, ValueError, urllib.error.URLError, TimeoutError):
        if not settings.demo_on_failure:
            raise
        aircraft = DEMO["ac"]
        status = "demo"
    plane, distance = select_nearest(aircraft, settings)
    return plane, distance, status, len(aircraft)


def produce_frame(settings: Settings, demo: bool = False) -> tuple[Image.Image, str]:
    plane, distance, status, _ = fetch_snapshot(settings, demo=demo)
    image = render(plane, distance, status)
    save_frame(image, settings)
    return image, status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--demo", action="store_true", help="render bundled sample aircraft")
    parser.add_argument("--watch", action="store_true", help="refresh continuously")
    args = parser.parse_args()
    settings = load_settings(Path(args.config))
    while True:
        _, status = produce_frame(settings, demo=args.demo)
        print(f"Rendered {status} frame to {settings.output_rgb} and {settings.output_png}")
        if not args.watch:
            return 0
        time.sleep(max(1.0, settings.refresh_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
