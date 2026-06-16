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

CROP_FILTER = os.getenv(
    "CROP_FILTER",
    "crop=700:150:0:950,scale=3000:-1"
)

PROGRAMME_HOURS = int(os.getenv("PROGRAMME_HOURS", "1"))
MAX_CHANNELS = int(os.getenv("MAX_CHANNELS", "0"))


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

    text = re.sub(
        r"^(IE|IZ|IP|LIE|LE|LZ|ZZ|PP|IFS|ES|KE|JP|WR|HE|FE|BE|AZ|Y|E|B|A|I|2)\s+",
        "",
        text,
        flags=re.IGNORECASE,
    )

    if re.fullmatch(r"\((19|20)\d\d\)", text):
        return ""

    match = re.search(
        r"([A-Z0-9][A-Z0-9 :'\-&,.!]{3,})\s*\((19|20)\d\d\)",
        text.upper(),
    )

    if match:
        cleaned = match.group(0).strip()

        if re.fullmatch(r"\((19|20)\d\d\)", cleaned):
            return ""

        return cleaned

    text = re.sub(r"[^A-Za-z0-9 '&:,.!()\-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -:|.")

    if len(text) < 4:
        return ""

    return text


def title_has_year(title):
    return bool(re.search(r"\((19|20)\d\d\)", title or ""))


def split_title_year(title):
    match = re.search(r"^(.*?)\s*\(((19|20)\d\d)\)\s*$", title or "")
    if not match:
        return title, ""

    clean_title = match.group(1).strip()
    year = match.group(2).strip()
    return clean_title, year


def score_title(title):
    if not title:
        return 0

    score = 0

    if title_has_year(title):
        score += 100

    score += min(len(title), 80)

    if re.fullmatch(r"\d+\s*\((19|20)\d\d\)", title):
        score -= 50

    if len(title.replace(" ", "")) < 4:
        score -= 50

    return score


def choose_best_title(detected_titles, fallback):
    if not detected_titles:
        return fallback

    movie_titles = [
        t for t in detected_titles
        if title_has_year(t) and not re.fullmatch(r"\d+\s*\((19|20)\d\d\)", t)
    ]

    if movie_titles:
        counts = Counter(movie_titles)
        return sorted(
            movie_titles,
            key=lambda t: (counts[t], score_title(t)),
            reverse=True,
        )[0]

    counts = Counter(detected_titles)
    return sorted(
        detected_titles,
        key=lambda t: (counts[t], score_title(t)),
        reverse=True,
    )[0]


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

    return choose_best_title(detected_titles, channel["name"])


def write_xmltv(channels, titles):
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    stop = now + timedelta(hours=PROGRAMME_HOURS)

    tv = ET.Element("tv", {
        "generator-info-name": "OnePlay GitHub OCR XMLTV",
        "source-info-name": "OnePlay OCR",
    })

    for ch in channels:
        c = ET.SubElement(tv, "channel", id=ch["id"])
        ET.SubElement(c, "display-name", {"lang": "en"}).text = ch["name"]

        if ch.get("logo"):
            ET.SubElement(c, "icon", src=ch["logo"])

    for ch in channels:
        detected_title = titles.get(ch["id"], ch["name"])
        clean_title, year = split_title_year(detected_title)

        p = ET.SubElement(tv, "programme", {
            "channel": ch["id"],
            "start": now.strftime("%Y%m%d%H%M%S %z"),
            "stop": stop.strftime("%Y%m%d%H%M%S %z"),
        })

        ET.SubElement(p, "title", {"lang": "en"}).text = clean_title

        if year:
            ET.SubElement(p, "sub-title", {"lang": "en"}).text = f"Movie ({year})"
        else:
            ET.SubElement(p, "sub-title", {"lang": "en"}).text = "Movie"

        ET.SubElement(p, "desc", {"lang": "en"}).text = "24/7 channel"
        ET.SubElement(p, "category", {"lang": "en"}).text = "Movie"

        if year:
            ET.SubElement(p, "date").text = year

        if ch.get("logo"):
            ET.SubElement(p, "icon", src=ch["logo"])

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
