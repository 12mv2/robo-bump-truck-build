# Orin software: manifest

© 2026 Rooney Industries LLC · MIT licence

This directory is the Python stack that runs on the truck's Orin: `live_trail.py` and every local module it imports or loads. It is the snapshot that launched runs 27 to 31.

| Item | Value |
|---|---|
| Source commit | `92fd5003ccb2b65991a602fab9081fc9ba1348d4` |
| Files | 19 Python files, 9082 lines |
| Third-party pins | [requirements.txt](requirements.txt) |

**What the truck runs at power-up is not this snapshot.** Autostart on the truck today launches `live_trail.py` with sha256 `b6ab3aff4d1ac451ae3ec53296f4ed44719b9a3878ed49eb5920a2c7be6f61fb`. This snapshot is shipped because it is the code runs 27 to 31 used.

## Files

Each file is the source file with one licence line added at the top (after the `#!` line where there is one). Comments and strings that named people, partners, private paths, addresses or radio channel numbers were reworded; thresholds, PWM values and gains are unchanged. The lines and sha256 below are of the files in this directory.

| Path | Lines | sha256 | What it does |
|---|---|---|---|
| `live_trail.py` | 3068 | `c5de4c1ad6cad26f39d360c170e5bc6d87e60f9af7ccd6283b0b350c4db7e1dd` | Entry point: phone stream -> DINOv2-S/14 -> steering head -> line fit -> pure pursuit, and every frame logged; hands intent to an output owner |
| `output_owner.py` | 944 | `98eeae8d13b24615d6a8cedf0b201c5c5dba73bda2a08bf6a56c7467920a22b9` | Legacy output owner: the one thread that writes steering and throttle overrides to the flight controller, with every veto and the start gesture |
| `owner_simple.py` | 186 | `60d9a2a176718e7d937c6ffd002b3ad3b2f4002b5891c1a64e386f9a9d1c6c79` | Simple output owner (`--owner simple`): [the mode switch](../../docs/radio-and-controller.md#the-mode-switch) selects Human, Assist or Stop; the speed dial is the throttle; the kill dial and the wheel or trigger release |
| `recovery.py` | 485 | `58a5e7254fd4ba885c4689855cf0ede2548dfa6bf1f90deb98c547ff68212c96` | Bounded brake, neutral and reverse sequence for a stall, with its validated limits; issues no writes itself |
| `batch.py` | 106 | `5332d9ecf208cdc672ec5e1da2ec9fff3a0b72a9736f9c6d74f912f6400e7cc8` | Repeat cells under one start; advances a cell only after measured rest and neutral servo feedback |
| `cell_plan.py` | 211 | `fe30aab2c7216f0246f8c470e82d894bb75854daa3beb46ba3ce8eb0aea3097b` | Per-cell parameter changes applied at a confirmed batch cell boundary |
| `continuous_sweep.py` | 258 | `51ca606b7541af0853d0fc8511fbb8da65f15030cddbad5ebc0c931f11764450` | Timed tuning sweep of four knobs on the frame thread, plus the file inbox that changes stages without a relaunch |
| `path_recovery.py` | 1379 | `a6bc73d4163dfe59c9c6e5e2710fc63a5f3853c63872c9e211d428dd095506d4` | Path Recovery and turnarounds: SEARCH, ACQUIRE, ALIGN, FOLLOW state machine, gyro heading input and event log |
| `motion_signal.py` | 149 | `0e11012cfd166e9beabbfcd274a39fe749124a8055fc031dbbe0da9533c8d0a6` | Shadow-only image motion signal (is the truck moving?); logged, never read by control |
| `heading_telemetry.py` | 154 | `895dd00fd1a4544364da14905a34d913623bda4320fcbf990e6b65d33e35c4ff` | Read-only flight-controller telemetry negotiation at startup (attitude and servo feedback rates) |
| `blue_gate.py` | 108 | `c955078ab007200cf72099910cede3909d9cf47ce169119d769337132e12d6d3` | Opt-in blue-tape colour gate that caps the head's probability where the feed shows no blue |
| `durable_run.py` | 37 | `152aae3083c3448c6ab5c620a7c9fc88740341556dc663c111bc80436df8ec94` | Atomic writes and directory sync for recording checkpoints, called by the disk-writer thread |
| `inspect_reverse.py` | 77 | `a8f25c66e46b69d702c9ab0645dba1f6ab91362e5a7d9aaf63bae8a562cddddc` | Read-only receipt of the ESC and ArduRover reverse configuration; also the rover heartbeat wait |
| `launch_phase.py` | 102 | `ad5d624ec71417703eab0eb68057f2a6de3e6f1335cabdb71f75450f5d4697e1` | Proof written by a managed launch that it exited before touching the flight controller |
| `package_id.py` | 159 | `ce51e69f6e3a136ea14f26784a97dd50269f77c1187f586fd4a14e89c830ba1f` | Hashes the code on disk and the modules actually loaded, so a run records what ran |
| `reverse_trial.py` | 587 | `b2cd4944edf57fdcd696c8343021904235aadd2a2fb8baac01e608f0dce9b879` | One supervised reverse trial: server inside live_trail.py and an operator client that runs from a laptop over ssh |
| `truck_status.py` | 231 | `ba4cc278d2167e000fdb4fc68d8dace75fcd913e2783cd569569e6f07badee8a` | Writes the control layer's status snapshot each tick and prints it (`python3 truck_status.py`) |
| `setup-tools/setup_capture.py` | 382 | `879b4f0e45f0b528467849079a28344dadc66fffad400b8b9ceb273aa4c37fe2` | Captures the launch recipe, file bytes and effective setup before the owner can send, and observes the run |
| `setup-tools/setup_store.py` | 459 | `6628bddd4227db60902d4ffc3e88a9696ff73eb1fb6f260ea4774a0d9b16441d` | Content-addressed store for the setup snapshots that setup_capture.py writes |

## Differences from the source a user will notice

| Change | Why |
|---|---|
| The external-planner input is `--relay-udp`, `--relay-mode`, `--relay-max-age-ms`, `--relay-sign`, its frame-log key is `relay`, and the setup scenario is `relay-controller` | Renamed from a private project name. Off by default; a run without these flags is unchanged |
| `reverse_trial.py --orin` and `truck_status.py --orin` take `USER@HOST` and use your own key-based ssh (`BatchMode=yes`); `--orin` is required by `reverse_trial.py` | The source carried a fixed address, user and password |
| `reverse_trial.py --dir` defaults to `~/live/trials` on the Orin; its local ledger defaults to `trials/trials.jsonl` beside the script | The source defaults were private paths |
| `setup-tools/setup_capture.py` has no partner-bundle capture function and no observer for it | Partner code with no caller in this stack |
| Line numbers: `live_trail.py` line N in the source is line N+1 here | The licence line sits after the `#!` line |

`~/live` is the deploy directory on the Orin, relative to the user's home. The code reads and writes `~/live/RECORD_ON`, `~/live/STREAM_FPS`, `~/live/status.json`, `~/live/trials/`, `~/live/runs/`, `~/live/setup-tools/` and `~/live/setup-store/`.

## Not in this directory

| Item | Where |
|---|---|
| The steering head `centre_head_lag0_r8c_v1_OVAL4.npy`, sha256 `4b78e28749ca465b37a3ede775a272daa84e8cc7b793f7fc627ba00a83926777` | Not yet published. Described on [model](../../model/README.md) |
| DINOv2-S/14 backbone (`dinov2_vits14_pretrain.pth` and the `facebookresearch_dinov2` hub checkout) | The torch.hub cache on the Orin; `live_trail.py` loads it with `source="local"` |
| Launch files: `start.sh`, `auto_boot.sh` (v2), `auto.conf` and the systemd unit | Not part of the source commit, so not in this snapshot |

## Known issues in this snapshot

The code is shipped as runs 27 to 31 used it; none of these is fixed here. Line numbers are those of the files in this directory. Each row ends with the operating rule that covers it. Two rules cover most rows: keep the kill dial in hand whenever the stack runs, and never leave the truck powered and unattended.

| # | Level | File:line | What happens | Operator rule |
|---|---|---|---|---|
| 1 | High | `owner_simple.py:55`, `owner_simple.py:113`, `owner_simple.py:133`; `live_trail.py:2736`, `live_trail.py:2872-2878` | With `--owner simple`, losing the lane, or a stalled frame loop that leaves the steering intent stale, sets steering straight but does not drop the throttle: it stays at the speed dial. Only a throttle cap or a Path Recovery manoeuvre in the intent brings it to neutral. The `--hold-ms` help text and the file header say the output is released after that time; for throttle it is not. The end-of-run reasons `laps_done`, `route_end` and `path_invalid` are not acted on by this owner either; only reasons that start with `recovery_` are. The exits are the kill dial, the mode switch, a touch of the wheel or trigger, a stale radio link or a stale heartbeat | Keep the kill dial in hand whenever the stack runs. Never leave the truck powered and unattended. Bring the speed dial to the bottom at the first sign of a lost lane or a stalled stream |
| 2 | Medium | `live_trail.py:805` | A heartbeat from any MAVLink source other than a ground station refreshes the heartbeat age and can change the reported mode and armed state; the servo and attitude messages are filtered to the flight controller, this one is not. A second MAVLink device on the link can hide a lost flight-controller heartbeat from both owners | Keep the flight-controller link to the Orin free of other MAVLink devices |
| 3 | Medium | `live_trail.py:1230-1233`, `live_trail.py:2519`, `live_trail.py:2524` | Ctrl-C only sets a flag, and the flag is read after the next camera frame arrives. With the phone not streaming, Ctrl-C does nothing and the owner thread keeps sending. A hard kill of the process skips the owner's release on exit (`live_trail.py:3038`) | Stop with the kill dial or the mode switch first, then end the process. Do not rely on Ctrl-C when the stream is down |
| 4 | Medium | `live_trail.py:689`, `recovery.py:185` | The flight controller's `RC_OVERRIDE_TIME` is read but checked (at 0.5 s or less) only for `--stall-recovery`. In other runs, if the process dies hard, the flight controller keeps the last override until that timeout (3 s by default) | Keep the kill dial in hand. Set `RC_OVERRIDE_TIME` low on the flight controller before running |
| 5 | Medium | `reverse_trial.py:50`, `truck_status.py:214` | The ssh calls to the Orin use `StrictHostKeyChecking=no`, so a changed host key is not refused. A host answering at the Orin's address could return a forged trial result, which `accept` would turn into a reverse profile. Both files are imported by `live_trail.py` (`live_trail.py:1908`, `live_trail.py:2294`), so the line is left as shipped | Run the reverse-trial client only on a network you control. Connect once by hand so the host key is recorded, and check it |
| 6 | Medium | `reverse_trial.py:139` | A reverse-trial result is accepted for any trial that carries the same `--trial-id`. Reusing an id writes the earlier trial's result as the new one while the new trial runs unobserved | Use a fresh `--trial-id` for every trial |
| 7 | Medium | `live_trail.py:1914`, `live_trail.py:1931`, `output_owner.py:139` | `--reverse-ready --reverse-profile` without `--stall-recovery` passes every start check, then stops with an AttributeError once the model, the flight controller and the snapshot are up. With `--owner simple` the profile is ignored while the status page reads "ACCEPTED BY --reverse-ready". A start-up failure only | Pass `--stall-recovery` with `--reverse-ready`, and use the legacy owner for reverse profiles |
| 8 | Medium | `live_trail.py:791` | After a flight-controller reboot its clock restarts near zero, and servo-feedback packets are dropped until the new uptime passes the old stamp. Recovery and batch runs then end with `servo_stale`. It fails safe but stops the run | Restart the flight controller and the stack together |
| 9 | Medium | `setup-tools/setup_capture.py:358-377`, `live_trail.py:1074` | The closing setup observation re-reads the whole frame log and checks every stored file, on the log-writer thread, which is given 60 s to finish at exit. On a long run it can run out of time; the final setup record and the `last_tested` reference are then not written and the frame log is not synced. How long it takes was not measured | After a long run, check that the run folder holds the final setup record before relying on it |
| 10 | Medium | `path_recovery.py:802-814` | The 2 s of sustained following needed to arm automatic recovery is measured from the first lane frame and is not reset when the lane is lost and found again, so a brief re-sighting can arm it | Treat automatic recovery as armed after any lane sighting; keep the kill dial in hand |
| 11 | Low | `live_trail.py:117`, `live_trail.py:143-144` | With no `RECORD_ON` file, a stop-recording request goes to the phone in the first second of each run and stops a recording started by hand. The sent-state is not reset when the phone reconnects | Start the phone recording after the stack is up |
| 12 | Low | `setup-tools/setup_capture.py:215` | When `--log` is omitted, the setup capture sets it, so a compute-only, `--dry-run` or video run records frames and pictures. Feed pictures fill the disk over many runs | Check free disk space before a session |
| 13 | Low | `live_trail.py:1679`, `live_trail.py:2158` | `--fc` without `--dry-run` sends, and `--owner` defaults to `legacy`. Nothing arms the vehicle or changes its mode or parameters; `--start-armed` is opt-in | Add `--dry-run` for a first check and `--owner simple` as in [orin-setup](../../docs/orin-setup.md#install-the-stack-from-srcorin) |
| 14 | Low | `reverse_trial.py:527-529` | `--dir` is placed unquoted in the remote shell command, and a request line equal to `TRIALEOF` (for example in `--question`) cuts the request short. The inputs are the operator's own arguments | Keep `--dir` free of spaces and quotes, and do not put a line reading `TRIALEOF` in `--question` |
| 15 | Low | `live_trail.py:485`, `live_trail.py:2175` | The taught-path file is loaded with `allow_pickle=True` and the backbone with `torch.hub.load(source="local")`; both run code from files on the Orin | Use only path files and a hub checkout you made yourself |
