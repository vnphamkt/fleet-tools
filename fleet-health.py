#!/usr/bin/env python3
"""
fleet-health.py — health monitor for Muse's fleet (muse-box machines).

Requirement (proposed 2026-10-04 — adjust to taste):
  No 15-minute SLA. Hourly check. Silence when healthy.
  Alert only when something is actually not working:

  1. CRASH LOOP: box.log shows 3+ agent restarts in 15 min.
  2. BOX STUCK: heartbeat fresh (<10 min) but an inbox task sits
     unpicked for >30 min.
  3. HUNG TASK: a task sits in running/ longer than 2x its timeout.
  4. WORK WAITING: inbox task pending while heartbeat stale >30 min
     (machine off with work queued) — info, not alarm.
  5. SUICIDE TASK: an inbox/running task whose command kills muse-box
     processes (the 2026-10-04 M2 incident) — quarantine + alarm.

  A dark machine with an empty inbox is FINE — the fleet is off by
  default (power costs). Silence is not failure.

Usage: python3 fleet-health.py [--json]
Exit 0 = healthy (no output unless --json). Exit 1 = problems found.
"""
import json, os, re, subprocess, sys
from datetime import datetime, timezone

FLEET = None
_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fleet-config.json")
_EXAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fleet-config.example.json")

def load_fleet():
    global FLEET
    if FLEET is not None:
        return FLEET
    for path in (_CONFIG, _EXAMPLE):
        try:
            with open(path) as f:
                FLEET = json.load(f)
                return FLEET
        except Exception:
            continue
    # fallback: empty — user supplies fleet-config.json
    # (see fleet-config.example.json)
    FLEET = {}
    return FLEET

def gws(args):
    try:
        r = subprocess.run(["hatch_gws_cli"] + args, capture_output=True,
                           text=True, timeout=60)
        return json.loads(r.stdout) if r.stdout.strip() else {}
    except Exception:
        return {}

def list_files(parent):
    d = gws(["drive", "files", "list", "--params",
             json.dumps({"q": f"'{parent}' in parents and trashed=false",
                         "fields": "files(id,name,mimeType,modifiedTime)"})])
    return d.get("files", [])

def download(file_id, dest):
    r = subprocess.run(
        ["hatch_gws_cli", "drive", "files", "get", "--params",
         json.dumps({"fileId": file_id, "alt": "media"}),
         "--output", dest],
        capture_output=True, text=True, timeout=60)
    return os.path.exists(dest) and os.path.getsize(dest) > 0

def find_kids(folder_id):
    kids = {}
    for f in list_files(folder_id):
        n = f["name"].lower()
        if "inbox" in n: kids["inbox"] = f["id"]
        elif "outbox" in n: kids["outbox"] = f["id"]
        elif "running" in n: kids["running"] = f["id"]
        elif n == "heartbeat.json": kids["heartbeat"] = f["id"]
        elif n == "box.log": kids["boxlog"] = f["id"]
    return kids

def file_json(file_id):
    d = gws(["drive", "files", "get", "--params",
             json.dumps({"fileId": file_id, "alt": "media"})])
    return d if isinstance(d, dict) else {}

def now():
    return datetime.now(timezone.utc)

def age_sec(iso):
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (now() - t).total_seconds()
    except Exception:
        return None

SUICIDE_PAT = re.compile(r"stop-process.*muse-box|muse-box.*stop-process", re.I)

def check_machine(tag, cfg, problems, infos, alive):
    kids = find_kids(cfg["folder"])
    if not kids:
        problems.append(f"{tag}: fleet folder unreachable")
        alive[tag] = "unknown"
        return

    # heartbeat
    hb_age = None
    hb_alive = False
    if kids.get("heartbeat"):
        hb = file_json(kids["heartbeat"])
        hb_age = age_sec(hb.get("time", ""))
        hb_alive = hb.get("status") == "alive" and hb_age is not None and hb_age < 600
    alive[tag] = ("alive" if hb_alive
                  else f"off ({int(hb_age/60)}m stale)" if hb_age else "unknown")

    # inbox
    inbox_tasks = []
    if kids.get("inbox"):
        for f in list_files(kids["inbox"]):
            if f["name"].endswith(".task.json"):
                inbox_tasks.append(f)
    # running
    running_tasks = []
    if kids.get("running"):
        for f in list_files(kids["running"]):
            running_tasks.append(f)

    # 5. suicide tasks
    for f in inbox_tasks + running_tasks:
        body = file_json(f["id"])
        cmd = str(body.get("cmd", ""))
        if SUICIDE_PAT.search(cmd):
            problems.append(
                f"{tag}: SUICIDE TASK {f['name']} kills muse-box — quarantine it")

def check_crash_loop(dest, problems, tag):
    """Gap-based detection: 3+ restarts with <5min gaps = loop.
    Hourly lifeline restarts (3600s gaps) are normal, not a loop."""
    try:
        with open(dest, errors="replace") as fh:
            tail = fh.read()[-30000:]
    except Exception:
        return
    times = re.findall(r"\[(\d{2}):(\d{2}):(\d{2})\] Muse Box v[\d.]+ .*watching", tail)
    if len(times) < 3:
        return
    secs = [int(h) * 3600 + int(m) * 60 + int(s) for h, m, s in times]
    # handle midnight wrap
    gaps = []
    for a, b in zip(secs, secs[1:]):
        g = b - a
        if g < 0:
            g += 86400
        gaps.append(g)
    tight = 0
    for g in gaps[-8:]:
        if g < 300:
            tight += 1
        else:
            tight = 0
        if tight >= 2:  # 3 restarts, 2 tight gaps
            problems.append(
                f"{tag}: CRASH LOOP — 3+ restarts <5min apart in box.log")
            return

    # 1. crash loop (gap-based: 3+ restarts <5min apart)
    if kids.get("boxlog"):
        dest = f"/tmp/health-{tag}-boxlog.txt"
        if download(kids["boxlog"], dest):
            check_crash_loop(dest, problems, tag)

    # 2. box stuck / 4. work waiting
    for f in inbox_tasks:
        a = age_sec(f.get("modifiedTime", ""))
        if a is None:
            continue
        if hb_alive and a > 1800:
            problems.append(
                f"{tag}: BOX STUCK — {f['name']} in inbox {int(a/60)}m, heartbeat fresh")
        elif not hb_alive and hb_age is not None and hb_age > 1800 and a > 1800:
            infos.append(
                f"{tag}: work waiting — {f['name']} queued, machine off "
                f"(heartbeat {int(hb_age/60)}m stale)")

    # 3. hung tasks
    for f in running_tasks:
        a = age_sec(f.get("modifiedTime", ""))
        body = file_json(f["id"])
        timeout = body.get("timeout", 300)
        try:
            timeout = int(timeout)
        except Exception:
            timeout = 300
        if a is not None and a > 2 * timeout and timeout > 0:
            problems.append(
                f"{tag}: HUNG TASK — {f['name']} in running/ {int(a/60)}m "
                f"(2x timeout {timeout}s)")

    if not problems and not infos:
        pass  # healthy: silence

def main():
    problems, infos, alive = [], [], {}
    fleet = load_fleet()
    for tag, cfg in fleet.items():
        try:
            check_machine(tag, cfg, problems, infos, alive)
        except Exception as e:
            problems.append(f"{tag}: monitor error: {e}")
            alive[tag] = "unknown"
    out = {"problems": problems, "info": infos, "alive": alive,
           "checked_at": now().isoformat()}
    if "--json" in sys.argv or "--status" in sys.argv or problems or infos:
        print(json.dumps(out, indent=2))
    sys.exit(1 if problems else 0)

if __name__ == "__main__":
    main()
