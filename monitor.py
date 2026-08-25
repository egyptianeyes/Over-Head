#!/usr/bin/env python3
"""Serve Over-Head as a sharp, full-screen monitor display."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree

from overhead import fetch_traffic_snapshot, load_settings, render, save_frame, select_nearest


SOARING_INDEX_URL = (
    "https://raw.githubusercontent.com/soaring-symbols/soaring-symbols/"
    "main/airlines.json"
)
SOARING_ASSET_URL = (
    "https://raw.githubusercontent.com/soaring-symbols/soaring-symbols/"
    "main/assets/{slug}/{filename}"
)
JXCK_LOGO_URLS = (
    "https://raw.githubusercontent.com/Jxck-S/airline-logos/main/fr24_banners/{code}.png",
    "https://raw.githubusercontent.com/Jxck-S/airline-logos/main/radarbox_banners/{code}.png",
    "https://raw.githubusercontent.com/Jxck-S/airline-logos/main/flightaware_logos/{code}.png",
)
USER_AGENT = "Over-Head/0.2 (+personal wall display)"
MAX_LOGO_BYTES = 1_000_000
RADAR_RANGES = (5, 10, 15, 25, 40, 60, 100)

AIRCRAFT_NAMES = {
    "A306": "AIRBUS A300-600", "A319": "AIRBUS A319", "A320": "AIRBUS A320",
    "A321": "AIRBUS A321", "A20N": "AIRBUS A320NEO", "A21N": "AIRBUS A321NEO",
    "A332": "AIRBUS A330-200", "A333": "AIRBUS A330-300", "A339": "AIRBUS A330-900NEO",
    "A343": "AIRBUS A340-300", "A359": "AIRBUS A350-900", "A35K": "AIRBUS A350-1000",
    "A388": "AIRBUS A380-800", "AT43": "ATR 42-300", "AT45": "ATR 42-500",
    "AT72": "ATR 72-200", "AT75": "ATR 72-500", "AT76": "ATR 72-600",
    "B712": "BOEING 717-200", "B733": "BOEING 737-300", "B734": "BOEING 737-400",
    "B735": "BOEING 737-500", "B736": "BOEING 737-600", "B737": "BOEING 737-700",
    "B738": "BOEING 737-800", "B739": "BOEING 737-900", "B38M": "BOEING 737 MAX 8",
    "B39M": "BOEING 737 MAX 9", "B744": "BOEING 747-400", "B748": "BOEING 747-8",
    "B752": "BOEING 757-200", "B753": "BOEING 757-300",
    "B762": "BOEING 767-200", "B763": "BOEING 767-300",
    "B764": "BOEING 767-400ER", "B76F": "BOEING 767 FREIGHTER",
    "B772": "BOEING 777-200", "B77L": "BOEING 777-200LR", "B77W": "BOEING 777-300ER",
    "B788": "BOEING 787-8", "B789": "BOEING 787-9", "B78X": "BOEING 787-10",
    "C152": "CESSNA 152", "C172": "CESSNA 172", "C182": "CESSNA 182",
    "CL60": "BOMBARDIER CHALLENGER 600 SERIES",
    "CRJ7": "BOMBARDIER CRJ700", "CRJ9": "BOMBARDIER CRJ900", "DH8D": "DE HAVILLAND DASH 8-400",
    "E170": "EMBRAER E170", "E175": "EMBRAER E175", "E190": "EMBRAER E190",
    "E195": "EMBRAER E195", "E290": "EMBRAER E190-E2", "E295": "EMBRAER E195-E2",
    "P28A": "PIPER PA-28 CHEROKEE", "PC12": "PILATUS PC-12",
}


def aircraft_name(type_code: Any, description: Any = None) -> str:
    """Expand a common ICAO aircraft designator into a readable model name."""
    if isinstance(description, str) and description.strip():
        return description.strip().upper()
    code = str(type_code or "").strip().upper()
    return AIRCRAFT_NAMES.get(code, "MODEL NOT IDENTIFIED" if code else "AIRCRAFT NOT IDENTIFIED")


def operator_code(plane: dict[str, Any] | None) -> str:
    """Return a three-letter ICAO operator code when the callsign has one."""
    if not plane:
        return ""
    callsign = re.sub(r"[^A-Z0-9]", "", str(plane.get("flight") or "").upper())
    registration = re.sub(r"[^A-Z0-9]", "", str(plane.get("r") or "").upper())
    if len(callsign) < 4 or not callsign[:3].isalpha() or callsign == registration:
        return ""
    return callsign[:3]


def aircraft_identity(plane: dict[str, Any] | None) -> str:
    """Return a stable identity used to detect a change of tracked aircraft."""
    if not plane:
        return ""
    for field in ("hex", "r", "flight"):
        value = re.sub(r"[^A-Z0-9]", "", str(plane.get(field) or "").upper())
        if value:
            return value
    return ""


def safe_svg(data: bytes) -> bytes | None:
    """Reject active or externally-referencing SVG content before serving it."""
    if len(data) > MAX_LOGO_BYTES:
        return None
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return None
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag in {"script", "foreignobject", "iframe", "object", "embed"}:
            return None
        for name, value in node.attrib.items():
            attr = name.rsplit("}", 1)[-1].lower()
            if attr.startswith("on"):
                return None
            if attr == "href" and value and not value.startswith("#"):
                return None
    return data


def radar_contacts(aircraft: list[dict[str, Any]], settings, selected: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Project current aircraft positions onto a north-up circular radar."""
    selected_hex = str((selected or {}).get("hex") or "")
    latitude_scale = 60.0405
    longitude_scale = latitude_scale * math.cos(math.radians(settings.latitude))
    contacts: list[dict[str, Any]] = []
    for plane in aircraft:
        lat, lon = plane.get("lat"), plane.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        try:
            if float(plane.get("seen_pos", 999)) > 20:
                continue
        except (TypeError, ValueError):
            continue
        east = (float(lon) - settings.longitude) * longitude_scale
        north = (float(lat) - settings.latitude) * latitude_scale
        distance = math.hypot(east, north)
        if distance > settings.radius_nm:
            continue
        callsign = str(plane.get("flight") or plane.get("r") or plane.get("hex") or "").strip()
        contacts.append({
            "x": round(50 + east / settings.radius_nm * 46, 2),
            "y": round(50 - north / settings.radius_nm * 46, 2),
            "callsign": callsign,
            "track": plane.get("track") or 0,
            "altitude": plane.get("alt_baro"),
            "selected": bool(selected_hex and str(plane.get("hex") or "") == selected_hex),
            "emergency": str(plane.get("squawk") or "") in {"7500", "7600", "7700"},
        })
    return contacts


class LogoStore:
    """Resolve logos once, cache them locally, and remember missing operators."""

    def __init__(self, cache_dir: Path = Path("cache/logos")) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = cache_dir / "soaring-airlines.json"
        self._index: dict[str, dict[str, Any]] | None = None
        self._missing: set[str] = set()
        self._lock = threading.Lock()

    def _download(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=6) as response:
            if response.status != 200:
                raise OSError(f"logo response {response.status}")
            data = response.read(MAX_LOGO_BYTES + 1)
        if len(data) > MAX_LOGO_BYTES:
            raise ValueError("logo response too large")
        return data

    def _load_index(self) -> dict[str, dict[str, Any]]:
        if self._index is not None:
            return self._index
        raw: bytes
        try:
            raw = self.index_path.read_bytes()
            if time.time() - self.index_path.stat().st_mtime > 7 * 86400:
                raise OSError("stale index")
        except OSError:
            raw = self._download(SOARING_INDEX_URL)
            self.index_path.write_bytes(raw)
        records = json.loads(raw)
        self._index = {
            str(record.get("icao") or "").upper(): record
            for record in records
            if isinstance(record, dict) and record.get("icao")
        }
        return self._index

    def _soaring(self, code: str) -> tuple[bytes, str, str] | None:
        record = self._load_index().get(code)
        if not record or not record.get("slug"):
            return None
        slug = str(record["slug"])
        for filename in ("logo.svg", "icon.svg"):
            try:
                data = safe_svg(self._download(SOARING_ASSET_URL.format(slug=slug, filename=filename)))
            except (OSError, ValueError, urllib.error.URLError, TimeoutError):
                continue
            if data:
                return data, "image/svg+xml", "Soaring Symbols"
        return None

    def _jxck(self, code: str) -> tuple[bytes, str, str] | None:
        for url in JXCK_LOGO_URLS:
            try:
                data = self._download(url.format(code=code))
            except (OSError, ValueError, urllib.error.URLError, TimeoutError):
                continue
            if data.startswith(b"\x89PNG\r\n\x1a\n"):
                return data, "image/png", "Jxck-S airline-logos"
        return None

    def get(self, code: str) -> tuple[bytes, str, str] | None:
        if not re.fullmatch(r"[A-Z]{3}", code) or code in self._missing:
            return None
        with self._lock:
            for suffix, content_type, source in (
                ("svg", "image/svg+xml", "Soaring Symbols"),
                ("png", "image/png", "Jxck-S airline-logos"),
            ):
                path = self.cache_dir / f"{code}-wordmark.{suffix}"
                if path.is_file():
                    return path.read_bytes(), content_type, source
            try:
                result = self._soaring(code)
            except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError, TimeoutError):
                result = None
            result = result or self._jxck(code)
            if result:
                data, content_type, source = result
                suffix = "svg" if content_type == "image/svg+xml" else "png"
                (self.cache_dir / f"{code}-wordmark.{suffix}").write_bytes(data)
                return result
            self._missing.add(code)
            return None


PAGE = b"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Over-Head</title>
  <style>
    :root { color-scheme: dark; --bg:#02050a; --line:#123846; --cyan:#14ecff; --white:#e8f6ff; --muted:#6c9aac; --amber:#ffb020; --red:#ff3e52; }
    * { box-sizing: border-box; }
    html,body { width:100%; height:100%; margin:0; overflow:hidden; background:var(--bg); }
    body { color:var(--white); font-family:"Segoe UI",Arial,sans-serif; cursor:none; -webkit-font-smoothing:antialiased; text-rendering:geometricPrecision; }
    .wall { width:100vw; height:100vh; padding:clamp(18px,2.4vw,52px); display:grid; grid-template-rows:auto 1fr auto; gap:clamp(14px,2vh,28px); background:radial-gradient(circle at 76% 48%,rgba(20,236,255,.075),transparent 28%),linear-gradient(145deg,#06121a 0%,var(--bg) 52%); }
    header,footer { display:flex; align-items:center; justify-content:space-between; }
    .header-tools { display:flex; align-items:center; gap:clamp(14px,1.5vw,28px); }
    .radar-toggle { cursor:pointer; border:1px solid var(--line); border-radius:999px; padding:.62em 1.05em; background:rgba(20,236,255,.055); color:var(--muted); font:750 clamp(11px,1vw,18px)/1 "Segoe UI",sans-serif; letter-spacing:.14em; }
    .radar-toggle:hover,.radar-toggle:focus-visible,.wall.radar-open .radar-toggle { color:var(--cyan); border-color:rgba(20,236,255,.48); outline:none; }
    .sound-toggle { width:clamp(38px,2.7vw,50px); height:clamp(38px,2.7vw,50px); padding:9px; display:grid; place-items:center; cursor:pointer; border:1px solid var(--line); border-radius:50%; background:rgba(20,236,255,.055); color:var(--cyan); }
    .sound-toggle:hover,.sound-toggle:focus-visible { border-color:var(--cyan); background:rgba(20,236,255,.12); outline:none; }
    .sound-toggle svg { width:100%; height:100%; }
    .sound-toggle .mute-slash { display:none; }
    .sound-toggle.muted { color:var(--muted); }
    .sound-toggle.muted .mute-slash { display:block; }
    .sound-toggle.blocked { color:var(--amber); border-color:rgba(255,176,32,.65); }
    .brand { color:var(--cyan); font-size:clamp(30px,4.2vw,78px); font-weight:800; letter-spacing:-.055em; line-height:1; }
    .live { display:flex; align-items:center; gap:.7em; color:var(--muted); font-size:clamp(14px,1.45vw,26px); font-weight:700; letter-spacing:.16em; }
    .live-dot { width:.62em; height:.62em; border-radius:50%; background:var(--cyan); box-shadow:0 0 1em var(--cyan); }
    .live.demo .live-dot { background:var(--amber); box-shadow:0 0 1em var(--amber); }
    .live.error .live-dot { background:var(--red); box-shadow:0 0 1em var(--red); }
    .content { min-height:0; display:grid; grid-template-columns:minmax(0,1fr); gap:clamp(14px,1.3vw,24px); }
    .wall.radar-open .content { grid-template-columns:minmax(0,1fr) clamp(320px,29vw,560px); }
    main { min-height:0; border:1px solid var(--line); border-radius:clamp(16px,2vw,32px); background:rgba(0,2,7,.72); display:grid; grid-template-columns:1fr; overflow:hidden; box-shadow:inset 0 0 60px rgba(20,236,255,.025),0 28px 80px rgba(0,0,0,.34); }
    .information { min-width:0; padding:clamp(26px,4vw,76px); display:flex; flex-direction:column; justify-content:space-between; }
    .flight-heading { min-width:0; display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center; gap:clamp(24px,3vw,56px); }
    .flight-copy { min-width:0; }
    .callsign { overflow:hidden; color:var(--white); font-size:clamp(64px,10.5vw,198px); font-weight:800; letter-spacing:-.065em; line-height:.88; white-space:nowrap; }
    .identity { margin-top:clamp(18px,2.2vh,34px); min-width:0; display:flex; flex-direction:column; gap:.24em; }
    .registration { color:var(--muted); font-size:clamp(22px,2.5vw,44px); font-weight:700; letter-spacing:.1em; }
    .aircraft-name { color:var(--white); font-size:clamp(17px,1.55vw,28px); font-weight:650; letter-spacing:.065em; }
    .logo-box { width:clamp(190px,17vw,330px); height:clamp(100px,14vh,180px); display:grid; place-items:center; overflow:visible; transition:width .2s ease,height .2s ease; }
    .logo-box.wide { width:clamp(240px,19vw,370px); height:clamp(80px,10vh,125px); }
    .logo-box.square { width:clamp(110px,10vw,180px); height:clamp(110px,12vh,180px); }
    .logo-box.tall { width:clamp(100px,9vw,160px); height:clamp(125px,16vh,205px); }
    .logo-box img { display:none; max-width:100%; max-height:100%; width:auto; height:auto; object-fit:contain; filter:drop-shadow(0 0 12px rgba(108,154,172,.15)); }
    .operator-badge { color:var(--cyan); font-size:clamp(24px,2.5vw,44px); font-weight:850; letter-spacing:.08em; }
    .metrics { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:clamp(12px,2vw,30px); margin-top:clamp(24px,5vh,70px); }
    .metric { min-width:0; padding-top:clamp(12px,2vh,24px); border-top:1px solid var(--line); }
    .metric-label { color:var(--muted); font-size:clamp(11px,1vw,18px); font-weight:700; letter-spacing:.18em; }
    .metric-value { margin-top:.18em; color:var(--white); font-size:clamp(27px,3.5vw,64px); font-weight:750; letter-spacing:-.035em; white-space:nowrap; }
    .metric:first-child .metric-value { color:var(--amber); }
    .radar-panel { display:none; min-width:0; min-height:0; padding:clamp(18px,1.8vw,30px); border:1px solid var(--line); border-radius:clamp(16px,2vw,32px); background:rgba(0,2,7,.78); grid-template-rows:auto minmax(0,1fr) auto; overflow:hidden; box-shadow:inset 0 0 60px rgba(20,236,255,.025),0 28px 80px rgba(0,0,0,.28); }
    .wall.radar-open .radar-panel { display:grid; }
    .radar-heading { display:flex; align-items:end; justify-content:space-between; }
    .radar-heading strong { color:var(--white); font-size:clamp(18px,1.5vw,28px); letter-spacing:.08em; }
    .radar-heading span,.radar-foot { color:var(--muted); font-size:clamp(10px,.82vw,14px); font-weight:700; letter-spacing:.14em; }
    .radar-stage { min-height:0; display:grid; place-items:center; }
    #radar { width:min(100%,52vh); aspect-ratio:1; overflow:visible; }
    .radar-grid { fill:rgba(20,236,255,.018); stroke:rgba(20,236,255,.18); stroke-width:.35; vector-effect:non-scaling-stroke; }
    .radar-axis { stroke:rgba(20,236,255,.09); stroke-width:.3; vector-effect:non-scaling-stroke; }
    .radar-home { fill:var(--white); filter:drop-shadow(0 0 2px var(--cyan)); }
    .contact { color:var(--muted); transition:transform 800ms ease; }
    .contact.selected { color:var(--cyan); filter:drop-shadow(0 0 1.6px var(--cyan)); }
    .contact.emergency { color:var(--red); }
    .contact path { fill:currentColor; }
    .contact text { fill:currentColor; font:700 3.2px "Segoe UI",sans-serif; letter-spacing:.02em; }
    .radar-foot { display:flex; justify-content:space-between; }
    .wall.radar-open .metric-value { font-size:clamp(26px,2.75vw,52px); }
    .wall.radar-open .callsign { font-size:clamp(64px,7.2vw,138px); }
    .radar-zoom { display:flex; align-items:center; gap:clamp(8px,.7vw,12px); }
    .zoom-button { width:2.15em; height:2.15em; padding:0; display:grid; place-items:center; cursor:pointer; border:1px solid var(--line); border-radius:50%; background:rgba(20,236,255,.055); color:var(--cyan); font:700 clamp(15px,1.2vw,21px)/1 "Segoe UI",sans-serif; }
    .zoom-button:hover,.zoom-button:focus-visible { border-color:var(--cyan); background:rgba(20,236,255,.12); outline:none; }
    .zoom-button:disabled { color:#34525e; border-color:#18303a; cursor:default; }
    .wall.no-aircraft .information { justify-content:center; }
    .wall.no-aircraft .flight-heading { display:block; }
    .wall.no-aircraft .callsign { text-align:center; font-size:clamp(100px,15vw,260px); line-height:1; letter-spacing:0; }
    .wall.no-aircraft .identity,.wall.no-aircraft .logo-box,.wall.no-aircraft .metrics { display:none; }
    footer { color:#52717e; font:600 clamp(11px,.9vw,16px)/1.2 "Segoe UI",sans-serif; letter-spacing:.1em; }
    @media (max-width:1150px) { .wall.radar-open .content { grid-template-columns:minmax(0,1fr) minmax(280px,36vw); } .wall.radar-open .metrics { grid-template-columns:repeat(2,minmax(0,1fr)); } }
    @media (max-aspect-ratio:4/3) { .callsign { font-size:clamp(56px,15vw,130px); } }
  </style>
</head>
<body>
  <section class="wall no-aircraft" id="wall">
    <header><div class="brand">OVER-HEAD</div><div class="header-tools"><button class="sound-toggle" id="sound-toggle" type="button" aria-label="Mute aircraft change sound" aria-pressed="true"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 18h6m-5 2h4M6.5 16.5h11c-1.6-1.7-2.2-3.5-2.2-6.2A3.3 3.3 0 0 0 12 7a3.3 3.3 0 0 0-3.3 3.3c0 2.7-.6 4.5-2.2 6.2Z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><path class="mute-slash" d="M4 4l16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg></button><button class="radar-toggle" id="radar-toggle" type="button" aria-pressed="false">RADAR</button><div class="live" id="live"><span class="live-dot"></span><span id="mode">STARTING</span></div></div></header>
    <div class="content">
    <main>
      <section class="information">
        <div class="flight-heading"><div class="flight-copy"><div class="callsign" id="callsign">-</div><div class="identity"><span class="registration" id="registration"></span><span class="aircraft-name" id="aircraft-name"></span></div></div><div class="logo-box" id="logo-box"><img id="operator-logo" alt=""><span class="operator-badge" id="operator-badge">-</span></div></div>
        <div class="metrics">
          <div class="metric"><div class="metric-label">ALTITUDE</div><div class="metric-value" id="altitude">--</div></div>
          <div class="metric"><div class="metric-label">GROUND SPEED</div><div class="metric-value" id="speed">--</div></div>
          <div class="metric"><div class="metric-label">VERTICAL</div><div class="metric-value" id="vertical">--</div></div>
          <div class="metric metric-distance"><div class="metric-label">DISTANCE</div><div class="metric-value"><span id="distance-main">--</span> NM</div></div>
        </div>
      </section>
    </main>
    <aside class="radar-panel" aria-label="Nearby aircraft radar">
      <div class="radar-heading"><strong>WIDER AREA</strong><span id="radar-count">0 CONTACTS</span></div>
      <div class="radar-stage"><svg id="radar" viewBox="0 0 100 100" role="img" aria-label="North-up radar of nearby aircraft">
        <circle class="radar-grid" cx="50" cy="50" r="46"/><circle class="radar-grid" cx="50" cy="50" r="30.7"/><circle class="radar-grid" cx="50" cy="50" r="15.3"/>
        <path class="radar-axis" d="M50 4V96M4 50H96"/><text x="50" y="2.8" text-anchor="middle" fill="#6c9aac" font-size="3">N</text>
        <g id="radar-contacts"></g><circle class="radar-home" cx="50" cy="50" r="1.25"/>
      </svg></div>
      <div class="radar-foot"><span>HOME CENTRE</span><div class="radar-zoom"><button class="zoom-button" id="zoom-in" type="button" aria-label="Zoom in and reduce tracking range">&minus;</button><span id="radar-range">25 NM</span><button class="zoom-button" id="zoom-out" type="button" aria-label="Zoom out and increase tracking range">+</button></div></div>
    </aside>
    </div>
    <footer><span>SOURCE: ADSB.LOL</span><span id="footer-status">CONNECTING</span></footer>
  </section>
  <audio id="alert-tone" preload="auto" src="/beep-tone.mp3"></audio>
  <script>
    const byId=id=>document.getElementById(id);
    const svgNS='http://www.w3.org/2000/svg';
    const radarRanges=[5,10,15,25,40,60,100]; let currentRadarRange=25;
    let soundEnabled=localStorage.getItem('overhead-sound')!=='muted'; let audioBlocked=false; let trackedAircraftId='';
    const number=(value,digits=0)=>value==null?'--':Number(value).toFixed(digits);
    const altitude=value=>typeof value==='number'?Math.round(value).toLocaleString()+' FT':String(value||'--').toUpperCase();
    function vertical(value){ if(typeof value!=='number')return 'LEVEL'; if(value>150)return '\\u2191 '+Math.abs(Math.round(value)).toLocaleString(); if(value<-150)return '\\u2193 '+Math.abs(Math.round(value)).toLocaleString(); return 'LEVEL'; }
    function updateSoundButton(){ const button=byId('sound-toggle'); button.classList.toggle('muted',!soundEnabled); button.classList.toggle('blocked',audioBlocked&&soundEnabled); button.setAttribute('aria-pressed',String(soundEnabled)); button.setAttribute('aria-label',!soundEnabled?'Enable aircraft change sound':audioBlocked?'Enable aircraft sound':'Mute aircraft change sound'); }
    async function playTone(){ if(!soundEnabled)return; const tone=byId('alert-tone'); try{ tone.currentTime=0; await tone.play(); audioBlocked=false; }catch(_){ audioBlocked=true; } updateSoundButton(); }
    function drawRadar(contacts,radius){
      const layer=byId('radar-contacts'); layer.replaceChildren();
      for(const c of contacts||[]){
        const group=document.createElementNS(svgNS,'g'); group.setAttribute('class','contact'+(c.selected?' selected':'')+(c.emergency?' emergency':'')); group.setAttribute('transform','translate('+c.x+' '+c.y+') rotate('+(Number(c.track)||0)+')');
        const plane=document.createElementNS(svgNS,'path'); plane.setAttribute('d','M0-2.3L.65-.25 2.3.7 2.3 1.2.6.7.45 2 1.15 2.5 1.15 2.8 0 2.5-1.15 2.8-1.15 2.5-.45 2-.6.7-2.3 1.2-2.3.7-.65-.25Z'); group.appendChild(plane);
        if(!c.selected&&(contacts||[]).length<=10){ const label=document.createElementNS(svgNS,'text'); label.textContent=c.callsign; label.setAttribute('x','2.8'); label.setAttribute('y','-1.5'); label.setAttribute('transform','rotate('+(0-(Number(c.track)||0))+' 2.8 -1.5)'); group.appendChild(label); }
        layer.appendChild(group);
      }
      currentRadarRange=Number(radius)||25; byId('radar-count').textContent=(contacts||[]).length+' CONTACTS'; byId('radar-range').textContent=number(radius,0)+' NM';
      const rangeIndex=radarRanges.indexOf(currentRadarRange); byId('zoom-in').disabled=rangeIndex===0; byId('zoom-out').disabled=rangeIndex===radarRanges.length-1;
    }
    function setRadar(open){ byId('wall').classList.toggle('radar-open',open); byId('radar-toggle').setAttribute('aria-pressed',String(open)); byId('radar-toggle').textContent=open?'HIDE RADAR':'RADAR'; localStorage.setItem('overhead-radar',open?'open':'closed'); }
    async function changeRadarRange(direction){
      let index=radarRanges.indexOf(currentRadarRange); if(index<0)index=radarRanges.reduce((best,value,i)=>Math.abs(value-currentRadarRange)<Math.abs(radarRanges[best]-currentRadarRange)?i:best,0);
      const next=radarRanges[Math.max(0,Math.min(radarRanges.length-1,index+direction))]; if(next===currentRadarRange)return;
      currentRadarRange=next; localStorage.setItem('overhead-radar-range',String(next)); byId('radar-range').textContent=next+' NM';
      await fetch('/radar-range?radius='+next,{cache:'no-store'});
    }
    async function update(){
      try{
        const response=await fetch('/status?t='+Date.now(),{cache:'no-store'}); if(!response.ok)throw new Error('status'); const d=await response.json();
        const mode=String(d.mode||'live').toLowerCase(); byId('live').className='live '+mode; byId('mode').textContent=mode.toUpperCase();
        const hasAircraft=Boolean(d.aircraft_id); byId('wall').classList.toggle('no-aircraft',!hasAircraft); byId('callsign').textContent=hasAircraft?(d.callsign||d.registration||d.aircraft_id):'-';
        if(d.aircraft_id&&d.aircraft_id!==trackedAircraftId){ trackedAircraftId=d.aircraft_id; playTone(); } else if(!d.aircraft_id){ trackedAircraftId=''; }
        byId('registration').textContent=d.registration||'REGISTRATION UNKNOWN'; byId('aircraft-name').textContent=d.aircraft_name+(d.aircraft_type?'  \\u00b7  '+d.aircraft_type:'');
        const logo=byId('operator-logo'),badge=byId('operator-badge'),logoBox=byId('logo-box'); badge.textContent=d.operator_code||'-';
        if(d.logo_available){ logo.onload=()=>{ const ratio=logo.naturalWidth&&logo.naturalHeight?logo.naturalWidth/logo.naturalHeight:2; const shape=ratio>2.2?'wide':ratio<.8?'tall':ratio<1.25?'square':'standard'; logoBox.className='logo-box '+shape; logo.style.display='block'; badge.style.display='none'; }; logo.onerror=()=>{logoBox.className='logo-box square';logo.style.display='none';badge.style.display='block'}; logo.alt=(d.airline_name||d.operator_code||'Airline')+' logo'; logo.src='/operator-logo?v='+encodeURIComponent(d.logo_revision); }
        else { logo.removeAttribute('src'); logoBox.className='logo-box square'; logo.style.display='none'; badge.style.display='block'; }
        byId('altitude').textContent=altitude(d.altitude); byId('speed').textContent=d.speed==null?'--':Math.round(d.speed)+' KT'; byId('vertical').textContent=vertical(d.vertical_rate);
        byId('distance-main').textContent=number(d.distance_nm,1);
        drawRadar(d.radar_contacts,d.radar_radius_nm);
        byId('footer-status').textContent=d.total+' CONTACTS  /  UPDATED '+d.updated;
      }catch(_){ byId('live').className='live error'; byId('mode').textContent='RECONNECTING'; }
    }
    setRadar(localStorage.getItem('overhead-radar')==='open'); byId('radar-toggle').addEventListener('click',()=>setRadar(!byId('wall').classList.contains('radar-open'))); document.addEventListener('keydown',event=>{if(event.key.toLowerCase()==='r')setRadar(!byId('wall').classList.contains('radar-open'))});
    updateSoundButton(); byId('sound-toggle').addEventListener('click',()=>{ if(audioBlocked&&soundEnabled){ playTone(); return; } soundEnabled=!soundEnabled; audioBlocked=false; localStorage.setItem('overhead-sound',soundEnabled?'on':'muted'); updateSoundButton(); });
    byId('zoom-in').addEventListener('click',()=>changeRadarRange(-1)); byId('zoom-out').addEventListener('click',()=>changeRadarRange(1));
    const savedRange=Number(localStorage.getItem('overhead-radar-range')); if(radarRanges.includes(savedRange)&&savedRange!==25){ currentRadarRange=savedRange; fetch('/radar-range?radius='+savedRange,{cache:'no-store'}); }
    setInterval(update,2000); update(); document.addEventListener('dblclick',()=>document.documentElement.requestFullscreen?.());
  </script>
</body>
</html>
"""


class FrameState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.png = b""
        self.logo = b""
        self.logo_type = "image/svg+xml"
        self.payload: dict[str, Any] = {"mode": "starting", "updated": "never", "total": 0}

    def update(self, image, mode: str, plane: dict[str, Any] | None, distance: float | None, aircraft: list[dict[str, Any]], settings, logo: tuple[bytes, str, str] | None) -> None:
        data = BytesIO()
        image.save(data, format="PNG")
        plane = plane or {}
        code = operator_code(plane)
        logo_data, logo_type, logo_source = logo or (b"", "", "")
        payload = {
            "mode": mode,
            "updated": time.strftime("%H:%M:%S"),
            "total": len(aircraft),
            "aircraft_id": aircraft_identity(plane),
            "callsign": str(plane.get("flight") or "").strip(),
            "registration": str(plane.get("r") or "").strip(),
            "aircraft_type": str(plane.get("t") or "").strip(),
            "aircraft_name": aircraft_name(plane.get("t"), plane.get("desc")),
            "altitude": plane.get("alt_baro"), "speed": plane.get("gs"), "vertical_rate": plane.get("baro_rate"),
            "track": plane.get("track") or 0, "distance_nm": distance, "squawk": plane.get("squawk"), "emergency": plane.get("emergency"),
            "operator_code": code, "airline_name": "", "logo_available": bool(logo_data),
            "logo_source": logo_source,
            "logo_revision": f"{code}-{hashlib.sha256(logo_data).hexdigest()[:12]}" if logo_data else code,
            "radar_radius_nm": settings.radius_nm,
            "radar_contacts": radar_contacts(aircraft, settings, plane),
        }
        with self.lock:
            self.png = data.getvalue()
            self.logo = logo_data
            self.logo_type = logo_type or "application/octet-stream"
            self.payload = payload


def update_state(state: FrameState, settings, demo: bool, logos: LogoStore) -> None:
    aircraft, mode = fetch_traffic_snapshot(settings, demo=demo)
    plane, distance = select_nearest(aircraft, settings)
    image = render(plane, distance, mode)
    save_frame(image, settings)
    state.update(image, mode, plane, distance, aircraft, settings, logos.get(operator_code(plane)))


def refresh_loop(state: FrameState, settings, demo: bool, logos: LogoStore, refresh_event: threading.Event) -> None:
    while True:
        refresh_event.wait(timeout=max(1.0, settings.refresh_seconds))
        refresh_event.clear()
        try:
            update_state(state, settings, demo, logos)
        except Exception as exc:
            with state.lock:
                state.payload["mode"] = "error"
                state.payload["updated"] = time.strftime("%H:%M:%S")
                state.payload["error"] = type(exc).__name__


def handler_factory(state: FrameState, settings, refresh_event: threading.Event, tone_path: Path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            request_url = urlsplit(self.path)
            path = request_url.path
            if path == "/": self.send(HTTPStatus.OK, "text/html; charset=utf-8", PAGE)
            elif path == "/frame.png":
                with state.lock: body = state.png
                self.send(HTTPStatus.OK, "image/png", body, cache=False)
            elif path == "/status":
                with state.lock: body = json.dumps(state.payload).encode()
                self.send(HTTPStatus.OK, "application/json", body, cache=False)
            elif path == "/operator-logo":
                with state.lock: body, content_type = state.logo, state.logo_type
                if body: self.send(HTTPStatus.OK, content_type, body, cache=True)
                else: self.send(HTTPStatus.NOT_FOUND, "text/plain", b"No logo", cache=False)
            elif path == "/beep-tone.mp3":
                try:
                    body = tone_path.read_bytes()
                except OSError:
                    self.send(HTTPStatus.NOT_FOUND, "text/plain", b"beep-tone.mp3 not found", cache=False)
                else:
                    self.send(HTTPStatus.OK, "audio/mpeg", body, cache=True)
            elif path == "/radar-range":
                try:
                    radius = int(parse_qs(request_url.query).get("radius", [""])[0])
                except (TypeError, ValueError):
                    radius = 0
                if radius not in RADAR_RANGES:
                    self.send(HTTPStatus.BAD_REQUEST, "application/json", json.dumps({"error": "invalid range", "allowed": RADAR_RANGES}).encode(), cache=False)
                else:
                    settings.radius_nm = float(radius)
                    with state.lock: state.payload["radar_radius_nm"] = radius
                    refresh_event.set()
                    self.send(HTTPStatus.OK, "application/json", json.dumps({"radius_nm": radius}).encode(), cache=False)
            else: self.send(HTTPStatus.NOT_FOUND, "text/plain", b"Not found")

        def send(self, status, content_type, body, cache=True) -> None:
            self.send_response(status); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "public, max-age=30" if cache else "no-store"); self.end_headers(); self.wfile.write(body)

        def log_message(self, fmt, *args) -> None: return

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.json"); parser.add_argument("--host", default="127.0.0.1"); parser.add_argument("--port", type=int, default=8765); parser.add_argument("--demo", action="store_true"); parser.add_argument("--open", action="store_true", dest="open_browser")
    args = parser.parse_args(); settings = load_settings(Path(args.config)); state = FrameState(); logos = LogoStore(); refresh_event = threading.Event(); update_state(state, settings, args.demo, logos)
    threading.Thread(target=refresh_loop, args=(state, settings, args.demo, logos, refresh_event), daemon=True).start()
    tone_path = Path(__file__).resolve().with_name("beep-tone.mp3")
    server = ThreadingHTTPServer((args.host, args.port), handler_factory(state, settings, refresh_event, tone_path)); url = f"http://{args.host}:{args.port}/"
    print(f"Over-Head monitor running at {url}"); print("Double-click the display to enter browser full-screen mode.")
    if args.open_browser: webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
    return 0


if __name__ == "__main__": raise SystemExit(main())
