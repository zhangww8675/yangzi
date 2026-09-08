"""Collect China's major-river observations once for GitHub Actions."""
import csv
import hashlib
import io
import json
import re
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from fontTools.ttLib import TTFont


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "大江大河数据"
FINGERPRINTS_FILE = ROOT / "font_fingerprints.json"
PAGE_URL = "http://xxfb.mwr.cn/sq_djdh.html"
API_URL = "http://xxfb.mwr.cn/OTMuhovshHolkdc/OTMfxrjsvUahgb"
FONT_URL = "http://xxfb.mwr.cn/ttf/{token}.ttf"
FIELDS = [
    "流域", "行政区划", "河名", "站名", "时间",
    "水位(m)", "超警戒水位(m)", "抓取时间",
]
CHINA_TIME = timezone(timedelta(hours=8))
TAG = re.compile(r"#([A-Za-z0-9_]+)otltag(.*?)#FontTag", re.DOTALL)

# The site publishes IPv4 and IPv6, while some GitHub runners cannot route its
# IPv6 address. Force requests to use the reachable IPv4 endpoint.
_SYSTEM_GETADDRINFO = socket.getaddrinfo


def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _SYSTEM_GETADDRINFO(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_getaddrinfo


def request_with_retry(session, url, attempts=6):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(url, timeout=45)
            response.raise_for_status()
            if response.content:
                return response
            last_error = RuntimeError("服务器返回空内容")
        except requests.RequestException as error:
            last_error = error
        if attempt < attempts:
            time.sleep(attempt * 3)
    raise RuntimeError(f"访问失败（已重试 {attempts} 次）：{url}；{last_error}")


def glyph_signature(font, glyph_name):
    coordinates, ends, flags = font["glyf"][glyph_name].getCoordinates(font["glyf"])
    payload = json.dumps(
        [list(map(list, coordinates)), list(ends), list(flags)],
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def build_decoder(font_bytes, fingerprints):
    font = TTFont(io.BytesIO(font_bytes))
    decoder = {}
    for fake_codepoint, glyph_name in font.getBestCmap().items():
        character = fingerprints.get(glyph_signature(font, glyph_name))
        if character is not None:
            decoder[chr(fake_codepoint)] = character
    return decoder


def tagged_parts(value):
    if not isinstance(value, str):
        return []
    return TAG.findall(value)


def decode_value(value, decoders):
    if value is None:
        return "--"
    if not isinstance(value, str):
        return str(value)

    def replace(match):
        token, encoded = match.groups()
        decoder = decoders.get(token)
        if decoder is None:
            raise RuntimeError(f"缺少动态字体解码器：{token}")
        decoded = "".join(decoder.get(character, character) for character in encoded)
        suspicious = [c for c in decoded if 0x3500 <= ord(c) <= 0x4DFF]
        if suspicious:
            raise RuntimeError(f"动态字体存在未识别字符：{token}")
        return decoded

    return TAG.sub(replace, value).strip() or "--"


def safe_part(value):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip().rstrip(" .")
    return name or "未知"


def alert_text(value):
    if value in (None, "", "--"):
        return "--"
    try:
        number = float(value)
        return "--" if number == 0 else format(number, "g")
    except (TypeError, ValueError):
        return str(value).strip() or "--"


def fetch_records():
    fingerprints = json.loads(FINGERPRINTS_FILE.read_text(encoding="utf-8"))
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Referer": PAGE_URL,
        "Accept": "application/json,text/plain,*/*",
        "Cache-Control": "no-cache",
    })
    try:
        # Establish the same session/cookies as a browser before calling the API.
        request_with_retry(session, PAGE_URL)
        response = request_with_retry(session, API_URL)
        payload = response.json()
        if payload.get("returncode") != 0 or not payload.get("result"):
            raise RuntimeError(f"接口没有返回有效数据：{payload.get('message')}")

        tokens = set()
        for item in payload["result"]:
            for value in item.values():
                tokens.update(token for token, _ in tagged_parts(value))

        decoders = {}
        for token in sorted(tokens):
            font_response = request_with_retry(session, FONT_URL.format(token=token))
            decoders[token] = build_decoder(font_response.content, fingerprints)

        captured = datetime.now(CHINA_TIME).strftime("%Y-%m-%d %H:%M:%S")
        records = []
        for item in payload["result"]:
            record = {
                "流域": decode_value(item.get("bsnm"), decoders),
                "行政区划": decode_value(item.get("addvnm"), decoders),
                "河名": decode_value(item.get("rvnm"), decoders),
                "站名": decode_value(item.get("stnm"), decoders),
                "时间": str(item.get("tm") or "").strip(),
                "水位(m)": decode_value(item.get("z"), decoders),
                "超警戒水位(m)": alert_text(item.get("alertValue")),
                "抓取时间": captured,
            }
            if record["站名"] != "--" and record["时间"]:
                records.append(record)
        if not records:
            raise RuntimeError("解码后没有有效站点记录")
        return records
    finally:
        session.close()


def station_path(record):
    return (
        OUTPUT_DIR
        / safe_part(record["流域"])
        / safe_part(record["行政区划"])
        / safe_part(record["河名"])
        / f"{safe_part(record['站名'])}.csv"
    )


def save_station(path, station_records):
    path.parent.mkdir(parents=True, exist_ok=True)
    history = {}
    if path.exists():
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != FIELDS:
                raise RuntimeError(f"表头不匹配：{path}")
            for row in reader:
                history[row["时间"]] = row

    added = updated = 0
    comparison_fields = [field for field in FIELDS if field != "抓取时间"]
    for record in station_records:
        key = record["时间"]
        old = history.get(key)
        if old is None:
            history[key] = record
            added += 1
        elif any(old[field] != record[field] for field in comparison_fields):
            history[key] = record
            updated += 1

    if added or updated:
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            for key in sorted(history):
                writer.writerow(history[key])
        temporary.replace(path)
    return added, updated


def main():
    records = fetch_records()
    groups = {}
    for record in records:
        groups.setdefault(station_path(record), []).append(record)

    total_added = total_updated = 0
    for path, station_records in groups.items():
        added, updated = save_station(path, station_records)
        total_added += added
        total_updated += updated
    print(
        f"完成：接口 {len(records)} 条，站点文件 {len(groups)} 个，"
        f"新增 {total_added} 条，修订 {total_updated} 条。"
    )


if __name__ == "__main__":
    main()
