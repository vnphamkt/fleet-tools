#!/usr/bin/env python3
"""
hwscan.py — hardware assessment for any system Muse gets on.
Stdlib only, no installs. Windows + Linux.
Gathers the crucial info CPUID/HWiNFO would: CPU, cache, instruction sets,
RAM, GPU, storage, board, OS — then prints a verdict on what the box can run.

Usage: python3 hwscan.py [--json]
"""
import json, os, platform, re, subprocess, sys

def sh(cmd, timeout=15):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, shell=isinstance(cmd, str)).stdout.strip()
    except Exception:
        return ""

def ps(cmd, timeout=30):
    """Run a PowerShell snippet, return stdout."""
    full = ["powershell", "-NoProfile", "-Command", cmd]
    try:
        r = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""

def parse_ps_table(text):
    """Parse PowerShell Format-List 'Key : Value' output into dict."""
    d = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            d[k.strip()] = v.strip()
    return d

def windows():
    info = {"os": {}}
    o = parse_ps_table(ps("(Get-CimInstance Win32_OperatingSystem) | Format-List Caption, Version, BuildNumber, OSArchitecture"))
    info["os"] = {"name": o.get("Caption"), "version": o.get("Version"),
                  "build": o.get("BuildNumber"), "arch": o.get("OSArchitecture")}

    c = parse_ps_table(ps("(Get-CimInstance Win32_Processor | Select-Object -First 1) | Format-List Name, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed, L2CacheSize, L3CacheSize, Manufacturer"))
    info["cpu"] = {
        "model": c.get("Name"), "maker": c.get("Manufacturer"),
        "physical_cores": c.get("NumberOfCores"), "logical_cores": c.get("NumberOfLogicalProcessors"),
        "max_mhz": c.get("MaxClockSpeed"),
        "l2_kb": c.get("L2CacheSize"), "l3_kb": c.get("L3CacheSize"),
    }

    m = parse_ps_table(ps("(Get-CimInstance Win32_ComputerSystem) | Format-List TotalPhysicalMemory"))
    sticks = ps("(Get-CimInstance Win32_PhysicalMemory) | ForEach-Object { \"{0}GB @ {1}MHz {2}\" -f ([math]::Round($_.Capacity/1GB,1)), $_.Speed, $_.SMBIOSMemoryType }")
    try:
        total_gb = round(int(m.get("TotalPhysicalMemory", 0)) / 1e9, 1)
    except Exception:
        total_gb = None
    info["ram"] = {"total_gb": total_gb,
                   "sticks": [s for s in sticks.splitlines() if s.strip()] or None}

    gpus = []
    for block in ps("(Get-CimInstance Win32_VideoController) | Format-List Name, AdapterRAM, DriverVersion --").split("\n\n"):
        g = parse_ps_table(block)
        if g.get("Name"):
            try:
                vram = round(int(g.get("AdapterRAM", 0)) / 1e9, 1)
            except Exception:
                vram = None
            gpus.append({"name": g["Name"], "vram_gb": vram, "driver": g.get("DriverVersion")})
    info["gpu"] = gpus or None

    disks = []
    for block in ps("(Get-CimInstance Win32_DiskDrive) | Format-List Model, Size, MediaType, InterfaceType").split("\n\n"):
        d = parse_ps_table(block)
        if d.get("Model"):
            try:
                size = round(int(d.get("Size", 0)) / 1e9, 1)
            except Exception:
                size = None
            disks.append({"model": d["Model"], "size_gb": size,
                          "media": d.get("MediaType"), "interface": d.get("InterfaceType")})
    vols = []
    for block in ps("(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3') | Format-List DeviceID, Size, FreeSpace").split("\n\n"):
        v = parse_ps_table(block)
        if v.get("DeviceID"):
            try:
                vols.append({"drive": v["DeviceID"],
                             "size_gb": round(int(v.get("Size", 0)) / 1e9, 1),
                             "free_gb": round(int(v.get("FreeSpace", 0)) / 1e9, 1)})
            except Exception:
                pass
    info["storage"] = {"drives": disks or None, "volumes": vols or None}

    b = parse_ps_table(ps("(Get-CimInstance Win32_BaseBoard) | Format-List Manufacturer, Product"))
    bi = parse_ps_table(ps("(Get-CimInstance Win32_BIOS) | Format-List SMBIOSBIOSVersion"))
    info["board"] = {"maker": b.get("Manufacturer"), "model": b.get("Product"),
                     "bios": bi.get("SMBIOSBIOSVersion")}
    return info

def linux():
    info = {"os": {}}
    try:
        rel = {}
        with open("/etc/os-release") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    rel[k] = v.strip('"')
        info["os"] = {"name": rel.get("PRETTY_NAME"), "arch": platform.machine(),
                      "kernel": platform.release()}
    except Exception:
        info["os"] = {"name": sys.platform, "arch": platform.machine()}

    cpu = {}
    try:
        with open("/proc/cpuinfo") as f:
            txt = f.read()
        m = re.search(r"model name\s*:\s*(.+)", txt)
        cpu["model"] = m.group(1).strip() if m else None
        cpu["physical_cores"] = txt.count("physical id") and len(set(re.findall(r"physical id\s*:\s*(\d+)", txt))) or None
        cores = re.findall(r"cpu cores\s*:\s*(\d+)", txt)
        cpu["cores_per_socket"] = int(cores[0]) if cores else None
        cpu["logical_cores"] = os.cpu_count()
        mhz = re.search(r"cpu MHz\s*:\s*([\d.]+)", txt)
        cpu["mhz"] = float(mhz.group(1)) if mhz else None
        fl = re.search(r"flags\s*:\s*(.+)", txt)
        flags = set(fl.group(1).split()) if fl else set()
        interesting = ["avx", "avx2", "avx512f", "fma", "aes", "sse4_2", "sha_ni",
                       "bmi1", "bmi2", "popcnt", "rdrand"]
        cpu["instructions"] = [i for i in interesting if i in flags]
    except Exception:
        pass
    # cache from sysfs
    try:
        cache = {}
        import glob
        for p in glob.glob("/sys/devices/system/cpu/cpu0/cache/index*"):
            with open(os.path.join(p, "level")) as f: lvl = f.read().strip()
            with open(os.path.join(p, "type")) as f: typ = f.read().strip()
            with open(os.path.join(p, "size")) as f: sz = f.read().strip()
            cache.setdefault("L" + lvl + "_" + typ, sz)
        cpu["cache"] = cache or None
    except Exception:
        pass
    info["cpu"] = cpu or None

    ram = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    ram["total_gb"] = round(int(line.split()[1]) / 1e6, 1)
                elif line.startswith("MemAvailable"):
                    ram["avail_gb"] = round(int(line.split()[1]) / 1e6, 1)
        dm = sh("dmidecode -t memory 2>/dev/null | grep -E 'Size:|Speed:|Type:' | head -12")
        ram["sticks"] = dm.splitlines() or None
    except Exception:
        pass
    info["ram"] = ram or None

    gpus = []
    for line in sh(["lspci"]).splitlines():
        if re.search(r"vga|3d|display", line, re.I):
            gpus.append({"name": re.sub(r"^[0-9a-f:. ]+", "", line, flags=re.I).strip()})
    nv = sh(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
             "--format=csv,noheader"])
    if nv:
        gpus = [{"name": p[0].strip(), "vram": p[1].strip(), "driver": p[2].strip()}
                for p in (l.split(",") for l in nv.splitlines()) if len(p) >= 3]
    info["gpu"] = gpus or None

    vols = []
    for line in sh(["df", "-h", "--output=source,size,avail,target", "-x", "tmpfs",
                    "-x", "devtmpfs", "-x", "overlay"]).splitlines()[1:]:
        p = line.split()
        if len(p) >= 4:
            vols.append({"drive": p[0], "size": p[1], "free": p[2], "mount": p[3]})
    info["storage"] = {"volumes": vols or None}
    info["board"] = None
    return info

def verdict(info):
    """What can this box run for Muse's use?"""
    v = []
    cpu = info.get("cpu") or {}
    ram = info.get("ram") or {}
    gpu = info.get("gpu")
    try:
        total_ram = float(ram.get("total_gb") or 0)
    except Exception:
        total_ram = 0
    try:
        logical = int(cpu.get("logical_cores") or 0)
    except Exception:
        logical = 0

    has_cuda = bool(gpu) and any("nvidia" in (g.get("name") or "").lower() for g in gpu)
    vram = 0
    if has_cuda:
        for g in gpu:
            m = re.search(r"([\d.]+)\s*Gi?B", str(g.get("vram") or g.get("vram_gb") or ""))
            if m:
                vram = max(vram, float(m.group(1)))

    v.append(f"CPU: {cpu.get('model') or '?'} ({logical} threads)")
    v.append(f"RAM: {total_ram}GB total")
    v.append(f"GPU: {'; '.join(g.get('name','?') for g in gpu) if gpu else 'none'}")

    roles = []
    if has_cuda and vram >= 6:
        roles.append(f"local LLM inference (Ollama, ~{int(vram)}GB VRAM)")
    elif has_cuda:
        roles.append("light GPU work (small models only)")
    if total_ram >= 16 and logical >= 8:
        roles.append("build/test workloads, containers")
    elif total_ram >= 8:
        roles.append("scripting, cron jobs, file processing, research orchestration")
    else:
        roles.append("light scripting and orchestration only")
    if not has_cuda:
        roles.append("NO local LLM — use cloud APIs or fleet GPUs")
    v.append("CAN RUN: " + "; ".join(roles))
    return v

def main():
    info = windows() if platform.system() == "Windows" else linux()
    info["verdict"] = verdict(info)
    if "--json" in sys.argv:
        print(json.dumps(info, indent=2))
    else:
        print(json.dumps(info, indent=2))

if __name__ == "__main__":
    main()
