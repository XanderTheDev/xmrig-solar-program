# xmrig-solar-program
XMRig script that keeps track of CPU power consumption and solar energy generation (from a goodwe inverter) and shows it in a nice dashboard.

---

## How it looks

<img width="1688" height="1304" alt="Dashboard Screenshot" src="https://github.com/user-attachments/assets/856c0611-6d88-4116-978b-47a7ec63a550" />

---

## Features

- Shows your **total generated energy** from your solar panel(s) (since max 11 months before you started the main.py script).
- Shows your **average solar energy** per month.
- Shows your **total CPU/PC usage**.
- Shows what your **CPU/PC uses on average** in kWh.
- Shows how many **watts your CPU is using** on average the last 24hrs.
- Shows how many **kWh your solar panels have generated** this month.
- Shows how much **electricity costs you need to pay** for last month.
- A monthly comparison card.

---

## Requirements

- Python 3.12+
- Goodwe inverter (I do not know which ones are supported by this, but I think most)
- XMRig
- XMRig config file

---

## Setup Tutorial

Follow these steps to get the dashboard running:

### 1. Clone this repository
```bash
git clone https://github.com/XanderTheDev/xmrig-solar-program.git
cd xmrig-solar-program
```

### 2. Install needed dependencies

#### Mutable linux distros

```bash
pip install requests
```

#### Immutable distro NixOS 
```bash
nix-shell  # You need to be in the repository to do this
```

### 3. Register/login on GOODWE SEMS PORTAL

Go to [SEMS Portal](https://www.semsportal.com/home/login) and register or login.

If you haven't setup a goodwe inverter, you need to do that obviously.

### 4. Copy station ID into config.py

When you are in the SEMS Portal look at your url. You will see something like ```https://www.semsportal.com/PowerStation/PowerStatusSnMin/5ed23680-1929-5f82-bdbf-f748ff54f43b```,
you need to copy the ```5ed23680-1929-5f82-bdbf-f748ff54f43b``` and put it in where the placeholder is for ```'gw_station_id'``` in goodwe_scripts/config.py.

### 5. Add all the other info into config.py

Put in all your info with how you logged in into the SEMS Portal in goodwe_scripts/config.py. So like:
- ```'gw_account'``` is your email for the SEMS Portal
- ```'gw_password'``` is your password for the SEMS Portal
- ```'city'``` is the city of your GoodWE inverter

### 6. Copy your XMRig to the correct location and change location in main.py

Copy your XMRig config.json and move it to ~/.config/xmrig/config.json if not already. Then in goodwe_scripts/main.py
change YOUR_USER to your username in the XMRIG_CMD variable.

### 7. Change kWh price in index.html

change ```const costPerKWh = 0.22; // €/kWh``` to what your actual price is for 1 kWh.

### 8. Run main.py

Go to the goodwe_scripts folder and run:
```bash
sudo python main.py
```

```sudo``` is recommended, because otherwise MSR does not work, but if you do not care about that, it still keeps working, but just without MSR.

### 8. Run run_server.py

In another terminal go to back to the goodwe_scripts folder and run:
```bash
python run_server.py

# Or alternatively you can also just make it an executable and run it:
chmod +x run_server.py
./run_server.py
```

### 9. You're done!

If everything went well you can now access your [Dashboard](127.0.0.1:8000/index.html)
