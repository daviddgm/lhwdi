# CPU-ID Linux

A lightweight, native-feeling hardware telemetry, system inspection, and CPU benchmarking utility for Linux, designed with a clean desktop interface inspired by the classic **CPUID CPU-Z** for Windows.

## Overview & Design Philosophy

**CPU-ID Linux** brings the familiar, compact, tabbed layout of **CPU-Z** to Linux desktops—combining real-time `/sys` and `/proc` kernel telemetry with an embedded desktop webview (`pywebview` / Flask).

* **CPU-Z Inspired Interface:** Compact fixed-window layout (`520px` wide) with categorized tabs (*CPU, Caches, Mainboard, Memory, SPD, Graphics, Drives, Sensors, Bench, About*), real-time per-core frequency grids, and both **Dark Mode** and **Light Mode** themes.
* **Processor & GPU Badges:** Features dedicated hardware logo boxes alongside processor and graphics adapter headers, dynamically matching detected CPU/GPU models against a local badge index (supporting both custom vector badges and classic CPU-Z style hardware emblems).
* **Hybrid Python + C11 Native Engine:** Uses Python/Flask for hardware parsing and API delivery, paired with an on-the-fly compiled **C11 native shared library (`gcc -O3 -march=native`)** for accurate, GIL-free Single-Thread and Multi-Thread CPU benchmarking.

## Key Features

* **CPU:** Detailed processor identification (Name, Vendor, Architecture, Family/Model/Stepping, Instruction Flags), min/max frequency limits, core/thread topology, BogoMIPS, and live 1-second per-core clock polling.
* **Caches:** Full L1d, L1i, L2, and L3 cache hierarchy breakdown with size and associativity ways.
* **Mainboard & BIOS:** Motherboard manufacturer, model, revision, and UEFI/BIOS vendor, version, and release date via DMI sysfs.
* **Memory & SPD:** Live RAM/Swap usage bars plus slot-by-slot DIMM inspection (`dmidecode` + `decode-dimms` EEPROM parsing for JEDEC/XMP timings, rank, voltage, and DRAM manufacturer).
* **Graphics (GPU):** Multi-GPU selector showing board partner, PCI Device ID, active kernel driver, PCIe link width/speed, real-time core/memory clocks (`pp_dpm_sclk` / `pp_dpm_mclk`), GPU load, temperature, board power, and VRAM utilization.
* **Drives (Storage):** NVMe and SATA/USB drive detection with PCIe link negotiation, firmware revision, drive temperature, serial number, and mounted partition space usage bars.
* **Sensors:** Grouped hardware temperature readings (`k10temp`, `zenpower`, `amdgpu`, `nvme`, Super I/O) with critical threshold indicators.
* **Native Benchmark:** Built-in Single-Thread and Multi-Thread FPU/ALU benchmark calibrated against reference processors (AMD Ryzen & Intel Core series).
* **Validation Reports:** One-click export of complete hardware snapshots to `~/Downloads` in both `.TXT` and formatted `.HTML` validation report formats.

## Dependencies

### Python Packages

* **Python 3.10+**
* `flask` — Local backend server and REST API (`/api/static`, `/api/dynamic`, `/api/bench`, `/api/export`)
* `pywebview` *(optional, recommended)* — Renders the UI in a standalone native desktop window; falls back automatically to the default web browser if unavailable.

### System & CLI Utilities (Linux)

* `gcc` — Required to compile the native C11 benchmark engine (`/tmp/cpuid_native_bench.so`) on first run.
* `pciutils` (`lspci`) — Used for clean commercial GPU naming and PCI device identification.
* `util-linux` (`lsblk`) — Used to map storage partitions and mount points.
* `dmidecode` *(optional, requires `sudo` / root)* — Enables detailed physical RAM slot detection in the **SPD** tab.
* `i2c-tools` (`decode-dimms`, with `ee1004` / `i2c-piix4` kernel modules) *(optional)* — Enables full JEDEC and XMP memory timing tables from DIMM EEPROMs.

## Installation & Usage

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-username/cpuid-linux.git
   cd cpuid-linux
   ```

2. **Install Python dependencies:**
   ```bash
   pip install flask pywebview
   ```
   *(On Gentoo Linux, you can install `dev-python/flask` via Portage and `pywebview` in a virtual environment or user site-packages.)*

3. **Run the application:**
   ```bash
   python3 app.py
   ```
   The app starts a local server on `http://127.0.0.1:5055` and opens the desktop window automatically.

## Customizing CPU & GPU Logos

Hardware logos are stored in `static/logos/` and mapped via `static/logos/logos_index.json`:

```json
{
  "cpu": [
    { "file": "amd_ryzen_9.png", "keywords": ["ryzen", "9"] },
    { "file": "intel_core_i7.png", "keywords": ["i7"] }
  ],
  "gpu": [
    { "file": "gpu_radeon_rx.png", "keywords": ["radeon"] },
    { "file": "gpu_nvidia_rtx.png", "keywords": ["rtx"] }
  ]
}
```

When hardware data is loaded, the frontend tokenizes the detected CPU or GPU name and selects the entry with the highest keyword match score.

## License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

```text
MIT License

Copyright (c) 2026 David Martinz

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Credits & Acknowledgments

* **Author & Engineering:** David Martinz ([gandhimartinz.eng.br](https://gandhimartinz.eng.br))
* **UI Inspiration:** Inspired by the iconic layout and ergonomics of **CPUID CPU-Z**.
* **Trademark Notice:** *AMD, Ryzen, Radeon, Intel, Core, NVIDIA, GeForce,* and *CPU-Z* are trademarks of their respective owners and are used solely for hardware identification purposes.