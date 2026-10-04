# Fleet Tools

Baby steps, in public. These are real tools built with an AI assistant
(Muse) to manage a small fleet of home machines — proof of work, not
claims of work.

## What this is

A home fleet: a few desktops with GPUs, a laptop, Raspberry Pis. The
problem is ordinary — know what each machine is, know whether it's
healthy, wake it when needed, keep the power bill down. The tools:

| Tool | What it does |
|---|---|
| `hwscan.py` | Hardware assessment for any machine it lands on — CPU, cache, instruction sets, RAM, GPU/VRAM, storage, board. Stdlib only, Windows + Linux. |
| `pipr-scan.py` | Plant In Place Record scanner — everything about *this* machine *in this place* that matters for remote work: hardware, network topology (internal/external IP, gateway, DNS, Tailscale), and readiness. Records are timestamped, saved, and diffed against the last scan. |
| `fleet-health.py` | Hourly health monitor — crash-loop detection, stuck tasks, hung tasks, suicide-task detection. Quiet when healthy. |
| `wake-daemon.py` | Runs on a ~5W Raspberry Pi. Watches a shared folder; on request, fires Wake-on-LAN magic packets to wake any fleet machine. The 1000W rigs stay off until needed. |

Docs: `wake-pi-bringup.md` (bring-up checklist), `FLEET-ROLES.md`
(dual-use design — every machine carries two roles).

## Philosophy

- Measure size, not free space, for system design.
- The fleet is off by default. Wake on demand, spin down when done.
- Computers are capital: their compute, used correctly, earns money.
- Small models on existing hardware beat rented GPUs for volume work.

## Status

Built October 2026, in nightly sessions with Muse. Learning Python by
building tools that run a real fleet — not exercises.
