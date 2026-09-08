"""Fetch one round of Yellow River observations for GitHub Actions."""
import csv
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup


URL = (
    "http://61.163.88.227:8006/hwsq2.aspx"
    "?sr=0nkRxv6s9CTRMlwRgmfFF6jTpJPtAv87"
)
OUTPUT_DIR = Path(__file__).resolve().parent / "黄河数据"
FIELDS = ["河名", "站名", "时间", "水位(m)", "流量(m3/s)"]
CHINA_TIME = timezone(timedelta(hours=8))


def format_time(text):
    """Add the most plausible year to the site's month-day timestamp."""
    text = re.sub(r"\s+", " ", text.strip())
    now = datetime.now(CHINA_TIME).replace(tzinfo=None)
    for year in (now.year, now.year - 1):
        try:
            value = datetime.strptime(f"{year}-{text}", "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        if value <= now:
            return value.strftime("%Y-%m-%d %H:%M")
    raise ValueError(f"无法识别观测时间：{text}")


def fetch_data():
    response = requests.get(
        URL,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 Chrome/120 Safari/537.36",
            "Referer": "http://61.163.88.227:8006/",
            "Cache-Control": "no-cache",
        },
        params={"t": int(datetime.now().timestamp() * 1000)},
        timeout=30,
    )
    response.raise_for_status()

    html = None
    for encoding in ("utf-8", "gb18030"):
        text = response.content.decode(encoding, errors="replace")
        if "河名" in text and "站名" in text and "水位" in text:
            html = text
            break
    if html is None:
        raise RuntimeError("网页中没有找到黄河水情表头")

    soup = BeautifulSoup(html, "html.parser")
    records = []
    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        values = [cell.get_text(strip=True) for cell in cells]
        if len(values) != 5:
            continue
        if not re.fullmatch(r"\d{2}-\d{2}\s+\d{2}:\d{2}", values[2]):
            continue
        river, station, observed, level, flow = values
        if not station:
            continue
        records.append({
            "河名": river,
            "站名": station,
            "时间": format_time(observed),
            "水位(m)": level or "-",
            "流量(m3/s)": flow or "-",
        })
    if not records:
        raise RuntimeError("没有提取到黄河水情数据，网页结构可能已变化")
    return records


def station_filename(station):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", station).rstrip(" .")
    if not name:
        raise ValueError("站名为空")
    return name + ".csv"


def save_station(station, new_records):
    output = OUTPUT_DIR / station_filename(station)
    history = {}
    if output.exists():
        with output.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != FIELDS:
                raise RuntimeError(f"{output.name} 表头不匹配")
            for row in reader:
                key = (row["河名"], row["时间"])
                history[key] = row

    added = updated = 0
    for record in new_records:
        key = (record["河名"], record["时间"])
        if key not in history:
            history[key] = record
            added += 1
        elif history[key] != record:
            history[key] = record
            updated += 1

    if added or updated:
        temporary = output.with_suffix(".tmp")
        with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            for key in sorted(history, key=lambda item: (item[1], item[0])):
                writer.writerow(history[key])
        temporary.replace(output)
    return added, updated


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    records = fetch_data()
    groups = {}
    for record in records:
        groups.setdefault(record["站名"], []).append(record)

    total_added = total_updated = 0
    for station, station_records in groups.items():
        added, updated = save_station(station, station_records)
        total_added += added
        total_updated += updated
        print(f"{station}: 新增 {added} 条，修订 {updated} 条")
    print(
        f"完成：{len(groups)} 个站点，新增 {total_added} 条，"
        f"修订 {total_updated} 条。"
    )


if __name__ == "__main__":
    main()

