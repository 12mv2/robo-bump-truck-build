# Orin setup

Author: Colin Rooney · © 2026 Rooney Industries LLC · MIT licence

## Pre-start boundary: historical controller

The bundled `src/orin` is the historical **runs 27–31 DINO snapshot**, not an accepted current controller. Its [manifest and known issues](../src/orin/MANIFEST.md#known-issues-in-this-snapshot) distinguish it from the later autostart source. The separate October 8 field kit is not supplied; see [ROS path availability](ros.md#how-it-connects-to-the-orin-stack).

Before powered use, follow the complete [first-drive boundary](first-drive.md#pre-start-boundary-historical-controller): intended-controller source review and separate supervised bench/field acceptance remain outstanding. The historical DINO path also needs the [unpublished head](../model/README.md#getting-the-head-file) and working [phone app access/streaming](ros.md#robot-eyes-the-phone-app).

This page records the historical Jetson Orin Nano setup: camera frames in, steering and throttle out, with the trail stack started by hand. The steps below are reference material within the pre-start boundary above. The versions below were read from the truck's working Orin on 2026-10-10 <!-- B8:6-17 -->. They were not produced by following this page on a fresh board, so treat these as an observed installed state, not a verified fresh-install recipe.

## Recorded installed versions

| Layer | Version | Check with |
|---|---|---|
| L4T (Jetson Linux) | R36 rev 4.7 (36.4.7) <!-- B8:6, B8:30, F:745 --> | `cat /etc/nv_tegra_release` |
| JetPack | 6.2.1 <!-- B8:7, B8:31, F:745 --> | `dpkg-query -W nvidia-jetpack nvidia-l4t-core` |
| OS | Ubuntu 22.04.5 LTS (jammy) <!-- B8:9, B8:36-39 -->, aarch64 | `lsb_release -a` |
| Kernel | 5.15.148-tegra <!-- B8:8 --> | `uname -a` |
| Python | 3.10.12, the system interpreter <!-- B8:10, B8:41 --> | `python3 --version` |
| ROS 2 | Humble only <!-- B8:11 --> | `printenv ROS_DISTRO` (`ros2` has no version flag) |
| PyTorch | torch 2.8.0 with torchvision 0.23.0, CUDA 12.6 <!-- B8:201-202, B8:453, F:747 --> | see step 5 |
| TensorRT | 10.3.0, installed but not used <!-- B8:196, F:782 --> | `pip3 list` |
| NumPy | below 2 (1.26.4 on the truck) <!-- B8:136, F:786 --> | `pip3 list` |
| Power mode | 25 W, mode id 1 <!-- B8:15, F:787 --> | `nvpmodel -q` |

NVIDIA's [JetPack 6.2.1 release](https://developer.nvidia.com/embedded/jetpack-sdk-621) includes Jetson Linux **36.4.4**. The table records the truck's later **36.4.7** readback; this repository does not reconstruct how that BSP update was installed. A stock 6.2.1 image therefore does not reproduce every recorded version.

On the truck's Orin the root disk is 233 GB of NVMe, 28% used at the time of the check, with 7.4 GiB of RAM and 3.7 GiB of swap <!-- B8:16, B8:17 -->. The pip list also shows a CUDA toolkit package at a different version from the CUDA that PyTorch reports; this page does not give one CUDA number for the whole machine <!-- B8:12, B8:453 -->.

## Historical setup steps — gated reference

1. **Image reference.** For the historical baseline, use the [JetPack 6.2.1 release page](https://developer.nvidia.com/embedded/jetpack-sdk-621) and [versioned installation documentation](https://docs.nvidia.com/jetson/jetpack/6.2.1/install-setup/index.html). The stock image includes L4T 36.4.4, not the truck's observed 36.4.7. Follow NVIDIA's device/firmware prerequisites; this page supplies no tested flashing or BSP-update procedure for reproducing the truck.
2. **First boot.** Complete the Ubuntu first-boot setup, set a password, and connect the Orin to your network. Note your Orin's address; every later step runs on it over SSH or at a keyboard.
3. **Record the installed versions** and compare them with the historical table above. A match is not a powered-controller acceptance test; investigate differences before adopting this reference setup.
4. **Install ROS 2 Humble.** Use the ROS project's apt-source package, whose file name starts with `ros2-apt-source`. The similar-looking name without the `2` does not exist: the download returns a 9-byte "Not Found" page that `dpkg` then rejects <!-- F:746 -->. Then install Humble from apt by following the ROS project's [Ubuntu deb install page](https://docs.ros.org/en/humble/Installation/Ubuntu-Install-Debs.html); this page gives no install commands of its own, because none were measured here. The ROS 2 repository's own install steps are in the [install section of ros](ros.md#install).
5. **Install PyTorch 2.8.0 and torchvision 0.23.0** from the Jetson AI Lab wheel index for JetPack 6 and CUDA 12.6, [`jp6/cu126`](https://pypi.jetson-ai-lab.io/jp6/cu126) <!-- F:747 -->. Do not use torch 2.9.1 or newer: it links a library this JetPack image does not ship, and `import torch` fails <!-- F:747 -->. Check it:

   ```
   python3 -c "import torch, torchvision; print(torch.__version__, torch.cuda.is_available(), torch.version.cuda)"
   ```

   Expected: `2.8.0 True 12.6` <!-- B8:453 -->.
6. **Pin NumPy below 2:** `pip3 install "numpy<2"`. The 2.8.0 wheel is built against NumPy 1.x. With NumPy 2, `torch.from_numpy` fails on real data while random-tensor benchmarks still pass, so the install looks healthy until it is fed a frame <!-- F:786 -->. `pip` may then warn that the headless OpenCV package wants NumPy 2; it works anyway <!-- F:786 -->.
7. **Keep the recorded 25 W baseline.** This build was recorded in 25 W mode <!-- F:787, B8:15 -->; that is not a universal hardware maximum. Read `nvpmodel -q` and check the modes supported by your installed configuration. [NVIDIA’s R36.4.4 power-mode documentation](https://docs.nvidia.com/jetson/archives/r36.4.4/DeveloperGuide/SD/PlatformPowerAndPerformance/JetsonOrinNanoSeriesJetsonOrinNxSeriesAndJetsonAgxOrinSeries.html) lists a higher-clocked MAXN_SUPER mode for Orin Nano 8 GB only with a supported Super flash configuration. Its availability does not establish thermal, power-supply or controller acceptance; this guide does not call for changing modes.
8. **Pin the clocks.** `sudo jetson_clocks` holds the GPU at 918 MHz <!-- F:1641 -->. On the same bench, capture-to-record fell from 227 ms to 198 ms and the perception stage (the perception model, line fit and pursuit) from 44 ms to 23 ms <!-- F:1639 -->. Same build, same phone, 8,689 frames over a 580 s run, statistics from frame 1,000 on <!-- F:1641 -->.
9. **Only if you build against libtorch or a ROS workspace:** install the Humble packages `cv-bridge`, `image-transport` and `camera-calibration`, plus `python3-colcon-common-extensions` <!-- F:2333 -->. JetPack does not put `nvcc` on `PATH`, and CMake fails inside PyTorch's config until you export the CUDA compiler path and `/usr/local/cuda/bin` <!-- F:2333 -->. The ROS repository is covered on [ros](ros.md).
10. **Historical deployment reference, after the pre-start review and acceptance gates: copy the trail stack to the Orin and verify its hash**, as in [Install the stack from src/orin](#install-the-stack-from-srcorin) below. Copy the drive tool and every module it imports, not only the entry file: a hand-kept file list once shipped 2 of the 6 modules the drive tool imports <!-- F:2034 -->. Then compute a SHA-256 of each file on the Orin and compare it with the source; treat the install as incomplete until every hash matches. Run the tool's non-actuating startup check on the Orin before the first powered start <!-- F:2039 -->.
11. **Historical powered-start reference:** the install section below records the manual launch (autostart is not shipped in this release). It is gated by the pre-start boundary above and [first drive](first-drive.md).

## Install the stack from src/orin

The trail stack is the Python in [src/orin](../src/orin/MANIFEST.md): 19 Python files, the snapshot that launched runs 27 to 31 <!-- src/orin/MANIFEST.md:5, src/orin/MANIFEST.md:10 at f6ff42a -->. Autostart is not shipped in this release. The truck's own autostart deploys a later `live_trail.py`, sha256 starting `b6ab3aff` <!-- src/orin/MANIFEST.md:13 at f6ff42a -->, and its launch files are not in the snapshot <!-- src/orin/MANIFEST.md:59 at f6ff42a -->. The manual launch below is retained as historical reference under the pre-start boundary.

These snapshot pins describe the DINO source only and cannot substitute for the separate field runtime.

The following installation and powered launch recipe is historical/reference only, gated on the review and separate acceptance above. Hash agreement establishes file identity, not controller acceptance.

1. **Copy `src/orin` to the Orin** into `~/live` in your home directory, keeping the `setup-tools/` subfolder. The code reads and writes under `~/live` <!-- src/orin/MANIFEST.md:51 at f6ff42a -->. Use your Orin's address with your own key-based ssh. Compare a SHA-256 of each file with the table in the manifest; the install is incomplete until every hash matches <!-- src/orin/MANIFEST.md:21-39 at f6ff42a -->.
2. **Install the Python pins** from `src/orin/requirements.txt`: NumPy 1.26.4, headless OpenCV 5.0.0.93, pymavlink 2.4.49 and torch 2.8.0 <!-- src/orin/requirements.txt:10-13 at f6ff42a -->. The file's comments list what a pip install does not give you: the platform (Python 3.10.12 on Ubuntu 22.04.5, JetPack 6.2.1), TensorRT as a JetPack package that this code does not import, NVIDIA's Jetson build of torch (a PyPI wheel of the same version has no Orin GPU support), and `ffmpeg` as a system binary that decodes the phone stream <!-- src/orin/requirements.txt:5-8 at f6ff42a -->. torchvision 0.23.0 sits beside torch but is not imported <!-- src/orin/requirements.txt:14 at f6ff42a -->. Steps 5 and 6 above cover the torch and NumPy installs.
3. **Place the DINOv2-S/14 backbone.** `live_trail.py` loads it from a local `torch.hub` checkout named `facebookresearch_dinov2_main` in the hub cache, with the checkpoint `dinov2_vits14_pretrain.pth` <!-- src/orin/live_trail.py:2175 at f6ff42a, src/orin/MANIFEST.md:58 at f6ff42a -->.
4. **Place the steering head** `centre_head_lag0_r8c_v1_OVAL4.npy`; the file is not in the repository and is not yet published. Its sha256 is `4b78e28749ca465b37a3ede775a272daa84e8cc7b793f7fc627ba00a83926777` <!-- src/orin/MANIFEST.md:57 at f6ff42a -->. More on it is on [model](../model/README.md).
5. **Point-of-use hazards:** lane loss, stale intent and route end can retain speed-dial throttle; wheel override changes steering only. Ctrl-C can wait on the camera while output continues, and hard kill skips release. Keep the kill available and follow the [powered-use/shutdown boundary](first-drive.md#pre-start-boundary-historical-controller). Keep AUTO_OFF present on an autostart truck before taking the phone stream; it allows only one client. Stop recording before a host reset. <!-- src/orin/MANIFEST.md:67-70; F:2970, F:1142 -->

   **Historical powered launch — only after the pre-start gates** from the directory holding `live_trail.py`, with the phone stream tunnelled as in the file's own header <!-- src/orin/live_trail.py:91-96 at f6ff42a -->. The tunnel needs `usbmuxd`, `iproxy` and `idevice_id` on the Orin and the phone app streaming to it; this page does not install them. The header runs them as `sudo usbmuxd --no-preflight` and `iproxy 7790 7790 -u $(idevice_id -l | head -1) &` <!-- src/orin/live_trail.py:92-93 at f6ff42a -->, and 7790 is the default of `--port` <!-- src/orin/live_trail.py:1591 at f6ff42a -->. The app is covered on [ros](ros.md#robot-eyes-the-phone-app). Then:

   ```
   python3 live_trail.py --head centre_head_lag0_r8c_v1_OVAL4.npy --pitch <pitch> \
       --fc /dev/ttyACM0 --owner simple --cam-fwd 0.24 --log runs/first
   ```

   Take `<pitch>` from your own camera mount: [calibration](calibration.md#camera-pose-pitch-and-height) covers measuring it. `--head` and `--pitch` are required <!-- src/orin/live_trail.py:1385 at f6ff42a, src/orin/live_trail.py:1390 at f6ff42a -->; `--pitch` takes `up`, `normal`, `down`, a number of degrees below the horizon, or `live`. `--fc` is the flight-controller serial port; `/dev/ttyACM0` is the file's own example <!-- src/orin/live_trail.py:95 at f6ff42a -->, and `ls /dev/ttyACM*` shows yours. Without it the stack only computes <!-- src/orin/live_trail.py:1584 at f6ff42a -->. `--owner simple` selects the simple output owner: [the mode switch](radio-and-controller.md#the-mode-switch) picks Human, Assist or Stop, the speed dial is the throttle and the kill dial is the kill <!-- src/orin/live_trail.py:1679-1682 at f6ff42a, src/orin/owner_simple.py:11-13, src/orin/owner_simple.py:16 at f6ff42a -->. `--cam-fwd` is how far the lens sits ahead of the rear axle in metres; 0.24 is the measured value, and the default 0 is the behaviour before it was measured <!-- src/orin/live_trail.py:1405 at f6ff42a, src/orin/live_trail.py:1406-1407 at f6ff42a -->. The run-plan files of runs 27 to 31 are not part of this release; the other tuning flags are listed by `python3 live_trail.py --help`. Add `--dry-run` to connect to the flight controller and check it without actuator override sends; telemetry and parameter traffic still occur <!-- src/orin/live_trail.py:1590 at f6ff42a -->.

The historical bench procedure used wheels off the ground and the kill dial in reach; see [first drive](first-drive.md). Those precautions do not resolve the snapshot's lane-loss or stale-intent throttle issue.

This snapshot has known issues; its recorded operating rules do not establish a current validated controller: [Known issues in this snapshot](../src/orin/MANIFEST.md#known-issues-in-this-snapshot).

## Autostart and the pause file

Autostart is not shipped in this release (see [Install the stack from src/orin](#install-the-stack-from-srcorin)). On the truck it is two services: a resident service that runs the drive tool, and a service for the live feed page; both were running at the check <!-- B8:13, F:2978 -->. The drive tool service restarts 5 seconds after it exits <!-- F:2978 -->.

Historical/reference only: this release does not ship autostart. The following describes the research truck, not an instruction to enable or alter services:

At power-up the Orin starts the trail stack and begins sending commands; seven guards zero the output, and the Orin never arms the flight controller. Whether the controller arms at boot is set by its parameters and the radio; the recorded bench rule was to keep AUTO_OFF touched whenever the truck was on the bench. <!-- F:2976, F:2978 -->

The 2026-10-10 read-only check found the service enabled and active, waiting while AUTO_OFF was present. <!-- B8 probe 2026-10-10; B49 --> The phone serves one stream client: when another receiver released it, autostart took the stream unattended. Keep the pause in place before starting another phone client. <!-- F:2970 -->

The recorded way to pause the truck's own autostart was to create the file AUTO_OFF in the trail stack's directory on the Orin <!-- F:2970 -->. The recorded procedure kept the [kill dial](radio-and-controller.md) on the radio within reach whenever the truck was powered; the kill path is on [power and safety](power-and-safety.md).

## Where this goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| `import torch` fails outright | torch 2.9.1 or newer <!-- F:747 --> | Install 2.8.0 |
| Benchmarks pass, real frames fail with "Numpy is not available" | NumPy 2 <!-- F:786 --> | Pin NumPy below 2 |
| ROS apt-source download rejected by `dpkg` | Wrong package name, 9-byte "Not Found" file <!-- F:746 --> | Use the `ros2-` prefixed name |
| CMake fails inside PyTorch's config | `nvcc` not on `PATH` <!-- F:2333 --> | Export the CUDA compiler path |
| Drive tool missing modules after a copy | The copy list was shorter than the set of imported modules <!-- F:2034 --> | Copy the full import set, verify hashes |

More pitfalls are on [pitfalls](pitfalls.md); how the pieces connect is on [system](system.md).
