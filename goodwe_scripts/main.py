import json
import subprocess
from datetime import datetime
from pathlib import Path
import time
from collections import deque

import requests

from config import args

# --------------------------------
# Config
# --------------------------------
MONTHLY_FILE = Path("monthly_stats.json")

# Miner commands
XMRIG_CMD = [
    "xmrig", "-c", "/home/YOUR_USER/.config/xmrig/config.json",
    "--api-worker-id=worker1", "--http-port=42000"
]

# --------------------------------
# Power measurement
# --------------------------------
CPU_ENERGY_PATH = "/sys/class/powercap/intel-rapl:0/energy_uj"
GPU1_POWER_PATH = "/sys/class/drm/card1/device/hwmon/hwmon4/power1_average"

def read_int(path):
    with open(path, "r") as f:
        return int(f.read().strip())

def read_cpu_power_watts():
    e1 = read_int(CPU_ENERGY_PATH)
    time.sleep(1)
    e2 = read_int(CPU_ENERGY_PATH)
    return (e2 - e1) / 1_000_000  # microjoules → joules/sec → watts

def read_gpu_power_watts(path):
    try:
        return read_int(path) / 1_000_000
    except FileNotFoundError:
        return 0.0

def read_total_system_power():
    cpu = read_cpu_power_watts()
    gpu1 = read_gpu_power_watts(GPU1_POWER_PATH)
    return cpu + gpu1

# --------------------------------
# Monthly Stats Helpers
# --------------------------------
def load_monthly_data():
    if MONTHLY_FILE.exists():
        with open(MONTHLY_FILE, "r") as f:
            return json.load(f)
    return {}

def save_monthly_data(data):
    with open(MONTHLY_FILE, "w") as f:
        json.dump(data, f, indent=4)

def month_key(dt=None):
    dt = dt or datetime.now()
    return f"{dt.year}-{dt.month:02d}"

# --------------------------------
# SEMSPortal API Helpers
# --------------------------------
def sems_login(email, password):
    url = "https://eu.semsportal.com/api/v2/common/crosslogin"
    headers = {
        "Content-Type": "application/json",
        "Token": '{"version":"v3.1","client":"ios","language":"en"}'
    }
    payload = {
        "account": email,
        "pwd": password,
        "agreement_agreement": 0,
        "is_local": False
    }
    r = requests.post(url, headers=headers, json=payload)
    r.raise_for_status()
    data = r.json()["data"]
    return data["uid"], data["token"], data["timestamp"]

def get_monthly_generation(uid, token, timestamp, plant_id, date_str):
    url = "https://eu.semsportal.com/api/v2/Charts/GetChartByPlant"
    headers = {
        "Content-Type": "application/json",
        "Token": json.dumps({
            "uid": uid,
            "timestamp": timestamp,
            "token": token,
            "client": "ios",
            "version": "v3.1",
            "language": "en"
        })
    }
    payload = {
        "id": plant_id,
        "date": date_str,
        "range": "3",  # 12 maanden
        "chartIndexId": "3",
        "isDetailFull": ""
    }
    r = requests.post(url, headers=headers, json=payload)
    r.raise_for_status()
    return r.json()["data"]

def ensure_previous_11_months_saved(uid, token, timestamp, plant_id):
    """
    Slaat de vorige 11 maanden op (exclusief de huidige maand) als ze nog niet bestaan.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    data = get_monthly_generation(uid, token, timestamp, plant_id, today)
    monthly_data = load_monthly_data()

    # Zoek de lijn "Generation (kWh)"
    for line in data.get("lines", []):
        if line.get("label") == "Generation (kWh)":
            xy = line.get("xy", [])
            if not xy:
                return

            # Alle maanden behalve de laatste (huidige maand)
            for entry in xy[:-1]:
                month = entry["x"]
                solar_val = float(entry["y"])
                if month not in monthly_data:
                    monthly_data[month] = {}
                    monthly_data[month]["solar"] = round(solar_val, 3)
                    print(f"[Startup] Saved solar {month} = {solar_val} kWh")

    save_monthly_data(monthly_data)

def fetch_solar_this_month(uid, token, timestamp, plant_id):
    """
    Haalt de huidige maand op (laatste entry van xy).
    """
    today = datetime.now().strftime("%Y-%m-%d")
    data = get_monthly_generation(uid, token, timestamp, plant_id, today)

    for line in data.get("lines", []):
        if line.get("label") == "Generation (kWh)":
            xy = line.get("xy", [])
            if xy:
                last_entry = xy[-1]  # huidige maand
                return float(last_entry["y"])
    return None

# --------------------------------
# Monthly rollover for PC usage
# --------------------------------
def roll_month_if_needed(monthly_data, current_month, pc_month_total_kwh):
    if current_month not in monthly_data:
        prev_month = month_key(datetime.now().replace(day=1) - timedelta(days=1))
        if prev_month not in monthly_data:
            monthly_data[prev_month] = {}
        monthly_data[prev_month]["pc_kwh_used"] = pc_month_total_kwh
        save_monthly_data(monthly_data)
        print(f"[Month Change] Saving PC usage for {prev_month}: {pc_month_total_kwh:.3f} kWh")
        return True
    return False

# --------------------------------
# Main
# --------------------------------
def main():
    # Login SEMSPortal
    uid, token, timestamp = sems_login(args["gw_account"], args["gw_password"])
    plant_id = args["gw_station_id"]

    # Sla vorige 11 maanden op
    ensure_previous_11_months_saved(uid, token, timestamp, plant_id)

    print(f"[{datetime.now()}] Starting miners...")
    xmrig_process = subprocess.Popen(XMRIG_CMD)

    # --- Power tracking state ---
    monthly_data = load_monthly_data()
    current_month = month_key()

    pc_month_total_Wh = monthly_data.get(current_month, {}).get("pc_kwh_used", 0) * 1000
    samples_24h = deque(maxlen=1440)  # 1440 minuten = 24 uur

    try:
        while True:
            loop_start = time.time()

            # ---- Read power ----
            watts = read_total_system_power()

            # Voeg toe aan 24h sample window
            samples_24h.append(watts)

            # Monthly Wh accumulation
            pc_month_total_Wh += watts / 60.0  # Wh per minuut

            # Convert to kWh
            pc_month_total_kwh = pc_month_total_Wh / 1000.0

            # Rolling 24h average
            avg_24h = sum(samples_24h) / len(samples_24h) if samples_24h else 0.0

            # ---- Update live JSON voor current month ----
            if current_month not in monthly_data:
                monthly_data[current_month] = {}

            monthly_data[current_month]["pc_kwh_used"] = round(pc_month_total_kwh, 4)
            monthly_data[current_month]["current_avg_watts_24h"] = round(avg_24h, 2)

            # ---- Fetch solar production voor current month ----
            solar_this_month = fetch_solar_this_month(uid, token, timestamp, plant_id)
            if solar_this_month is not None:
                monthly_data[current_month]["solar_this_month"] = round(solar_this_month, 3)

            save_monthly_data(monthly_data)

            # ---- Monthly rollover ----
            new_month = month_key()
            if new_month != current_month:
                print(f"[Month Change] Finalizing {current_month} PC usage = {pc_month_total_kwh:.3f} kWh")
                current_month = new_month
                pc_month_total_Wh = 0.0
                samples_24h.clear()

            # Wacht tot volgende minuut
            elapsed = time.time() - loop_start
            time.sleep(max(0, 60 - elapsed))

    except KeyboardInterrupt:
        print("Stopping miners...")
        xmrig_process.terminate()
        xmrig_process.wait()
        print("Miners stopped.")


if __name__ == "__main__":
    main()
