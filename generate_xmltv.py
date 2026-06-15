import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

CHANNELS_FILE = Path("channels.m3u")
OUTPUT_FILE = Path("guide.xml")
SCREENSHOT_FILE = Path("debug_screenshot.jpg")
CROP_FILE = Path("debug_crop.jpg")

# Adjust this after checking debug_crop.jpg.
# Format: crop=width:height:x:y
CROP_FILTER = "crop=900:220:80:500,scale=1800:-1"

PROGRAMME_HOURS = 2


def parse_m3u(path):
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    channels = []

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#EXTINF") and i + 1 < len(lines):
            url = lines[i + 1]
            tvg_name = _attr(line, "tvg-name") or line.split(",", 1)[-1].strip()
            tvg_logo = _attr(line, "tvg-logo")
            channel_id = slugify(tvg_name)
            channels.append({
                "id": channel_id,
                "name": tvg_name,
                "logo": tvg_logo,
                "url": url,
            })
            i += 2
        else:
            i += 1

    return channels


def _attr(line, name):
    match = re.search(rf'{name}="([^"]*)"', line)
    return match.group(1).strip() if match else ""


def slugify(value):
    value = value.replace("ᴴᴰ", "HD")
    value = re.sub(r"[^a-zA-Z0-9]+", ".", value).strip(".").lower()
    return value or "channel"


def run_command(cmd):
    return subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
    )


def detect_title(stream_url, fallback_title):
    try:
        run_command([
            "ffmpeg", "-y",
            "-i", stream_url,
            "-frames:v", "1",
            str(SCREENSHOT_FILE),
        ])

        run_command([
            "ffmpeg", "-y",
            "-i", str(SCREENSHOT_FILE),
            "-vf", CROP_FILTER,
            str(CROP_FILE),
        ])

        ocr = run_command([
            "tesseract",
            str(CROP_FILE),
            "stdout",
            "--psm", "6",
        ]).stdout

        title = clean_ocr(ocr)
        return title or fallback_title
    except Exception as exc:
        print(f"OCR failed for {fallback_title}: {exc}")
        return fallback_title


def clean_ocr(text):
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("ᴴᴰ", "").strip()
    text = re.sub(r"^[^A-Za-z0-9]+", "", text)
    text = re.sub(r"[^A-Za-z0-9)]+$", "", text)
    return text


def build_xmltv(channels):
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    stop = now + timedelta(hours=PROGRAMME_HOURS)

    tv = ET.Element("tv", {"generator-info-name": "OnePlay OCR XMLTV Generator"})

    for ch in channels:
        channel = ET.SubElement(tv, "channel", id=ch["id"])
        ET.SubElement(channel, "display-name").text = ch["name"]
        if ch.get("logo"):
            ET.SubElement(channel, "icon", src=ch["logo"])

    for ch in channels:
        print(f"Checking {ch['name']}...")
        title = detect_title(ch["url"], ch["name"])
        print(f"Detected title for {ch['name']}: {title}")

        programme = ET.SubElement(tv, "programme", {
            "channel": ch["id"],
            "start": now.strftime("%Y%m%d%H%M%S %z"),
            "stop": stop.strftime("%Y%m%d%H%M%S %z"),
        })
        ET.SubElement(programme, "title").text = title
        ET.SubElement(programme, "desc").text = f"Detected by OCR from {ch['name']}"

    tree = ET.ElementTree(tv)
    ET.indent(tree, space="  ")
    tree.write(OUTPUT_FILE, encoding="utf-8", xml_declaration=True)


def main():
    channels = parse_m3u(CHANNELS_FILE)
    if not channels:
        raise SystemExit("No channels found in channels.m3u")

    build_xmltv(channels)
    print(f"Created {OUTPUT_FILE} for {len(channels)} channel(s)")


if __name__ == "__main__":
    main()
