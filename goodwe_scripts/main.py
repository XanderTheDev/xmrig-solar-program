import json
import subprocess
from datetime import datetime
from pathlib import Path
import time
from collections import deque

import requests

from config import args


# ================================================================
# Configuration
# ================================================================

# Local JSON file where monthly solar/PC energy data is stored.
MONTHLY_FILE = Path("monthly_stats.json")

# Command used to launch XMRig (CPU/GPU miner).
# NOTE: Replace YOUR_USER with your actual username.
XMRIG_CMD = [
    "xmrig", "-c", "/home/YOUR_USER/.config/xmrig/config.json",
    "--api-worker-id=worker1", "--http-port=42000"
]


# ================================================================
# Power Measurement (Linux sysfs)
# ================================================================
# These sysfs paths expose real-time power and energy usage reported
# by the CPU (RAPL) and GPU (hwmon). All values are in micro-units.
CPU_ENERGY_PATH = "/sys/class/powercap/intel-rapl:0/energy_uj"
GPU1_POWER_PATH = "/sys/class/drm/card1/device/hwmon/hwmon4/power1_average"


def read_int(path):
    """Read a numeric (int) value from the given sysfs file."""
    with open(path, "r") as f:
        return int(f.read().strip())


def read_cpu_power_watts():
    """
    Estimate average CPU power over 1 second using RAPL energy readings.
    Returns watts = joules/second.
    """
    e1 = read_int(CPU_ENERGY_PATH)
    time.sleep(1)
    e2 = read_int(CPU_ENERGY_PATH)
    return (e2 - e1) / 1_000_000  # μJ → J → W


def read_gpu_power_watts(path):
    """
    Read instantaneous GPU power from hwmon.
    Missing files (e.g. no GPU) return 0W to avoid breaking the loop.
    """
    try:
        return read_int(path) / 1_000_000  # μW → W
    except FileNotFoundError:
        return 0.0


def read_total_system_power():
    """Return combined CPU + GPU power in watts."""
    cpu = read_cpu_power_watts()
    gpu1 = read_gpu_power_watts(GPU1_POWER_PATH)
    return cpu + gpu1


# ================================================================
# Monthly Stats Helpers
# ================================================================

def load_monthly_data():
    """Load monthly_stats.json or return empty structure."""
    if MONTHLY_FILE.exists():
        with open(MONTHLY_FILE, "r") as f:
            return json.load(f)
    return {}


def save_monthly_data(data):
    """Write monthly energy data to JSON file."""
    with open(MONTHLY_FILE, "w") as f:
        json.dump(data, f, indent=4)


def month_key(dt=None):
    """Return 'YYYY-MM' string for the given date (or now)."""
    dt = dt or datetime.now()
    return f"{dt.year}-{dt.month:02d}"


# ================================================================
# SEMSPortal API Helpers
# (Used to fetch official solar production data)
# ================================================================

def sems_login(email, password):
    """
    Authenticate with SEMSPortal and return uid/token/timestamp.
    This token is required for all subsequent API requests.
    """
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
    """
    Fetch 12 months of solar generation data ending at date_str.
    Returns raw SEMSPortal JSON.
    """
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
        "range": "3",        # "3" = last 12 months
        "chartIndexId": "3",
        "isDetailFull": ""
    }

    r = requests.post(url, headers=headers, json=payload)
    r.raise_for_status()
    return r.json()["data"]


def ensure_previous_11_months_saved(uid, token, timestamp, plant_id):
    """
    At startup, preload the last 11 months of solar data
    into monthly_stats.json if not already present.

    This creates historical data for the dashboard without
    waiting a full year of local measurements.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    data = get_monthly_generation(uid, token, timestamp, plant_id, today)
    monthly_data = load_monthly_data()

    # Find "Generation (kWh)" dataset in the API response.
    for line in data.get("lines", []):
        if line.get("label") == "Generation (kWh)":
            xy = line.get("xy", [])
            if not xy:
                return

            # Every month except the last (current month)
            for entry in xy[:-1]:
                month = entry["x"]
                solar_val = float(entry["y"])

                if month not in monthly_data:
                    monthly_data[month] = {
                        "solar": round(solar_val, 3)
                    }
                    print(f"[Startup] Saved solar {month} = {solar_val} kWh")

    save_monthly_data(monthly_data)


def fetch_solar_this_month(uid, token, timestamp, plant_id):
    """
    Fetch solar generation for the *current* month.
    (The last entry in SEMSPortal’s 12-month response.)
    """
    today = datetime.now().strftime("%Y-%m-%d")
    data = get_monthly_generation(uid, token, timestamp, plant_id, today)

    for line in data.get("lines", []):
        if line.get("label") == "Generation (kWh)":
            xy = line.get("xy", [])
            if xy:
                return float(xy[-1]["y"])

    return None


# ================================================================
# Main processing loop: mining + energy tracking
# ================================================================

def main():
    # --- Authenticate with SEMS ---
    uid, token, timestamp = sems_login(
        args["gw_account"],
        args["gw_password"]
    )
    plant_id = args["gw_station_id"]

    # Preload solar history for dashboard completeness.
    ensure_previous_11_months_saved(uid, token, timestamp, plant_id)

    print(f"[{datetime.now()}] Starting miners...")
    xmrig_process = subprocess.Popen(XMRIG_CMD)

    # --- Power tracking state ---
    monthly_data = load_monthly_data()
    current_month = month_key()

    # Convert stored kWh → Wh internal representation
    pc_month_total_Wh = monthly_data.get(current_month, {}).get("pc_kwh_used", 0) * 1000

    # Store 24 hours of minute-by-minute power measurements
    samples_24h = deque(maxlen=1440)

    try:
        while True:
            loop_start = time.time()

            # -------------------
            # Read current power
            # -------------------
            watts = read_total_system_power()
            samples_24h.append(watts)

            # Accumulate monthly Wh (per 1-minute interval)
            pc_month_total_Wh += watts / 60.0
            pc_month_total_kwh = pc_month_total_Wh / 1000.0

            # Rolling 24-hour average consumption
            avg_24h = sum(samples_24h) / len(samples_24h) if samples_24h else 0.0

            # Ensure month exists
            monthly_data.setdefault(current_month, {})

            # Update live metrics
            monthly_data[current_month]["pc_kwh_used"] = round(pc_month_total_kwh, 4)
            monthly_data[current_month]["current_avg_watts_24h"] = round(avg_24h, 2)

            # Fetch current month's solar production
            solar_this_month = fetch_solar_this_month(uid, token, timestamp, plant_id)
            if solar_this_month is not None:
                monthly_data[current_month]["solar_this_month"] = round(solar_this_month, 3)

            save_monthly_data(monthly_data)

            # -------------------
            # Monthly rollover
            # -------------------
            new_month = month_key()
            if new_month != current_month:
                print(f"[Month Change] Finalizing {current_month} PC usage = {pc_month_total_kwh:.3f} kWh")

                current_month = new_month
                pc_month_total_Wh = 0.0
                samples_24h.clear()

            # Wait until exactly 1 minute has passed
            elapsed = time.time() - loop_start
            time.sleep(max(0, 60 - elapsed))

    except KeyboardInterrupt:
        # Clean miner shutdown
        print("Stopping miners...")
        xmrig_process.terminate()
        xmrig_process.wait()
        print("Miners stopped.")


if __name__ == "__main__":
    main()
