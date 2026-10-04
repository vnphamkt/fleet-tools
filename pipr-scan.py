#!/usr/bin/env python3
"""
pipr-scan.py — Plant In Place Record scanner for Muse's fleet.
Stdlib only, no installs. Windows + Linux.

Per the PIPR doctrine: site-specific, not equipment-specific. This records
everything about THIS machine IN THIS PLACE that relates to Muse's ability
to work on it: hardware, network topology, reachability, and readiness.

Sections: identity, hardware, network, muse_readiness, verdict.
Records: saved timestamped to ~/workspace/records/pipr/ (or ./pipr-records/),
          diffed against the previous record for the same host ("checked").

Usage: python3 pipr-scan.py [--json] [--record-dir DIR]
"""
import json, os, platform, re, subprocess, sys, socket
from datetime import datetime, timezone

RECORD_DIR = os.path.expanduser("~/workspace/records/pipr")

def sh(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout,
                           shell=isinstance(cmd, str))
        return r.stdout.strip()
    except Exception:
        return ""

def ps(cmd, timeout=30):
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""

def parse_ps_list(text):
    d = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            d[k.strip()] = v.strip()
    return d

def ps_blocks(text):
    return [parse_ps_list(b) for b in text.split("\n\n") if b.strip()]

# ---------------------------------------------------------------- hardware
def hw_windows():
    hw = {}
    c = parse_ps_list(ps("(Get-CimInstance Win32_Processor|Select -First 1)|Format-List Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed,L2CacheSize,L3CacheSize"))
    hw["cpu"] = {"model": c.get("Name"), "physical": c.get("NumberOfCores"),
                 "logical": c.get("NumberOfLogicalProcessors"),
                 "max_mhz": c.get("MaxClockSpeed"),
                 "l2_kb": c.get("L2CacheSize"), "l3_kb": c.get("L3CacheSize")}
    m = parse_ps_list(ps("(Get-CimInstance Win32_ComputerSystem)|Format-List TotalPhysicalMemory"))
    try:
        hw["ram_gb"] = round(int(m.get("TotalPhysicalMemory", 0)) / 1e9, 1)
    except Exception:
        hw["ram_gb"] = None
    sticks = ps("(Get-CimInstance Win32_PhysicalMemory)|ForEach-Object{\"{0}GB@{1}MHz\" -f ([math]::Round($_.Capacity/1GB,1)),$_.Speed}")
    hw["ram_sticks"] = [s for s in sticks.splitlines() if s.strip()] or None
    gpus = []
    for g in ps_blocks(ps("(Get-CimInstance Win32_VideoController)|Format-List Name,AdapterRAM,DriverVersion")):
        if g.get("Name"):
            try: vram = round(int(g.get("AdapterRAM", 0)) / 1e9, 1)
            except Exception: vram = None
            gpus.append({"name": g["Name"], "vram_gb": vram, "driver": g.get("DriverVersion")})
    hw["gpu"] = gpus or None
    disks = []
    for d in ps_blocks(ps("(Get-CimInstance Win32_DiskDrive)|Format-List Model,Size,MediaType,InterfaceType")):
        if d.get("Model"):
            try: size = round(int(d.get("Size", 0)) / 1e9, 1)
            except Exception: size = None
            disks.append({"model": d["Model"], "size_gb": size,
                          "media": d.get("MediaType"), "iface": d.get("InterfaceType")})
    hw["disks"] = disks or None
    b = parse_ps_list(ps("(Get-CimInstance Win32_BaseBoard)|Format-List Manufacturer,Product"))
    hw["board"] = {"maker": b.get("Manufacturer"), "model": b.get("Product")}
    return hw

def hw_linux():
    hw = {}
    cpu = {}
    try:
        with open("/proc/cpuinfo") as f: txt = f.read()
        m = re.search(r"model name\s*:\s*(.+)", txt)
        cpu["model"] = m.group(1).strip() if m else platform.processor()
        cpu["logical"] = os.cpu_count()
        ids = set(re.findall(r"physical id\s*:\s*(\d+)", txt))
        cpu["sockets"] = len(ids) or None
        fl = re.search(r"flags\s*:\s*(.+)", txt)
        flags = set(fl.group(1).split()) if fl else set()
        cpu["instructions"] = [i for i in
            ["avx","avx2","avx512f","fma","aes","sse4_2","sha_ni","bmi1","bmi2"]
            if i in flags] or None
    except Exception: pass
    hw["cpu"] = cpu or None
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    hw["ram_gb"] = round(int(line.split()[1]) / 1e6, 1)
    except Exception: pass
    gpus = [{"name": re.sub(r"^[0-9a-f:. ]+", "", l, flags=re.I).strip()}
            for l in sh(["lspci"]).splitlines()
            if re.search(r"vga|3d|display", l, re.I)]
    nv = sh(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
             "--format=csv,noheader"])
    if nv:
        gpus = [{"name": p[0].strip(), "vram": p[1].strip(), "driver": p[2].strip()}
                for p in (l.split(",") for l in nv.splitlines()) if len(p) >= 3]
    hw["gpu"] = gpus or None
    hw["disks"] = None
    hw["board"] = None
    return hw

# ---------------------------------------------------------------- network
def net_windows():
    n = {}
    ips = ps("(Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike '169.254*' -and $_.PrefixOrigin -ne 'WellKnown'}) | ForEach-Object {\"{0}/{1} ({2})\" -f $_.IPAddress,$_.PrefixLength,$_.InterfaceAlias}")
    n["internal_ips"] = [s for s in ips.splitlines() if s.strip()] or None
    gw = ps("(Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Select-Object -First 1).NextHop")
    n["gateway"] = gw or None
    dns = ps("(Get-DnsClientServerAddress -AddressFamily IPv4 | Where-Object {$_.ServerAddresses}) | ForEach-Object {\"{0}: {1}\" -f $_.InterfaceAlias,($_.ServerAddresses -join ',')}")
    n["dns"] = [s for s in dns.splitlines() if s.strip()] or None
    ad = ps("(Get-NetAdapter | Where-Object {$_.Status -eq 'Up'}) | ForEach-Object {\"{0} [{1}] {2} MAC={3}\" -f $_.Name,$_.MediaType,$_.LinkSpeed,$_.MacAddress}")
    n["adapters_up"] = [s for s in ad.splitlines() if s.strip()] or None
    ts = ps("(tailscale ip -4) 2>$null")
    n["tailscale_ip"] = ts.strip() or None
    ext = ps("(Invoke-RestMethod -Uri 'https://ifconfig.me' -TimeoutSec 8) 2>$null")
    n["external_ip"] = ext.strip() or None
    return n

def net_linux():
    n = {}
    n["internal_ips"] = sh(["hostname", "-I"]).split() or None
    gw = ""
    for line in sh(["ip", "route", "show", "default"]).splitlines():
        m = re.search(r"via ([\d.]+)", line)
        if m: gw = m.group(1); break
    n["gateway"] = gw or None
    dns = []
    try:
        with open("/etc/resolv.conf") as f:
            dns = [l.split()[1] for l in f if l.startswith("nameserver")]
    except Exception: pass
    n["dns"] = dns or None
    ad = []
    for line in sh(["ip", "-o", "link", "show"]).splitlines():
        m = re.match(r"\d+: (\w+):.*state (\w+).*link/\w+ ([\da-f:]+)", line)
        if m and m.group(2) == "UP":
            ad.append(f"{m.group(1)} MAC={m.group(3)}")
    n["adapters_up"] = ad or None
    ts = sh(["tailscale", "ip", "-4"])
    n["tailscale_ip"] = ts or None
    ext = sh("curl -s --max-time 8 https://ifconfig.me")
    n["external_ip"] = ext or None
    return n

# ------------------------------------------------------- muse readiness
def ready_windows():
    r = {}
    r["user"] = os.environ.get("USERNAME")
    r["hostname"] = socket.gethostname()
    box = ps("(Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -like '*muse-box*'} | Measure-Object).Count")
    r["muse_box_running"] = (box.strip() == "1") if box.strip().isdigit() else None
    gdrive = ps("(Get-Process 'GoogleDriveFS' -ErrorAction SilentlyContinue | Measure-Object).Count")
    r["drive_sync_running"] = (gdrive.strip() >= "1") if gdrive.strip().isdigit() else False
    boot = parse_ps_list(ps("(Get-CimInstance Win32_OperatingSystem)|Format-List LastBootUpTime"))
    r["last_boot"] = boot.get("LastBootUpTime")
    vols = []
    for v in ps_blocks(ps("(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3')|Format-List DeviceID,FreeSpace,Size")):
        if v.get("DeviceID"):
            try:
                vols.append({"drive": v["DeviceID"],
                             "free_gb": round(int(v.get("FreeSpace", 0)) / 1e9, 1),
                             "size_gb": round(int(v.get("Size", 0)) / 1e9, 1)})
            except Exception: pass
    r["volumes"] = vols or None
    return r

def ready_linux():
    r = {}
    r["user"] = os.environ.get("USER")
    r["hostname"] = socket.gethostname()
    r["muse_box_running"] = None
    r["drive_sync_running"] = None
    r["uptime"] = sh(["uptime", "-p"]) or None
    vols = []
    for line in sh(["df", "-h", "--output=source,size,avail,target",
                    "-x", "tmpfs", "-x", "devtmpfs"]).splitlines()[1:]:
        p = line.split()
        if len(p) >= 4:
            vols.append({"drive": p[0], "size": p[1], "free": p[2], "mount": p[3]})
    r["volumes"] = vols or None
    r["last_boot"] = None
    return r

# ---------------------------------------------------------------- verdict
def verdict(rec):
    hw, net, rdy = rec["hardware"], rec["network"], rec["muse_readiness"]
    v = []
    cpu = hw.get("cpu") or {}
    v.append(f"CPU: {cpu.get('model') or '?'} ({cpu.get('logical')} threads)")
    v.append(f"RAM: {hw.get('ram_gb')}GB")
    gpu = hw.get("gpu")
    v.append(f"GPU: {'; '.join(g.get('name','?') for g in gpu) if gpu else 'none'}")
    v.append(f"Internal: {', '.join(net.get('internal_ips') or ['?'])}")
    v.append(f"External: {net.get('external_ip') or '?'} | Tailscale: {net.get('tailscale_ip') or 'no'}")
    v.append(f"Box running: {rdy.get('muse_box_running')} | Drive sync: {rdy.get('drive_sync_running')}")
    roles = []
    total_ram = hw.get("ram_gb") or 0
    has_cuda = bool(gpu) and any("nvidia" in (g.get("name") or "").lower() for g in gpu)
    if has_cuda:
        roles.append("local LLM inference (Ollama)")
    else:
        roles.append("NO local LLM — CPU inference only / cloud APIs")
    roles.append("cron, scripts, file processing, research orchestration"
                 if total_ram >= 8 else "light scripting only")
    if rdy.get("muse_box_running") is False:
        roles.append("WARNING: muse-box not running — cannot receive tasks")
    if rdy.get("drive_sync_running") is False:
        roles.append("WARNING: Drive sync down — inbox/outbox unreachable")
    v.append("CAN RUN: " + "; ".join(roles))
    return v

# ---------------------------------------------------------------- record
def save_record(rec, record_dir):
    os.makedirs(record_dir, exist_ok=True)
    host = rec["muse_readiness"].get("hostname") or "unknown"
    ts = rec["scanned_at"].replace(":", "").replace("-", "")
    path = os.path.join(record_dir, f"{host}-{ts}.json")
    with open(path, "w") as f:
        json.dump(rec, f, indent=2)
    latest = os.path.join(record_dir, f"{host}-latest.json")
    diff = None
    if os.path.exists(latest):
        with open(latest) as f:
            prev = json.load(f)
        diff = diff_records(prev, rec)
    with open(latest, "w") as f:
        json.dump(rec, f, indent=2)
    return path, diff

def diff_records(prev, cur, path=""):
    """Shallow diff of key fields; returns list of changed 'path: old -> new'."""
    changes = []
    watch = ["hardware.cpu.model", "hardware.cpu.logical", "hardware.ram_gb",
             "hardware.gpu", "network.internal_ips", "network.external_ip",
             "network.tailscale_ip", "muse_readiness.muse_box_running",
             "muse_readiness.drive_sync_running"]
    def get(d, p):
        for k in p.split("."):
            d = (d or {}).get(k)
        return d
    for w in watch:
        a, b = get(prev, w), get(cur, w)
        if json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True):
            changes.append(f"{w}: {a} -> {b}")
    return changes or None

def main():
    is_win = platform.system() == "Windows"
    rec = {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.system(),
        "identity": {
            "hostname": socket.gethostname(),
            "os": (parse_ps_list(ps("(Get-CimInstance Win32_OperatingSystem)|Format-List Caption,Version,BuildNumber"))
                   if is_win else {"name": platform.platform()}),
        },
        "hardware": hw_windows() if is_win else hw_linux(),
        "network": net_windows() if is_win else net_linux(),
        "muse_readiness": ready_windows() if is_win else ready_linux(),
    }
    rec["verdict"] = verdict(rec)

    args = sys.argv[1:]
    record_dir = RECORD_DIR
    if "--record-dir" in args:
        record_dir = args[args.index("--record-dir") + 1]
    path, diff = save_record(rec, record_dir)
    rec["record_path"] = path
    rec["changes_since_last"] = diff
    print(json.dumps(rec, indent=2))

if __name__ == "__main__":
    main()
