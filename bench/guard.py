"""Chief start gate: load<20 and (swap<8GB or swap<10GB with free memory>45%).
Hard pause: load>30, swap>11GB or free<30%; GPU admission is owned elsewhere.
The start gate already rejects the load/swap hard-pause region.
"""
import os
import subprocess


def parse_free_percent(text):
    # Last line of memory_pressure: "System-wide memory free percentage: 54%"
    return int(text.strip().splitlines()[-1].rsplit(" ", 1)[1].rstrip("%"))


def parse_swap_mb(text):
    # sysctl -n vm.swapusage: "total = 7168.00M  used = 6147.81M  free = 1020.19M  (encrypted)"
    return float(text.split()[5].rstrip("M"))


# shortcut: only these known heavy runners are detected besides load; switch to a host lock once one exists.
# Matches only a Python process running one of them, not an editor that has the file open.
HEAVY = r"python[^ ]* .*(round2\.py|eval/harness2/run\.py|pilot\.py --pack|harnesses[./]_surface|bench/seam\.py|app-worker\.py)"


class HostBusy(RuntimeError):
    pass


def problems(heavy=True, load=None, active=False):
    """heavy=False skips the heavy-runner check, for the seam's checks during its own trial."""
    load = os.getloadavg()[0] if load is None else load
    try:
        free = parse_free_percent(subprocess.run(["memory_pressure"], capture_output=True, text=True).stdout)
        swap = parse_swap_mb(subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True).stdout)
    except (IndexError, ValueError):
        return ["cannot read memory_pressure or vm.swapusage"]
    found = ["a heavy trial runner is already running"] if heavy and subprocess.run(["pgrep", "-f", HEAVY], capture_output=True).returncode == 0 else []
    if active:
        if load > 30:found.append(f"load {load:.1f} exceeds 30")
        if swap > 11264:found.append(f"swap {swap:.0f} MB exceeds 11 GB")
        if free < 30:found.append(f"free memory {free}% is under 30%")
        return found
    if load >= 20:
        found.append(f"load {load:.1f} is not under 20")
    if free < 30:
        found.append(f"free memory {free}% is under the 30% pause floor")
    if not (swap < 8192 or (swap < 10240 and free > 45)):
        found.append(f"swap {swap:.0f} MB/free {free}%: needs swap<8 GB or swap<10 GB with free>45%")
    return found
