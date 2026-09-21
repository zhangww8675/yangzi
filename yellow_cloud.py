"""黄河水情：每站一表；每天定时，失败后自动重试。"""
import csv
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup


BASE = "http://61.163.88.227:8006"
URLS = [
    BASE + "/hwsq2.aspx?sr=0nkRxv6s9CTRMlwRgmfFF6jTpJPtAv87",
    BASE + "/hwsq.aspx?sr=0nkRxv6s9CTRMlwRgmfFF6jTpJPtAv87",
]
OUTPUT_DIR = Path(__file__).resolve().parent / "黄河数据"
FIELDS = ["河名", "站名", "时间", "水位(m)", "流量(m3/s)"]
CHINA_TIME = timezone(timedelta(hours=8))

# 每天北京时间 09:20 正常抓取；失败后每 30 分钟重试。
RUN_HOUR = 9
RUN_MINUTE = 20
RETRY_SECONDS = 30 * 60
REQUEST_ATTEMPTS = 4


def format_time(text):
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


def make_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": BASE + "/",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })
    return session


def decode_html(content):
    for encoding in ("utf-8", "gb18030", "gbk", "gb2312"):
        text = content.decode(encoding, errors="replace")
        if "河名" in text and "站名" in text and "水位" in text:
            return text
    return None


def parse_table(html):
    soup = BeautifulSoup(html, "html.parser")
    records = []
    for row in soup.find_all("tr"):
        values = [
            cell.get_text(strip=True)
            for cell in row.find_all(["td", "th"], recursive=False)
        ]
        if len(values) != 5:
            continue
        if not re.fullmatch(r"\d{2}-\d{2}\s+\d{2}:\d{2}", values[2]):
            continue
        river, station, observed, level, flow = values
        if not station:
            continue
        records.append({
            "河名": river.strip(),
            "站名": station.strip(),
            "时间": format_time(observed),
            "水位(m)": level.strip() or "-",
            "流量(m3/s)": flow.strip() or "-",
        })
    return records


def fetch_data():
    errors = []
    session = make_session()
    try:
        # 首页失败不影响后续尝试。
        try:
            session.get(BASE + "/", timeout=15)
        except requests.RequestException:
            pass

        for attempt in range(1, REQUEST_ATTEMPTS + 1):
            for url in URLS:
                try:
                    response = session.get(
                        url,
                        params={"t": int(time.time() * 1000)},
                        timeout=30,
                    )
                    if response.status_code != 200:
                        errors.append(f"{url}: HTTP {response.status_code}")
                        continue
                    html = decode_html(response.content)
                    if html is None:
                        errors.append(f"{url}: 页面中没有水情表头")
                        continue
                    records = parse_table(html)
                    if records:
                        return records
                    errors.append(f"{url}: 页面中没有有效数据行")
                except requests.RequestException as error:
                    errors.append(f"{url}: {error}")

            if attempt < REQUEST_ATTEMPTS:
                print(
                    f"本轮未取得数据，10 秒后进行第 {attempt + 1} 次尝试……",
                    flush=True,
                )
                time.sleep(10)
    finally:
        session.close()

    recent = "；".join(errors[-4:])
    raise RuntimeError(f"黄河网站当前不可用。最近错误：{recent}")


def station_filename(station):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", station).rstrip(" .")
    return (name or "未知站点") + ".csv"


def save_station(station, records):
    output = OUTPUT_DIR / station_filename(station)
    history = {}
    if output.exists():
        with output.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != FIELDS:
                raise RuntimeError(f"{output.name} 表头不匹配")
            for row in reader:
                history[(row["河名"], row["时间"])] = row

    added = updated = 0
    for record in records:
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


def run_once():
    records = fetch_data()
    groups = {}
    for record in records:
        groups.setdefault(record["站名"], []).append(record)

    total_added = total_updated = 0
    for station, station_records in groups.items():
        added, updated = save_station(station, station_records)
        total_added += added
        total_updated += updated
        print(f"{station}：新增 {added} 条，修订 {updated} 条", flush=True)

    print(
        f"共检查 {len(groups)} 个站点；新增 {total_added} 条，"
        f"修订 {total_updated} 条。",
        flush=True,
    )


def next_daily_run():
    now = datetime.now(CHINA_TIME)
    target = now.replace(
        hour=RUN_HOUR, minute=RUN_MINUTE, second=0, microsecond=0
    )
    if target <= now:
        target += timedelta(days=1)
    return target


def main():
    """GitHub Actions 每次调用只执行一轮，定时由 workflow 管理。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("开始抓取黄河水情……", flush=True)
    run_once()


if __name__ == "__main__":
    main()
