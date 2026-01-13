import json
import subprocess
from datetime import datetime
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
RAPL_MAX_UJ = 2**32
XMRIG_CMD = [
    "xmrig", "-c", "/home/xander/.config/xmrig/config.json",
    "--api-worker-id=worker1", "--http-port=42000"
]
CPU_ENERGY_PATH = "/sys/class/powercap/intel-rapl:0/energy_uj"
SAMPLE_INTERVAL = 2.0  # Sample every 2 seconds for accurate energy tracking
SAVE_INTERVAL = 60.0   # Save to disk every 60 seconds

def read_int(path):
    with open(path, "r") as f:
        return int(f.read().strip())

class EnergyAccumulator:
    """
    Properly tracks energy consumption by reading RAPL counter deltas.
    The RAPL counter measures total energy consumed - we just need to
    accumulate the deltas over time.
    """
    def __init__(self):
        self.last_energy_uj = read_int(CPU_ENERGY_PATH)
        self.last_read_time = time.time()
        
        # Track total energy consumed in current month
        self.month_total_uj = 0
        
        # Track energy for rolling 24h average (store energy per sample)
        self.samples_24h = deque(maxlen=int(86400 / SAMPLE_INTERVAL))  # 24h worth of samples
        
    def sample_energy(self):
        """
        Read the RAPL counter and accumulate energy consumed since last read.
        Returns the instantaneous power in watts for display purposes.
        """
        now = time.time()
        current_energy_uj = read_int(CPU_ENERGY_PATH)
        
        # Handle counter wrap with modular arithmetic
        delta_energy_uj = (current_energy_uj - self.last_energy_uj) % RAPL_MAX_UJ
        delta_time_s = now - self.last_read_time
        
        # Update state
        self.last_energy_uj = current_energy_uj
        self.last_read_time = now
        
        # Accumulate energy
        self.month_total_uj += delta_energy_uj
        self.samples_24h.append(delta_energy_uj)
        
        # Calculate instantaneous power for display
        if delta_time_s > 0:
            power_watts = delta_energy_uj / 1_000_000 / delta_time_s
        else:
            power_watts = 0.0
            
        return power_watts
    
    def get_month_kwh(self):
        """Get total energy consumed this month in kWh"""
        return self.month_total_uj / 1_000_000 / 3600 / 1000  # µJ → J → Wh → kWh
    
    def get_avg_power_24h(self):
        """Get average power over last 24 hours in watts"""
        if not self.samples_24h:
            return 0.0
        
        total_energy_uj = sum(self.samples_24h)
        total_time_s = len(self.samples_24h) * SAMPLE_INTERVAL
        
        if total_time_s > 0:
            return total_energy_uj / 1_000_000 / total_time_s  # µJ → W
        return 0.0
    
    def reset_month(self):
        """Reset monthly accumulation (call on month boundary)"""
        self.month_total_uj = 0
        self.samples_24h.clear()

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
    
    time.sleep(5)
    
    # Load existing monthly data
    monthly_data = load_monthly_data()
    current_month = month_key()
    
    # Initialize energy accumulator
    # If we have existing data for this month, we'll add to it
    accumulator = EnergyAccumulator()
    if current_month in monthly_data and "pc_kwh_used" in monthly_data[current_month]:
        # Add existing kWh to our accumulator
        existing_kwh = monthly_data[current_month]["pc_kwh_used"]
        accumulator.month_total_uj = int(existing_kwh * 1000 * 3600 * 1_000_000)  # kWh → µJ
        print(f"[Startup] Resuming month {current_month} with {existing_kwh:.3f} kWh already logged")
    
    last_save_time = time.time()
    
    try:
        while True:
            loop_start = time.time()
            
            # Sample energy consumption
            current_watts = accumulator.sample_energy()
            
            # Check if we should save to disk
            if time.time() - last_save_time >= SAVE_INTERVAL:
                pc_month_kwh = accumulator.get_month_kwh()
                avg_24h_watts = accumulator.get_avg_power_24h()
                
                # Update JSON
                if current_month not in monthly_data:
                    monthly_data[current_month] = {}
                    
                monthly_data[current_month]["pc_kwh_used"] = round(pc_month_kwh, 4)
                monthly_data[current_month]["current_avg_watts_24h"] = round(avg_24h_watts, 2)
                monthly_data[current_month]["current_watts"] = round(current_watts, 2)
                
                # Fetch solar data
                solar_this_month = fetch_solar_this_month(uid, token, timestamp, plant_id)
                if solar_this_month is not None:
                    monthly_data[current_month]["solar_this_month"] = round(solar_this_month, 3)
                
                save_monthly_data(monthly_data)
                last_save_time = time.time()
                
                print(f"[{datetime.now().strftime('%H:%M:%S')}] "
                      f"Current: {current_watts:.1f}W | "
                      f"24h Avg: {avg_24h_watts:.1f}W | "
                      f"Month Total: {pc_month_kwh:.3f} kWh")
            
            # Check for month rollover
            new_month = month_key()
            if new_month != current_month:
                final_kwh = accumulator.get_month_kwh()
                print(f"[Month Change] Finalizing {current_month} PC usage = {final_kwh:.3f} kWh")
                
                # Save final month data
                monthly_data[current_month]["pc_kwh_used"] = round(final_kwh, 4)
                save_monthly_data(monthly_data)
                
                # Reset for new month
                current_month = new_month
                accumulator.reset_month()
            
            # Sleep until next sample interval
            elapsed = time.time() - loop_start
            time.sleep(max(0, SAMPLE_INTERVAL - elapsed))
            
    except KeyboardInterrupt:
        print("\nStopping miners...")
        xmrig_process.terminate()
        xmrig_process.wait()
        
        # Save final state
        final_kwh = accumulator.get_month_kwh()
        monthly_data[current_month]["pc_kwh_used"] = round(final_kwh, 4)
        save_monthly_data(monthly_data)
        
        print(f"Miners stopped. Final month total: {final_kwh:.3f} kWh")

if __name__ == "__main__":
    main()
