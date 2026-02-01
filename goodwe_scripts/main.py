import json
import subprocess
import time
from datetime import datetime
from pathlib import Path
from collections import deque
import requests
from requests.exceptions import RequestException
from config import args

# --------------------------------
# Config
# --------------------------------
MONTHLY_FILE = Path("monthly_stats.json")
RAPL_MAX_UJ = 2**32

XMRIG_CMD = [
    "xmrig", "-c", "/home/xander/.config/xmrig/config.json",
    "--api-worker-id=worker1", "--http-port=42000"
]

CPU_ENERGY_PATH = "/sys/class/powercap/intel-rapl:0/energy_uj"

SAMPLE_INTERVAL = 2.0
SAVE_INTERVAL = 60.0


def reconcile_old_months(monthly_data, current_month):
    """Finalize any past months that still contain transient fields"""
    for month, entry in monthly_data.items():
        if month >= current_month:
            continue  # skip current & future months

        dirty = any(
            k in entry
            for k in ("current_watts", "current_avg_watts_24h", "solar_this_month")
        )

        if dirty:
            print(f"[Startup Fix] Finalizing stale month {month}")

            entry.pop("current_watts", None)
            entry.pop("current_avg_watts_24h", None)

            if "solar_this_month" in entry:
                entry["solar"] = entry.pop("solar_this_month")


# --------------------------------
# Utilities
# --------------------------------
def read_int(path):
    with open(path, "r") as f:
        return int(f.read().strip())


def month_key(dt=None):
    dt = dt or datetime.now()
    return f"{dt.year}-{dt.month:02d}"


# --------------------------------
# Energy Accumulator
# --------------------------------
class EnergyAccumulator:
    def __init__(self):
        self.last_energy_uj = read_int(CPU_ENERGY_PATH)
        self.last_read_time = time.time()
        self.month_total_uj = 0
        self.samples_24h = deque(maxlen=int(86400 / SAMPLE_INTERVAL))

    def sample_energy(self):
        now = time.time()
        current_energy_uj = read_int(CPU_ENERGY_PATH)

        delta_energy_uj = (current_energy_uj - self.last_energy_uj) % RAPL_MAX_UJ
        delta_time = now - self.last_read_time

        self.last_energy_uj = current_energy_uj
        self.last_read_time = now

        self.month_total_uj += delta_energy_uj
        self.samples_24h.append(delta_energy_uj)

        if delta_time > 0:
            return delta_energy_uj / 1_000_000 / delta_time
        return 0.0

    def get_month_kwh(self):
        return self.month_total_uj / 1_000_000 / 3600 / 1000

    def get_avg_power_24h(self):
        if not self.samples_24h:
            return 0.0
        total_energy = sum(self.samples_24h)
        total_time = len(self.samples_24h) * SAMPLE_INTERVAL
        return total_energy / 1_000_000 / total_time if total_time else 0.0

    def reset_month(self):
        self.month_total_uj = 0
        self.samples_24h.clear()


# --------------------------------
# Monthly JSON Helpers
# --------------------------------
def load_monthly_data():
    if MONTHLY_FILE.exists():
        with open(MONTHLY_FILE, "r") as f:
            return json.load(f)
    return {}


def save_monthly_data(data):
    with open(MONTHLY_FILE, "w") as f:
        json.dump(data, f, indent=4)


# --------------------------------
# SEMS API
# --------------------------------
def sems_login(email, password):
    url = "https://eu.semsportal.com/api/v2/common/crosslogin"
    headers = {
        "Content-Type": "application/json",
        "Token": '{"version":"v3.1","client":"ios","language":"en"}'
    }
    payload = {"account": email, "pwd": password, "agreement_agreement": 0, "is_local": False}
    r = requests.post(url, headers=headers, json=payload)
    r.raise_for_status()
    data = r.json()["data"]
    return data["uid"], data["token"], data["timestamp"]


def get_monthly_generation(uid, token, timestamp, plant_id):
    today = datetime.now().strftime("%Y-%m-%d")
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
    payload = {"id": plant_id, "date": today, "range": "3", "chartIndexId": "3", "isDetailFull": ""}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        r.raise_for_status()
        return r.json().get("data", {})
    except RequestException:
        return {}


def fetch_solar_this_month(uid, token, timestamp, plant_id):
    data = get_monthly_generation(uid, token, timestamp, plant_id)
    for line in data.get("lines", []):
        if line.get("label") == "Generation (kWh)":
            xy = line.get("xy", [])
            if xy:
                return float(xy[-1]["y"])
    return None


# --------------------------------
# Month Finalization
# --------------------------------
def finalize_month(monthly_data, month, accumulator):
    final_kwh = accumulator.get_month_kwh()

    entry = monthly_data.setdefault(month, {})
    entry["pc_kwh_used"] = round(final_kwh, 4)

    # Remove transient fields
    entry.pop("current_watts", None)
    entry.pop("current_avg_watts_24h", None)

    # Rename solar field
    if "solar_this_month" in entry:
        entry["solar"] = entry.pop("solar_this_month")

    save_monthly_data(monthly_data)

    print(f"[Month Finalized] {month} → {final_kwh:.3f} kWh")


# --------------------------------
# Main
# --------------------------------
def main():
    uid, token, timestamp = sems_login(args["gw_account"], args["gw_password"])
    plant_id = args["gw_station_id"]

    print(f"[{datetime.now()}] Starting miners...")
    xmrig = subprocess.Popen(XMRIG_CMD)

    time.sleep(5)

    monthly_data = load_monthly_data()
    current_month = month_key()

    reconcile_old_months(monthly_data, current_month)
    save_monthly_data(monthly_data)

    accumulator = EnergyAccumulator()

    if current_month in monthly_data and "pc_kwh_used" in monthly_data[current_month]:
        existing_kwh = monthly_data[current_month]["pc_kwh_used"]
        accumulator.month_total_uj = int(existing_kwh * 1000 * 3600 * 1_000_000)
        print(f"[Resume] {current_month}: {existing_kwh:.3f} kWh")

    last_save = time.time()

    try:
        while True:
            loop_start = time.time()

            # 🔑 Month rollover FIRST
            new_month = month_key()
            if new_month != current_month:
                finalize_month(monthly_data, current_month, accumulator)
                accumulator.reset_month()
                current_month = new_month
                monthly_data[current_month] = {}

            # Sample energy
            current_watts = accumulator.sample_energy()

            # Periodic save
            if time.time() - last_save >= SAVE_INTERVAL:
                entry = monthly_data.setdefault(current_month, {})

                entry["pc_kwh_used"] = round(accumulator.get_month_kwh(), 4)
                entry["current_watts"] = round(current_watts, 2)
                entry["current_avg_watts_24h"] = round(accumulator.get_avg_power_24h(), 2)

                solar = fetch_solar_this_month(uid, token, timestamp, plant_id)
                if solar is not None:
                    entry["solar_this_month"] = round(solar, 3)

                save_monthly_data(monthly_data)
                last_save = time.time()

                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] "
                    f"{current_watts:.1f}W | "
                    f"24h {entry['current_avg_watts_24h']:.1f}W | "
                    f"Month {entry['pc_kwh_used']:.3f} kWh"
                )

            time.sleep(max(0, SAMPLE_INTERVAL - (time.time() - loop_start)))

    except KeyboardInterrupt:
        print("\nStopping miners...")
        xmrig.terminate()
        xmrig.wait()
        finalize_month(monthly_data, current_month, accumulator)


if __name__ == "__main__":
    main()
