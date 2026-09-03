import csv
import json
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

API_URL = "https://api.gsu.edu/proxy/handler/parking/spaces-available"
DATA_FILE = Path("data/parking.csv")

# GSU parking API 호출
with urllib.request.urlopen(API_URL, timeout=30) as response:
    data = json.load(response)

# T Deck은 [0][0]
tdeck = data[0][0]

free_spaces = int(tdeck["free_spaces"])
total_spaces = int(tdeck["total_spaces"])
occupancy = tdeck["occupancy"]

# Atlanta 현지 시간
now = datetime.now(ZoneInfo("America/New_York"))

# data 폴더가 없으면 생성
DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

file_exists = DATA_FILE.exists()

# CSV에 새로운 관측값 추가
with DATA_FILE.open("a", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)

    if not file_exists:
        writer.writerow([
            "timestamp",
            "free_spaces",
            "total_spaces",
            "occupancy"
        ])

    writer.writerow([
        now.isoformat(timespec="seconds"),
        free_spaces,
        total_spaces,
        occupancy
    ])

print(
    f"{now.isoformat(timespec='seconds')} | "
    f"T Deck: {free_spaces}/{total_spaces} free | "
    f"occupancy={occupancy}"
)
