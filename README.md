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
