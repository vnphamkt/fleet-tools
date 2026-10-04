# Wake Pi — bring-up checklist

One Raspberry Pi, always on (~5W), single job: wake any fleet machine on
Muse's request. Extends the muse-box Drive-inbox pattern — no new auth.

## What goes where

- The daemon: `~/workspace/muse-box/wake-daemon.py` (copy to the Pi)
- Drive folder: `7694 Muse Box / 7695 fleet / wake / inbox` (create once)
- MAC table: `~/wake/macs.json` on the Pi (from PIPR scans)

## Bring-up (hands-on)

1. Flash Raspberry Pi OS Lite to an SD card, boot the Pi on the LAN
   (ethernet preferred — WoL magic packets stay on wired LAN).
2. `sudo apt update && sudo apt install -y rclone python3`
3. `rclone config` — add Google Drive remote named `gdrive`
   (his Google account; same one the fleet Drive lives in).
4. Copy `wake-daemon.py` to `~/wake/`, create `~/wake/macs.json`:
   `{"M1": "AA:BB:CC:DD:EE:FF", "M2": "...", "M3": "..."}`
   MACs come from `pipr-scan.py` (adapters_up section) per machine.
5. Create the Drive folder `7695 fleet/wake/inbox`.
6. Enable WoL in each target machine's BIOS + network adapter settings
   (allow the NIC to wake the machine; disable "fast startup" on Windows —
   it breaks WoL).
7. Run: `nohup python3 ~/wake/wake-daemon.py &`
   (systemd unit later, once stable.)

## How Muse wakes a machine

1. Muse writes `wake-m1.json` (`{"host":"M1"}`) to the Drive wake inbox.
2. Pi syncs (≤30s), fires the magic packet ×3 to M1's MAC.
3. Pi writes `wake-m1.result.json`; Muse reads it.
4. Muse waits for the box heartbeat, then dispatches work.

## Power math (rough)

- Pi at 5W, 24/7, ~$0.30/kWh ≈ **$1/month**.
- One 1000W rig idling one day ≈ $2–7. The Pi pays for itself the first
  time it saves a single idle day.

## Status

- [ ] Pi on LAN (garage unit or one of the 2 incoming)
- [ ] rclone + daemon installed
- [ ] Drive wake/inbox folder created
- [ ] MACs collected (PIPR scans)
- [ ] WoL enabled in BIOS on M1/M2/M3 + powerhouses
- [ ] First wake test: M2 (he's at it; can verify boot)
