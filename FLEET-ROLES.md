# Fleet dual-use design (his order, 2026-10-04, standing)

Every machine carries TWO use-designs: a primary role and a secondary.
Muse must be able to switch a machine between them on demand, via box
tasks (no hands-on needed except power). No switch happens without a
real need — "so far we do not have a need."

| Machine | Primary | Secondary | Switch via |
|---|---|---|---|
| M1 xps3060ti (i7-11700/8c16t, 32GB, 3060 Ti, 1.84TB) | Pipeline workhorse (extraction, heavy compute) | GPU inference (Ollama, 8GB VRAM) | box task: start/stop ollama, queue work |
| M2 lenovo1070 (i7-6700/4c8t, 32GB, GTX 1070, 2.04TB) | His hands (interactive) + cheap brain (Ollama 6-7B) | Data staging / analyst (disk room) | box task |
| M3 msi (Katana, 3060 laptop) | Portable worker | GPU inference | box task |
| 3090 rig (24GB VRAM) | AI work (RAG server/brain) — or rented out | Whichever isn't primary; it must pay for itself | his call per job |
| 7900 XTX rig | Data prep / compute client — or rented out | Whichever isn't primary | his call per job |
| Wake Pi (~5W) | Fleet wake sender | LAN monitor | reflash/repurpose |
| M4 Mac | Mobile client | Light analysis | — |

Switching is task-driven: install service, start/stop, requeue work.
Power is the only hands-on step (his). The 1000W rigs additionally
follow the powerhouse policy: on when needed, down when not.
