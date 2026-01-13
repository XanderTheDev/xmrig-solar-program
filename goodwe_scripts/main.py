import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
import time
from collections import deque
from requests.exceptions import RequestException
import requests

from config import args

# --------------------------------
# Config
# --------------------------------
MONTHLY_FILE = Path("monthly_stats.json")
RAPL_MAX_UJ = 2**32  # 32-bit counter max (adjust if your CPU uses 48-bit)

XMRIG_CMD = [
    "xmrig", "-c", "/home/xander/.config/xmrig/config.json",
    "--api-worker-id=worker1", "--http-port=42000"
]

CPU_ENERGY_PATH = "/sys/class/powercap/intel-rapl:0/energy_uj"

def read_int(path):
    with open(path, "r") as f:
        return int(f.read().strip())

class PowerMonitor:
    def __init__(self, rolling_window=5):
        self.last_cpu_energy = read_int(CPU_ENERGY_PATH)
        self.last_time = time.time()
        self.rolling_window = rolling_window
        self.recent_watts = []

    def read_cpu_power_watts(self):
        now = time.time()
        energy = read_int(CPU_ENERGY_PATH)

        # Modular arithmetic to handle RAPL counter wrap
        delta_energy = (energy - self.last_cpu_energy) % RAPL_MAX_UJ

        delta_time_s = now - self.last_time
        self.last_cpu_energy = energy
        self.last_time = now

        if delta_time_s <= 0:
            power = 0.0
        else:
            power = delta_energy / 1_000_000 / delta_time_s  # µJ → J → W

        # Keep rolling average
        self.recent_watts.append(power)
        if len(self.recent_watts) > self.rolling_window:
            self.recent_watts.pop(0)

        return sum(self.recent_watts) / len(self.recent_watts)

    def read_total_power(self):
        # Only CPU for now
        return self.read_cpu_power_watts()

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
    headers = {"Content-Type": "application/json", "Token": '{"version":"v3.1","client":"ios","language":"en"}'}
    payload = {"account": email, "pwd": password, "agreement_agreement": 0, "is_local": False}
    r = requests.post(url, headers=headers, json=payload)
    r.raise_for_status()
    data = r.json()["data"]
    return data["uid"], data["token"], data["timestamp"]

def get_monthly_generation(uid, token, timestamp, plant_id, date_str):
    url = "https://eu.semsportal.com/api/v2/Charts/GetChartByPlant"
    headers = {
        "Content-Type": "application/json",
        "Token": json.dumps({"uid": uid, "timestamp": timestamp, "token": token, "client": "ios", "version": "v3.1", "language": "en"})
    }
    payload = {"id": plant_id, "date": date_str, "range": "3", "chartIndexId": "3", "isDetailFull": ""}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        r.raise_for_status()
        return r.json().get("data", {})
    except RequestException as e:
        print(f"[Warning] Could not fetch solar data: {e}")
        return {}

def ensure_previous_11_months_saved(uid, token, timestamp, plant_id):
    today = datetime.now().strftime("%Y-%m-%d")
    data = get_monthly_generation(uid, token, timestamp, plant_id, today)
    monthly_data = load_monthly_data()

    for line in data.get("lines", []):
        if line.get("label") == "Generation (kWh)":
            xy = line.get("xy", [])
            if not xy:
                return
            for entry in xy[:-1]:
                month = entry["x"]
                solar_val = float(entry["y"])
                if month not in monthly_data:
                    monthly_data[month] = {}
                    monthly_data[month]["solar"] = round(solar_val, 3)
                    print(f"[Startup] Saved solar {month} = {solar_val} kWh")

    save_monthly_data(monthly_data)

def fetch_solar_this_month(uid, token, timestamp, plant_id):
    today = datetime.now().strftime("%Y-%m-%d")
    data = get_monthly_generation(uid, token, timestamp, plant_id, today)
    for line in data.get("lines", []):
        if line.get("label") == "Generation (kWh)":
            xy = line.get("xy", [])
            if xy:
                last_entry = xy[-1]
                return float(last_entry["y"])
    return None

# --------------------------------
# Main
# --------------------------------
def main():
    uid, token, timestamp = sems_login(args["gw_account"], args["gw_password"])
    plant_id = args["gw_station_id"]

    ensure_previous_11_months_saved(uid, token, timestamp, plant_id)

    print(f"[{datetime.now()}] Starting miners...")
    xmrig_process = subprocess.Popen(XMRIG_CMD)

    power_monitor = PowerMonitor()
    time.sleep(5)
    monthly_data = load_monthly_data()
    current_month = month_key()
    pc_month_total_Wh = monthly_data.get(current_month, {}).get("pc_kwh_used", 0) * 1000
    samples_24h = deque(maxlen=1440)  # 24h rolling window

    try:
        while True:
            loop_start = time.time()

            # Read power (averaged over interval)
            watts = power_monitor.read_total_power()
            samples_24h.append(watts)
            pc_month_total_Wh += watts / 60.0  # Wh per minute
            pc_month_total_kwh = pc_month_total_Wh / 1000.0
            avg_24h = sum(samples_24h) / len(samples_24h) if samples_24h else 0.0

            # Update JSON
            if current_month not in monthly_data:
                monthly_data[current_month] = {}
            monthly_data[current_month]["pc_kwh_used"] = round(pc_month_total_kwh, 4)
            monthly_data[current_month]["current_avg_watts_24h"] = round(avg_24h, 2)

            solar_this_month = fetch_solar_this_month(uid, token, timestamp, plant_id)
            if solar_this_month is not None:
                monthly_data[current_month]["solar_this_month"] = round(solar_this_month, 3)

            save_monthly_data(monthly_data)

            # Monthly rollover
            new_month = month_key()
            if new_month != current_month:
                print(f"[Month Change] Finalizing {current_month} PC usage = {pc_month_total_kwh:.3f} kWh")
                current_month = new_month
                pc_month_total_Wh = 0.0
                samples_24h.clear()

            # Sleep until next minute
            elapsed = time.time() - loop_start
            time.sleep(max(0, 60 - elapsed))

    except KeyboardInterrupt:
        print("Stopping miners...")
        xmrig_process.terminate()
        xmrig_process.wait()
        print("Miners stopped.")

if __name__ == "__main__":
    main()
