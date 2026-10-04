#!/usr/bin/env python3
"""
wake-daemon.py — fleet wake-on-LAN sender for the always-on Pi.
Runs on the low-watt Pi (or any always-on LAN box). Single job: wake
fleet machines on Muse's request.

Pattern: same Drive-inbox architecture as muse-box. The Pi syncs
  fleet/wake/inbox/ via rclone every 30s.
  Muse drops wake-<host>.json {"host": "M1"} into the inbox.
  The daemon sends the WoL magic packet to that host's MAC on the LAN,
  writes wake-<host>.result.json, syncs back.
Visible by default: every request and result is a plain file in Drive.

Setup (on the Pi):
  1. Install: sudo apt install rclone wakeonlan python3
  2. rclone config  (Google Drive, name the remote "gdrive")
  3. mkdir -p ~/wake/inbox && edit ~/wake/macs.json (see below)
  4. Run: nohup python3 wake-daemon.py &
     (or install as a systemd service — see wake-daemon.service)

macs.json format:
  {"M1": "AA:BB:CC:DD:EE:FF", "M2": "11:22:33:44:55:66"}
  (populate from PIPR scans — pipr-scan.py records adapters/MACs)
"""
import json, os, socket, struct, subprocess, sys, time

HOME = os.path.expanduser("~")
WAKE_DIR = os.path.join(HOME, "wake")
INBOX = os.path.join(WAKE_DIR, "inbox")
MACS_FILE = os.path.join(WAKE_DIR, "macs.json")
REMOTE = "gdrive:YOUR_WAKE_INBOX_PATH"  # e.g. "gdrive:fleet/wake/inbox"
POLL_SEC = 30

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def sh(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0
    except Exception as e:
        log(f"cmd failed: {e}")
        return False

def send_magic(mac):
    mac = mac.replace(":", "").replace("-", "")
    if len(mac) != 12:
        raise ValueError(f"bad MAC: {mac}")
    data = b"\xff" * 6 + struct.pack("!12s", bytes.fromhex(mac)) * 16
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    # subnet-directed broadcast + limited broadcast, port 9
    for addr in ("192.168.1.255", "255.255.255.255"):
        try:
            s.sendto(data, (addr, 9))
        except Exception:
            pass
    s.close()

def load_macs():
    try:
        with open(MACS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def sync_down():
    return sh(["rclone", "sync", REMOTE, INBOX, "--exclude", "*.result.json"])

def sync_up():
    return sh(["rclone", "copy", INBOX, REMOTE])

def main():
    os.makedirs(INBOX, exist_ok=True)
    log("wake-daemon up. watching Drive wake inbox.")
    seen = set()
    while True:
        try:
            if not sync_down():
                log("rclone sync failed; retrying next poll")
                time.sleep(POLL_SEC)
                continue
            macs = load_macs()
            for fn in sorted(os.listdir(INBOX)):
                if not fn.startswith("wake-") or not fn.endswith(".json"):
                    continue
                if ".result" in fn or fn in seen:
                    continue
                seen.add(fn)
                host = fn[len("wake-"):-len(".json")].upper()
                mac = macs.get(host)
                result = {"host": host, "requested": fn,
                          "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
                if not mac:
                    result["ok"] = False
                    result["error"] = f"no MAC for {host} in macs.json"
                else:
                    try:
                        # send 3x, 2s apart — cheap insurance
                        for _ in range(3):
                            send_magic(mac)
                            time.sleep(2)
                        result["ok"] = True
                        result["mac"] = mac
                    except Exception as e:
                        result["ok"] = False
                        result["error"] = str(e)
                rfn = fn.replace(".json", ".result.json")
                with open(os.path.join(INBOX, rfn), "w") as f:
                    json.dump(result, f, indent=2)
                log(f"{fn} -> ok={result['ok']}")
            sync_up()
        except Exception as e:
            log(f"loop error: {e}")
        time.sleep(POLL_SEC)

if __name__ == "__main__":
    main()
