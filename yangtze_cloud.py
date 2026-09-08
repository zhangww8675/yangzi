"""Fetch one round of Yangtze observations for GitHub Actions."""
import csv
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup


OUTPUT_DIR = Path(__file__).resolve().parent / "长江数据"
FIELDS = ["站名", "时间", "水位(m)", "流量(m3/s)"]
URLS = [
    "http://www.cjh.com.cn/swyb/sssq.html",
    "http://www.cjh.com.cn/sqindex.html",
]
CHINA_TIME = timezone(timedelta(hours=8))


def clean(value):
    if value is None or str(value).strip() == "":
        return "-"
    return str(value).strip()


def format_time(value):
    text = clean(value)
    if re.fullmatch(r"\d{10}(?:\.0+)?|\d{13}(?:\.0+)?", text):
        number = float(text)
        if number > 100_000_000_000:
            number /= 1000
        return datetime.fromtimestamp(number, CHINA_TIME).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    normalized = re.sub(r"\s+", " ", text)
    for pattern in (
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%y/%m/%d %H:%M",
    ):
        try:
            return datetime.strptime(normalized, pattern).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except ValueError:
            pass
    raise ValueError(f"无法识别观测时间：{text}")


def records_from_json(html):
    candidates = []
    try:
        candidates.append(json.loads(html))
    except ValueError:
        pass

    for match in re.finditer(r"\b(?:sssq|waterData|data)\s*=\s*(\[)", html):
        try:
            value, _ = json.JSONDecoder().raw_decode(html[match.start(1):])
            candidates.append(value)
        except ValueError:
            continue

    for data in candidates:
        if not isinstance(data, list):
            continue
        records = []
        for item in data:
            if not isinstance(item, dict) or not item.get("stnm") or not item.get("tm"):
                continue
            if "z" not in item and "q" not in item:
                continue
            flow = clean(item.get("q"))
            if clean(item.get("oq")) != "-":
                flow = f"{flow}(入)/{clean(item.get('oq'))}(出)"
            records.append({
                "站名": clean(item["stnm"]),
                "时间": format_time(item["tm"]),
                "水位(m)": clean(item.get("z")),
                "流量(m3/s)": flow,
            })
        if records:
            return records
    return []


def records_from_table(html):
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        positions = None
        records = []
        for row in table.find_all("tr"):
            cells = [
                cell.get_text(strip=True)
                for cell in row.find_all(["td", "th"], recursive=False)
            ]
            header_positions = [
                next((i for i, text in enumerate(cells) if label in text), None)
                for label in ("站名", "时间", "水位", "流量")
            ]
            if all(i is not None for i in header_positions):
                positions = header_positions
                continue
            if positions is None or len(cells) <= max(positions):
                continue
            values = [clean(cells[i]) for i in positions]
            if values[0] == "-" or not re.search(r"\d{1,2}:\d{2}", values[1]):
                continue
            values[1] = format_time(values[1])
            records.append(dict(zip(FIELDS, values)))
        if records:
            return records
    return []


def fetch_data(session):
    errors = []
    for url in URLS:
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            for encoding in ("utf-8", "gb18030"):
                try:
                    html = response.content.decode(encoding)
                except UnicodeDecodeError:
                    continue
                records = records_from_json(html) or records_from_table(html)
                if records:
                    return records
            errors.append(f"{url}: 未找到预期数据")
        except (requests.RequestException, ValueError) as error:
            errors.append(f"{url}: {error}")
    raise RuntimeError("；".join(errors))


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
                history[row["时间"]] = row

    added = updated = 0
    for record in new_records:
        key = record["时间"]
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
            for key in sorted(history):
                writer.writerow(history[key])
        temporary.replace(output)
    return added, updated


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Referer": "http://www.cjh.com.cn/",
        "Cache-Control": "no-cache",
    })
    try:
        records = fetch_data(session)
    finally:
        session.close()

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
