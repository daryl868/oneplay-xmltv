import os
import re
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

CHANNELS_FILE = "channels.m3u"
OUTPUT_FILE = "guide.xml"

DEBUG_DIR = Path("debug")
DEBUG_DIR.mkdir(exist_ok=True)

# Based on your screenshot:
# title is in the lower-left black bar.
# Format: crop=width:height:x:y
CROP_FILTER = os.getenv(
    "CROP_FILTER",
    "crop=700:160:0:850,scale=2000:-1"
)

PROGRAMME_HOURS = int(os.getenv("PROGRAMME_HOURS", "2"))

# Set to 1 while testing. Change to 0 later for all channels.
MAX_CHANNELS = int(os.getenv("MAX_CHANNELS", "1"))


def slug(value):
    value = value.replace("+", "plus").replace("&", "and")
    value = re.sub(r"[^a-zA-Z0-9]+", ".", value).strip(".").lower()
    return value or "channel"


def parse_attrs(line):
    return dict(re.findall(r'(\S+)="([^"]*)"', line))


def parse_m3u(path):
    lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    channels = []
    pending = None

    for line in lines:
        line = line.strip()

        if not line:
            continue

        if line.startswith("#EXTINF"):
            attrs = parse_attrs(line)
            display_name = (
                line.split(",", 1)[-1].strip()
                if "," in line
                else attrs.get("tvg-name", "Channel")
            )

            pending = {
                "name": attrs.get("tvg-name") or display_name,
                "display_name": display_name,
                "logo": attrs.get("tvg-logo", ""),
                "group": attrs.get("group-title", ""),
            }

        elif pending and not line.startswith("#"):
            pending["url"] = line
            pending["id"] = slug(pending["name"])
            channels.append(pending)
            pending = None

    return channels


def run_cmd(cmd, timeout=45):
    return subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def clean_ocr(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    text = text.replace("ᴴᴰ", "").replace("HD", "").strip()

    # Prefer movie title format like FAST X (2023)
    match = re.search(
        r"([A-Z0-9][A-Z0-9 :'\-&,.!]+)\s*\((19|20)\d\d\)",
        text.upper()
    )

    if match:
        return match.group(0).strip()

    text = re.sub(r"[^A-Za-z0-9 '&:,.!()\-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -:|.")

    if len(text) < 3:
        return ""

    return text


def capture_title(channel, index):
    safe = f"{index:02d}_{channel['id']}"
    detected_titles = []

    for n in range(1, 4):
        shot = DEBUG_DIR / f"{safe}_shot_{n}.jpg"
        crop = DEBUG_DIR / f"{safe}_crop_{n}.jpg"

        print(f"  Capturing frame {n}...", flush=True)

        cap = run_cmd([
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-rw_timeout",
            "15000000",
            "-timeout",
            "15000000",
            "-i",
            channel["url"],
            "-t",
            "8",
            "-frames:v",
            "1",
            str(shot),
        ], timeout=35)

        if cap.returncode != 0 or not shot.exists():
            print(f"  FFmpeg failed: {cap.stderr[:500]}", flush=True)
            continue

        print("  Screenshot captured", flush=True)

        crop_cmd = run_cmd([
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(shot),
            "-vf",
            CROP_FILTER,
            str(crop),
        ], timeout=30)

        if crop_cmd.returncode != 0 or not crop.exists():
            print(f"  Crop failed: {crop_cmd.stderr[:500]}", flush=True)
            continue

        print("  Crop created", flush=True)

        ocr = run_cmd([
            "tesseract",
            str(crop),
            "stdout",
            "--psm",
            "7",
        ], timeout=30)

        if ocr.returncode != 0:
            print(f"  OCR failed: {ocr.stderr[:500]}", flush=True)
            continue

        title = clean_ocr(ocr.stdout)
        print(f"  OCR result: {title}", flush=True)

        if title:
            detected_titles.append(title)

    if detected_titles:
        return Counter(detected_titles).most_common(1)[0][0]

    return channel["name"]


def write_xmltv(channels, titles):
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    stop = now + timedelta(hours=PROGRAMME_HOURS)

    tv = ET.Element("tv", {
        "generator-info-name": "OnePlay GitHub OCR XMLTV"
    })

    for ch in channels:
        c = ET.SubElement(tv, "channel", id=ch["id"])
        ET.SubElement(c, "display-name").text = ch["name"]

        if ch.get("logo"):
            ET.SubElement(c, "icon", src=ch["logo"])

    for ch in channels:
        p = ET.SubElement(tv, "programme", {
            "channel": ch["id"],
            "start": now.strftime("%Y%m%d%H%M%S %z"),
            "stop": stop.strftime("%Y%m%d%H%M%S %z"),
        })

        ET.SubElement(p, "title").text = titles.get(ch["id"], ch["name"])
        ET.SubElement(p, "desc").text = f"Auto-detected by OCR from {ch['name']}"

    tree = ET.ElementTree(tv)
    ET.indent(tree, space="  ")
    tree.write(OUTPUT_FILE, encoding="utf-8", xml_declaration=True)


def main():
    channels = parse_m3u(CHANNELS_FILE)

    if MAX_CHANNELS > 0:
        channels = channels[:MAX_CHANNELS]

    print(f"Found {len(channels)} channels", flush=True)

    titles = {}

    for i, ch in enumerate(channels, start=1):
        print(f"Checking {ch['name']}...", flush=True)
        title = capture_title(ch, i)
        titles[ch["id"]] = title
        print(f"  Final title: {title}", flush=True)

    write_xmltv(channels, titles)
    print(f"Created {OUTPUT_FILE}", flush=True)


if __name__ == "__main__":
    main()
