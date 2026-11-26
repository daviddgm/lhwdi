#!/usr/bin/env python3

import os
import re
import base64
import ctypes
import glob
import json
import platform
import shutil
import subprocess
import threading
import webbrowser
import time
import pathlib
from datetime import datetime
from flask import Flask, jsonify, render_template, request, Response

app = Flask(__name__)


def read_file(path):
    try:
        with open(path, 'r') as f:
            return f.read().strip()
    except Exception:
        return None


# -------------------------------------------------------------------------
# ARM / AArch64 SoC & Core Lookup Tables
# -------------------------------------------------------------------------
ARM_PART_MAP = {
    ("0x41", "0xd03"): "ARM Cortex-A53",
    ("0x41", "0xd04"): "ARM Cortex-A35",
    ("0x41", "0xd05"): "ARM Cortex-A55",
    ("0x41", "0xd07"): "ARM Cortex-A57",
    ("0x41", "0xd08"): "ARM Cortex-A72",
    ("0x41", "0xd09"): "ARM Cortex-A73",
    ("0x41", "0xd0a"): "ARM Cortex-A75",
    ("0x41", "0xd0b"): "ARM Cortex-A76",
    ("0x41", "0xd0d"): "ARM Cortex-A77",
    ("0x41", "0xd41"): "ARM Cortex-A78",
    ("0x41", "0xd46"): "ARM Cortex-A510",
    ("0x41", "0xd47"): "ARM Cortex-A710",
    ("0x41", "0xd48"): "ARM Cortex-X2",
}

ARM_SOC_DB = {
    "rockchip,rk3588s": ("Rockchip RK3588S", "8 nm (Samsung 8LPP)", "FCBGA-1088 (SoC)"),
    "rockchip,rk3588":  ("Rockchip RK3588",  "8 nm (Samsung 8LPP)", "FCBGA-1088 (SoC)"),
    "rockchip,rk3568":  ("Rockchip RK3568",  "22 nm",               "FCBGA-636 (SoC)"),
    "rockchip,rk3566":  ("Rockchip RK3566",  "22 nm",               "FCBGA-547 (SoC)"),
    "rockchip,rk3399":  ("Rockchip RK3399",  "28 nm HKMG",          "FCBGA-828 (SoC)"),
    "allwinner,sun50i-h618": ("Allwinner H618", "28 nm",            "FBGA (SoC)"),
    "allwinner,sun50i-h616": ("Allwinner H616", "28 nm",            "FBGA (SoC)"),
    "allwinner,sun50i-h6":   ("Allwinner H6",   "28 nm",            "FBGA (SoC)"),
    "amlogic,sm1":      ("Amlogic S905X3 (SM1)", "12 nm",           "FBGA (SoC)"),
    "amlogic,g12b":     ("Amlogic A311D / S922X", "12 nm",          "FBGA (SoC)"),
    "amlogic,g12a":     ("Amlogic S905X2 (G12A)", "12 nm",          "FBGA (SoC)"),
    "mediatek,mt8183":  ("MediaTek Helio P60T (MT8183)", "12 nm",   "FBGA (SoC)"),
    "mediatek,mt8195":  ("MediaTek Kompanio 1380 (MT8195)", "6 nm", "FBGA (SoC)"),
    "brcm,bcm2712":     ("Broadcom BCM2712 (RPi 5)", "16 nm",       "FCBGA (SoC)"),
    "brcm,bcm2711":     ("Broadcom BCM2711 (RPi 4)", "28 nm",       "FCBGA (SoC)"),
}


def is_arm_arch() -> bool:
    return platform.machine().lower() in ("aarch64", "arm64", "armv7l", "armv8l", "armhf")


def parse_arm_cpu(cpuinfo: str, cpu: dict) -> None:
    """Populates CPU fields on ARM/AArch64 systems using /proc/device-tree and /proc/cpuinfo."""
    dt_model = (read_file("/proc/device-tree/model") or "").replace("\x00", "").strip()
    dt_compat_raw = read_file("/proc/device-tree/compatible") or ""
    compat_list = [c.strip() for c in dt_compat_raw.split("\x00") if c.strip()]

    soc_name, litho, pkg = None, "N/A", "BGA (Embedded SoC)"
    for c in compat_list:
        if c in ARM_SOC_DB:
            soc_name, litho, pkg = ARM_SOC_DB[c]
            break

    soc_vendor = "ARM SoC"
    for c in compat_list:
        if c.startswith("rockchip,"):
            soc_vendor = "Rockchip"
            break
        if c.startswith("allwinner,"):
            soc_vendor = "Allwinner"
            break
        if c.startswith("amlogic,"):
            soc_vendor = "Amlogic"
            break
        if c.startswith("mediatek,"):
            soc_vendor = "MediaTek"
            break
        if c.startswith("brcm,"):
            soc_vendor = "Broadcom"
            break

    clusters = {}
    blocks = [b for b in cpuinfo.strip().split("\n\n") if "processor" in b.lower()]
    for blk in blocks:
        imp_m = re.search(r"CPU implementer\s*:\s*(0x[0-9a-fA-F]+)", blk)
        part_m = re.search(r"CPU part\s*:\s*(0x[0-9a-fA-F]+)", blk)
        if imp_m and part_m:
            core_label = ARM_PART_MAP.get(
                (imp_m.group(1).lower(), part_m.group(1).lower()),
                f"ARM Part {part_m.group(1)}"
            )
            clusters[core_label] = clusters.get(core_label, 0) + 1

    cluster_desc = " + ".join(f"{cnt}x {name}" for name, cnt in clusters.items()) if clusters else "ARMv8/v9"

    cpu["vendor"] = soc_vendor
    if soc_name and dt_model and soc_name.lower() not in dt_model.lower():
        cpu["name"] = f"{soc_name} ({dt_model})"
    else:
        cpu["name"] = soc_name or dt_model or f"{soc_vendor} Processor"

    cpu["codename"] = cluster_desc
    cpu["lithography"] = litho
    cpu["package"] = pkg
    cpu["cores"] = str(len(blocks)) if blocks else str(cpu["threads"])
    cpu["threads"] = len(blocks) if blocks else cpu["threads"]

    feat_m = re.search(r"Features\s*:\s*(.+)", cpuinfo)
    if feat_m:
        cpu["flags"] = feat_m.group(1).strip()

    bogo_m = re.search(r"BogoMIPS\s*:\s*(.+)", cpuinfo, re.IGNORECASE)
    if bogo_m:
        cpu["bogomips"] = bogo_m.group(1).strip()

    var_m = re.search(r"CPU variant\s*:\s*(0x[0-9a-fA-F]+)", cpuinfo)
    rev_m = re.search(r"CPU revision\s*:\s*(\d+)", cpuinfo)
    part_first = re.search(r"CPU part\s*:\s*(0x[0-9a-fA-F]+)", cpuinfo)
    if part_first:
        cpu["family"] = "ARMv8/v9"
        cpu["model"] = part_first.group(1)
    if var_m and rev_m:
        cpu["stepping"] = f"r{int(var_m.group(1), 16)}p{rev_m.group(1)}"


def detect_cpu_package(vendor: str, family_str: str, model_str: str, name: str) -> str:
    """
    Detects CPU socket / package via DMI Type 4 (if dmidecode is authorized)
    or infers it from CPUID family/model and processor branding.
    """
    # 1. Try reading physical socket designation from DMI Type 4 (Processor Information)
    try:
        out = subprocess.check_output(
            ["sudo", "-n", "/usr/sbin/dmidecode", "-t", "4"],
            stderr=subprocess.DEVNULL, text=True
        )
        m = re.search(r"^\s*Upgrade:\s*(.+)$", out, re.MULTILINE)
        if m:
            upgrade = m.group(1).strip()
            if upgrade and upgrade not in ("Other", "Unknown", "None", "<OUT OF SPEC>"):
                return upgrade
    except Exception:
        pass

    # 2. Fallback inference from CPUID and commercial name
    try:
        fam = int(family_str)
        mod = int(model_str)
    except (ValueError, TypeError):
        return "N/A"

    name_l = name.lower()

    if vendor == "AuthenticAMD":
        if "threadripper" in name_l:
            if fam == 26:
                return "Socket sTR5 (LGA 4844)"
            if fam == 25:
                return "Socket sWRX8 / sTRX4"
            return "Socket TR4 (LGA 4094)"
        if "epyc" in name_l:
            if fam == 26:
                return "Socket SP5 / SP6"
            if fam == 25 and mod in (17, 160):
                return "Socket SP5 (LGA 6096)"
            return "Socket SP3 (LGA 4094)"
        if fam == 26:
            if any(x in name_l for x in ("ai 9", "ai 7", "ai 5", "hx", "hs")):
                return "Socket FP8 (BGA)"
            return "Socket AM5 (LGA 1718)"
        if fam == 25:
            if mod in (97,):  # Raphael
                return "Socket AM5 (LGA 1718)"
            if mod in (116, 117, 120):  # Phoenix / Hawk Point
                return "Socket AM5 / FP7 / FP8"
            if mod in (68,):  # Rembrandt
                return "Socket FP7 (BGA)"
            return "Socket AM4 (PGA 1331)"
        if fam == 23:
            if any(x in name_l for x in ("u", "h", "hs")) and "ryzen" in name_l:
                return "Socket FP5 / FP6 (BGA)"
            return "Socket AM4 (PGA 1331)"
        if fam == 21:
            return "Socket AM3+ / FM2+"

    elif vendor == "GenuineIntel" and fam == 6:
        if mod in (198,):
            return "Socket LGA 1851"
        if mod in (181, 197, 170, 172, 189):
            return "FCBGA 2049 / Mobile"
        if mod in (151, 183, 191):
            return "Socket LGA 1700"
        if mod in (165, 167):
            return "Socket LGA 1200"
        if mod in (158, 94):
            return "Socket LGA 1151"
        if mod in (60, 71):
            return "Socket LGA 1150"
        if mod in (85, 106):
            return "Socket LGA 3647 / LGA 4189"

    return "N/A"


def detect_cpu_specs(vendor: str, family_str: str, model_str: str, name: str) -> dict:
    """
    Infers CPU microarchitecture codename and lithography node from CPUID (vendor, family, model)
    and commercial processor name.
    """
    try:
        fam = int(family_str)
        mod = int(model_str)
    except (ValueError, TypeError):
        return {"codename": "Unknown", "lithography": "N/A"}

    name_l = name.lower()

    if vendor == "AuthenticAMD":
        # Family 23 (0x17): Zen, Zen+, Zen 2
        if fam == 23:
            amd_f23 = {
                1: ("Naples / Whitehaven / Summit Ridge (Zen)", "14 nm"),
                8: ("Colfax / Pinnacle Ridge (Zen+)", "12 nm"),
                17: ("Raven Ridge (Zen)", "14 nm"),
                24: ("Picasso (Zen+)", "12 nm"),
                32: ("Dali / Pollock (Zen)", "14 nm"),
                49: ("Rome / Castle Peak (Zen 2)", "7 nm"),
                71: ("Xbox / Renoir (Zen 2)", "7 nm"),
                96: ("Renoir / Lucienne (Zen 2)", "7 nm"),
                104: ("Lucienne (Zen 2)", "7 nm"),
                113: ("Matisse (Zen 2)", "7 nm"),
                144: ("Van Gogh (Zen 2)", "7 nm"),
                160: ("Mendocino (Zen 2)", "6 nm"),
            }
            if mod in amd_f23:
                return {"codename": amd_f23[mod][0], "lithography": amd_f23[mod][1]}
            return {"codename": "Zen / Zen 2 Family", "lithography": "14 nm / 7 nm"}

        # Family 25 (0x19): Zen 3, Zen 3+, Zen 4
        if fam == 25:
            amd_f25 = {
                1: ("Milan / Chagall (Zen 3)", "7 nm"),
                8: ("Chagall Threadripper (Zen 3)", "7 nm"),
                17: ("Genoa / Storm Peak (Zen 4)", "5 nm"),
                24: ("Storm Peak Threadripper (Zen 4)", "5 nm"),
                33: ("Vermeer (Zen 3)", "7 nm"),
                68: ("Rembrandt (Zen 3+)", "6 nm"),
                80: ("Cezanne / Barcelo (Zen 3)", "7 nm"),
                97: ("Raphael (Zen 4)", "5 nm"),
                116: ("Phoenix (Zen 4)", "4 nm"),
                117: ("Hawk Point (Zen 4)", "4 nm"),
                120: ("Phoenix 2 (Zen 4)", "4 nm"),
                160: ("Bergamo (Zen 4c)", "5 nm"),
            }
            if mod in amd_f25:
                return {"codename": amd_f25[mod][0], "lithography": amd_f25[mod][1]}
            return {"codename": "Zen 3 / Zen 4 Family", "lithography": "7 nm / 5 nm"}

        # Family 26 (0x1A): Zen 5 / Zen 5c
        if fam == 26:
            amd_f26 = {
                1: ("Turin (Zen 5)", "4 nm"),
                17: ("Turin Dense (Zen 5c)", "3 nm"),
                32: ("Strix Point (Zen 5)", "4 nm"),
                36: ("Strix Halo (Zen 5)", "4 nm"),
                68: ("Granite Ridge (Zen 5)", "4 nm"),
                96: ("Krackan Point (Zen 5)", "4 nm"),
            }
            if mod in amd_f26:
                return {"codename": amd_f26[mod][0], "lithography": amd_f26[mod][1]}
            return {"codename": "Zen 5 Family", "lithography": "4 nm"}

        if fam == 21:
            return {"codename": "Bulldozer / Piledriver / Excavator", "lithography": "32 nm / 28 nm"}
        if fam == 16:
            return {"codename": "K10 (Phenom / Athlon II)", "lithography": "45 nm"}

    elif vendor == "GenuineIntel" and fam == 6:
        intel_f6 = {
            60: ("Haswell", "22 nm"),
            61: ("Broadwell", "14 nm"),
            63: ("Haswell-E / EP", "22 nm"),
            69: ("Haswell-ULT", "22 nm"),
            70: ("Crystal Well", "22 nm"),
            71: ("Broadwell-H", "14 nm"),
            78: ("Skylake-U/Y", "14 nm"),
            85: ("Skylake-X / Cascade Lake", "14 nm"),
            94: ("Skylake-S/H", "14 nm"),
            102: ("Cannon Lake", "10 nm"),
            106: ("Ice Lake-SP", "10 nm"),
            125: ("Ice Lake-Y", "10 nm"),
            126: ("Ice Lake-U", "10 nm"),
            140: ("Tiger Lake-U", "10 nm SuperFin"),
            141: ("Tiger Lake-H", "10 nm SuperFin"),
            142: ("Kaby Lake / Whiskey Lake / Comet Lake-U", "14 nm"),
            151: ("Alder Lake-S/HX", "Intel 7 (10 nm)"),
            154: ("Alder Lake-P/H", "Intel 7 (10 nm)"),
            158: ("Kaby Lake / Coffee Lake", "14 nm"),
            165: ("Comet Lake-S/H", "14 nm"),
            167: ("Rocket Lake-S", "14 nm"),
            170: ("Meteor Lake-H/U", "Intel 4 (7 nm)"),
            172: ("Meteor Lake", "Intel 4 (7 nm)"),
            181: ("Arrow Lake-U", "TSMC N3B (3 nm)"),
            183: ("Raptor Lake-S/HX", "Intel 7 (10 nm)"),
            186: ("Raptor Lake-P/H", "Intel 7 (10 nm)"),
            189: ("Lunar Lake", "TSMC N3B (3 nm)"),
            191: ("Raptor Lake-S Refresh", "Intel 7 (10 nm)"),
            197: ("Arrow Lake-H", "TSMC N3B (3 nm)"),
            198: ("Arrow Lake-S", "TSMC N3B (3 nm)"),
        }
        if mod in intel_f6:
            return {"codename": intel_f6[mod][0], "lithography": intel_f6[mod][1]}

    if "ryzen" in name_l:
        if any(x in name_l for x in ("9950", "9900", "9700", "9600", "ai 9", "ai 7")):
            return {"codename": "Zen 5", "lithography": "4 nm"}
        if any(x in name_l for x in ("7950", "7900", "7800", "7700", "7600")):
            return {"codename": "Raphael (Zen 4)", "lithography": "5 nm"}
        if any(x in name_l for x in ("5950", "5900", "5800", "5700", "5600", "5500")):
            return {"codename": "Vermeer / Cezanne (Zen 3)", "lithography": "7 nm"}
        if any(x in name_l for x in ("3950", "3900", "3800", "3700", "3600")):
            return {"codename": "Matisse (Zen 2)", "lithography": "7 nm"}

    return {"codename": f"Family {fam} Model {mod}", "lithography": "N/A"}


def detect_gpu_specs(pci_id: str, name: str) -> dict:
    """
    Infers GPU die codename and lithography node from PCI vendor:device ID or commercial GPU name.
    """
    clean_id = pci_id.split()[0].lower() if pci_id else ""
    name_l = name.lower()

    pci_gpu_db = {
        # AMD RDNA 4 (4 nm TSMC N4P)
        "1002:7550": ("Navi 48 (RDNA 4)", "4 nm (TSMC N4P)"),
        "1002:7590": ("Navi 44 (RDNA 4)", "4 nm (TSMC N4P)"),
        # AMD RDNA 3 / 3.5 (5 nm / 6 nm / 4 nm)
        "1002:744c": ("Navi 31 (RDNA 3)", "5 nm / 6 nm"),
        "1002:747e": ("Navi 32 (RDNA 3)", "5 nm / 6 nm"),
        "1002:7480": ("Navi 33 (RDNA 3)", "6 nm"),
        "1002:15bf": ("Phoenix / Radeon 780M (RDNA 3)", "4 nm"),
        "1002:15c8": ("Phoenix 2 / Radeon 740M (RDNA 3)", "4 nm"),
        "1002:150e": ("Strix Point / Radeon 890M (RDNA 3.5)", "4 nm"),
        # AMD RDNA 2 (7 nm / 6 nm)
        "1002:73bf": ("Navi 21 (RDNA 2)", "7 nm"),
        "1002:73df": ("Navi 22 (RDNA 2)", "7 nm"),
        "1002:73ff": ("Navi 23 (RDNA 2)", "7 nm"),
        "1002:743f": ("Navi 24 (RDNA 2)", "6 nm"),
        "1002:164e": ("Raphael iGPU (RDNA 2)", "6 nm"),
        "1002:1638": ("Cezanne Vega iGPU (GCN 5)", "7 nm"),
        # AMD RDNA 1 & Polaris
        "1002:731f": ("Navi 10 (RDNA 1)", "7 nm"),
        "1002:7340": ("Navi 14 (RDNA 1)", "7 nm"),
        "1002:67df": ("Polaris 20 (GCN 4)", "14 nm"),
        # NVIDIA Blackwell (TSMC 4NP)
        "10de:2b85": ("GB202 (Blackwell)", "4 nm (TSMC 4NP)"),
        "10de:2c02": ("GB203 (Blackwell)", "4 nm (TSMC 4NP)"),
        "10de:2d04": ("GB205 (Blackwell)", "4 nm (TSMC 4NP)"),
        "10de:2d05": ("GB206 (Blackwell)", "4 nm (TSMC 4NP)"),
        # NVIDIA Ada Lovelace (TSMC 4N)
        "10de:2684": ("AD102 (Ada Lovelace)", "4 nm (TSMC 4N)"),
        "10de:2704": ("AD103 (Ada Lovelace)", "4 nm (TSMC 4N)"),
        "10de:2782": ("AD104 (Ada Lovelace)", "4 nm (TSMC 4N)"),
        "10de:2786": ("AD104 (Ada Lovelace)", "4 nm (TSMC 4N)"),
        "10de:2803": ("AD106 (Ada Lovelace)", "4 nm (TSMC 4N)"),
        "10de:2882": ("AD107 (Ada Lovelace)", "4 nm (TSMC 4N)"),
        # NVIDIA Ampere (Samsung 8N)
        "10de:2204": ("GA102 (Ampere)", "8 nm"),
        "10de:2206": ("GA102 (Ampere)", "8 nm"),
        "10de:2484": ("GA104 (Ampere)", "8 nm"),
        "10de:2489": ("GA104 (Ampere)", "8 nm"),
        "10de:2504": ("GA106 (Ampere)", "8 nm"),
        # NVIDIA Turing (TSMC 12FFN)
        "10de:1e07": ("TU102 (Turing)", "12 nm"),
        "10de:1e84": ("TU104 (Turing)", "12 nm"),
        "10de:1f08": ("TU106 (Turing)", "12 nm"),
        "10de:2184": ("TU116 (Turing)", "12 nm"),
        # Intel Arc Alchemist (TSMC N6) & Battlemage (TSMC N5/N4)
        "8086:56a0": ("ACM-G10 (Alchemist)", "6 nm"),
        "8086:56a1": ("ACM-G10 (Alchemist)", "6 nm"),
        "8086:56a5": ("ACM-G11 (Alchemist)", "6 nm"),
        "8086:e20b": ("BMG-G21 (Battlemage)", "5 nm"),
    }

    if clean_id in pci_gpu_db:
        return {"codename": pci_gpu_db[clean_id][0], "lithography": pci_gpu_db[clean_id][1]}

    if "rx 90" in name_l or "navi 4" in name_l:
        return {"codename": "RDNA 4", "lithography": "4 nm (TSMC N4P)"}
    if "rx 7" in name_l or "navi 3" in name_l:
        return {"codename": "RDNA 3", "lithography": "5 nm / 6 nm"}
    if "rx 6" in name_l or "navi 2" in name_l:
        return {"codename": "RDNA 2", "lithography": "7 nm"}
    if "rtx 50" in name_l:
        return {"codename": "Blackwell", "lithography": "4 nm (TSMC 4NP)"}
    if "rtx 40" in name_l:
        return {"codename": "Ada Lovelace", "lithography": "4 nm (TSMC 4N)"}
    if "rtx 30" in name_l:
        return {"codename": "Ampere", "lithography": "8 nm"}
    if "rtx 20" in name_l or "gtx 16" in name_l:
        return {"codename": "Turing", "lithography": "12 nm"}
    if "arc b" in name_l:
        return {"codename": "Battlemage", "lithography": "5 nm"}
    if "arc a" in name_l:
        return {"codename": "Alchemist", "lithography": "6 nm"}

    return {"codename": "N/A", "lithography": "N/A"}


def get_bus_speed_mhz() -> float:
    """
    Returns the reference base clock / bus speed in MHz.
    Modern x86_64 platforms use a 100.0 MHz BCLK; ARM SoCs use a 24.0 MHz reference crystal.
    """
    if is_arm_arch():
        return 24.0
    return 100.0


def resolve_hardware_logo(hardware_name: str, hw_type: str = "cpu", index_path: str = "static/logos/logos_index.json") -> str:
    """
    Returns the PNG path corresponding to the given processor or graphics card.
    """
    with open(index_path, "r", encoding="utf-8") as f:
        index = json.load(f)

    norm = re.sub(r"[^a-z0-9\+\-\s]", " ", hardware_name.lower())
    tokens = set(norm.split())

    best_file = None
    best_score = 0

    for item in index.get(hw_type, []):
        keywords = item["keywords"]
        if all((kw in tokens if len(kw) <= 2 else kw in norm) for kw in keywords):
            score = len(keywords)
            if score > best_score:
                best_score = score
                best_file = item["file"]

    if best_file:
        return f"static/logos/{best_file}"

    return "static/logos/cpu_intel_inside.png" if hw_type == "cpu" else "static/logos/gpu_nvidia_generic.png"


def get_system_environment():
    """Collects OS, kernel, uptime, and runtime environment details for the About tab."""
    uname = platform.uname()
    uptime_str = "N/A"
    raw_up = read_file("/proc/uptime")
    if raw_up:
        try:
            secs = int(float(raw_up.split()[0]))
            days, rem = divmod(secs, 86400)
            hours, rem = divmod(rem, 3600)
            mins, _ = divmod(rem, 60)
            uptime_str = f"{days}d {hours:02d}h {mins:02d}m" if days > 0 else f"{hours:02d}h {mins:02d}m"
        except Exception:
            pass

    gcc_ver = "N/A"
    try:
        out = subprocess.check_output(["gcc", "--version"], text=True)
        gcc_ver = out.splitlines()[0].strip()
    except Exception:
        pass

    desktop = os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or "Headless / Web"
    session_type = os.environ.get("XDG_SESSION_TYPE") or "N/A"

    return {
        "hostname": uname.node,
        "kernel": f"{uname.system} {uname.release}",
        "kernel_build": uname.version.split("#")[1].split()[0] if "#" in uname.version else uname.version[:32],
        "python_version": platform.python_version(),
        "gcc_version": gcc_ver,
        "desktop": f"{desktop} ({session_type})",
        "uptime": uptime_str
    }


def get_static_hardware():
    bus_mhz = get_bus_speed_mhz()

    # Static CPU properties
    cpu = {
        "name": "Unknown",
        "vendor": "Unknown",
        "codename": "Unknown",
        "lithography": "N/A",
        "package": "N/A",
        "family": "N/A",
        "model": "N/A",
        "stepping": "N/A",
        "cores": "N/A",
        "threads": 0,
        "min_mhz": "0.00",
        "max_mhz": "0.00",
        "bus_speed_mhz": bus_mhz,
        "flags": "",
        "bogomips": "N/A",
        "architecture": platform.machine()
    }

    cpuinfo = read_file("/proc/cpuinfo")
    if is_arm_arch():
        if cpuinfo:
            parse_arm_cpu(cpuinfo, cpu)
        else:
            parse_arm_cpu("", cpu)
    else:
        if cpuinfo:
            processors = cpuinfo.strip().split("\n\n")
            cpu["threads"] = len(processors)
            first = processors[0]

            patterns = {
                "name": r"model name\s*:\s*(.+)",
                "vendor": r"vendor_id\s*:\s*(.+)",
                "family": r"cpu family\s*:\s*(.+)",
                "model": r"^model\s*:\s*(.+)",
                "stepping": r"stepping\s*:\s*(.+)",
                "flags": r"flags\s*:\s*(.+)",
                "bogomips": r"bogomips\s*:\s*(.+)",
                "cores": r"cpu cores\s*:\s*(.+)"
            }
            for key, pat in patterns.items():
                m = re.search(pat, first, re.MULTILINE)
                if m:
                    cpu[key] = m.group(1).strip()

        cpu_specs = detect_cpu_specs(cpu["vendor"], cpu["family"], cpu["model"], cpu["name"])
        cpu["codename"] = cpu_specs["codename"]
        cpu["lithography"] = cpu_specs["lithography"]
        cpu["package"] = detect_cpu_package(cpu["vendor"], cpu["family"], cpu["model"], cpu["name"])

    # Read min/max across all cores (handles big.LITTLE clusters where cpu0 != max cluster)
    min_freqs = [
        int(v) for p in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/cpuinfo_min_freq")
        if (v := read_file(p)) and v.isdigit()
    ]
    max_freqs = [
        int(v) for p in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/cpuinfo_max_freq")
        if (v := read_file(p)) and v.isdigit()
    ]
    if min_freqs:
        cpu["min_mhz"] = f"{min(min_freqs)/1000:.2f}"
    if max_freqs:
        cpu["max_mhz"] = f"{max(max_freqs)/1000:.2f}"

    # Cache hierarchy
    caches = []
    base_cache = "/sys/devices/system/cpu/cpu0/cache"
    if os.path.exists(base_cache):
        for idx in sorted(os.listdir(base_cache)):
            if idx.startswith("index"):
                p = os.path.join(base_cache, idx)
                lvl = read_file(f"{p}/level")
                ctype = read_file(f"{p}/type")
                size = read_file(f"{p}/size")
                ways = read_file(f"{p}/ways_of_associativity")
                if lvl and size:
                    caches.append({
                        "level": f"L{lvl} {ctype}",
                        "size": size,
                        "ways": f"{ways}-way" if ways else "N/A"
                    })

    # Motherboard & BIOS (DMI on x86_64, Device Tree fallback on ARM SBCs)
    dmi = "/sys/devices/virtual/dmi/id"
    dt_model = (read_file("/proc/device-tree/model") or "").replace("\x00", "").strip()
    dt_serial = (read_file("/proc/device-tree/serial-number") or "").replace("\x00", "").strip()
    mainboard = {
        "manufacturer": read_file(f"{dmi}/board_vendor") or (cpu["vendor"] if is_arm_arch() else "N/A"),
        "model": read_file(f"{dmi}/board_name") or (dt_model if dt_model else "N/A"),
        "version": read_file(f"{dmi}/board_version") or (f"S/N {dt_serial}" if dt_serial else "N/A"),
        "bios_vendor": read_file(f"{dmi}/bios_vendor") or ("U-Boot / Device Tree" if is_arm_arch() else "N/A"),
        "bios_version": read_file(f"{dmi}/bios_version") or "N/A",
        "bios_date": read_file(f"{dmi}/bios_date") or "N/A",
    }

    # OS Info
    try:
        distro = platform.freedesktop_os_release().get("PRETTY_NAME", "Linux")
    except Exception:
        distro = "Linux"

    return {
        "cpu": cpu,
        "caches": caches,
        "mainboard": mainboard,
        "graphics": get_gpu_details(),
        "storage": get_storage_drives(),
        "os": f"{distro} • Kernel {platform.release()}",
        "system": get_system_environment()
    }


def get_hwmon_sensors():
    """Reads all temperature sensors under /sys/class/hwmon with driver and label metadata."""
    friendly_chips = {
        "k10temp": "AMD CPU (k10temp)",
        "zenpower": "AMD CPU (zenpower)",
        "amdgpu": "AMD Radeon (amdgpu)",
        "nvme": "NVMe SSD",
        "it87": "Mainboard Super I/O",
        "nct6775": "Mainboard Super I/O",
        "acpitz": "ACPI Thermal Zone",
        "soc_thermal": "ARM SoC Thermal",
        "cpu_thermal": "ARM CPU Thermal",
        "gpu_thermal": "ARM GPU Thermal",
        "bigcore0_thermal": "ARM Big Core 0",
        "bigcore1_thermal": "ARM Big Core 1",
        "littlecore_thermal": "ARM Little Cores",
        "center_thermal": "SoC Center Thermal",
        "npu_thermal": "NPU Thermal"
    }

    sensors = []
    for hw_dir in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        chip_raw = read_file(os.path.join(hw_dir, "name")) or os.path.basename(hw_dir)
        chip_name = friendly_chips.get(chip_raw, chip_raw)

        temp_inputs = sorted(
            glob.glob(os.path.join(hw_dir, "temp*_input")),
            key=lambda p: int(re.search(r"temp(\d+)_input", p).group(1))
        )

        for t_path in temp_inputs:
            raw_val = read_file(t_path)
            if not raw_val or not raw_val.lstrip("-").isdigit():
                continue

            temp_c = round(int(raw_val) / 1000.0, 1)
            if temp_c <= -40.0 or temp_c >= 150.0:
                continue

            idx = re.search(r"temp(\d+)_input", t_path).group(1)
            label_path = os.path.join(hw_dir, f"temp{idx}_label")
            crit_path = os.path.join(hw_dir, f"temp{idx}_crit")

            label = read_file(label_path) or f"Temp #{idx}"
            crit_raw = read_file(crit_path)
            crit_c = round(int(crit_raw) / 1000.0, 1) if crit_raw and crit_raw.isdigit() else None

            sensors.append({
                "driver": chip_raw,
                "chip": chip_name,
                "label": label,
                "sensor": f"{chip_name} — {label}",
                "temp": temp_c,
                "crit": crit_c
            })

    priority = {"k10temp": 0, "zenpower": 0, "soc_thermal": 0, "cpu_thermal": 0, "amdgpu": 1, "gpu_thermal": 1, "nvme": 2}
    sensors.sort(key=lambda s: priority.get(s["driver"], 3))
    return sensors


def get_dynamic_telemetry():
    # Per-core clocks
    core_clocks = []
    cpu_dirs = sorted(
        glob.glob("/sys/devices/system/cpu/cpu[0-9]*"),
        key=lambda x: int(re.search(r"cpu(\d+)", x).group(1))
    )

    for cdir in cpu_dirs:
        cid = os.path.basename(cdir).replace("cpu", "Core #")
        freq = read_file(f"{cdir}/cpufreq/scaling_cur_freq")
        if freq and freq.isdigit():
            core_clocks.append({"core": cid, "mhz": round(int(freq) / 1000, 1)})

    # Fallback if cpufreq is unavailable (e.g., virtual machines)
    if not core_clocks:
        cpuinfo = read_file("/proc/cpuinfo") or ""
        matches = re.findall(r"cpu MHz\s*:\s*([\d.]+)", cpuinfo)
        for i, m in enumerate(matches):
            core_clocks.append({"core": f"Core #{i}", "mhz": round(float(m), 1)})

    # Compute current multiplier range & bus speed (across all clusters)
    bus_mhz = get_bus_speed_mhz()
    min_freqs = [
        int(v) for p in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/cpuinfo_min_freq")
        if (v := read_file(p)) and v.isdigit()
    ]
    max_freqs = [
        int(v) for p in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/cpuinfo_max_freq")
        if (v := read_file(p)) and v.isdigit()
    ]
    min_mult = round((min(min_freqs) / 1000.0) / bus_mhz, 1) if min_freqs else None
    max_mult = round((max(max_freqs) / 1000.0) / bus_mhz, 1) if max_freqs else None

    if core_clocks:
        peak_mhz = max(c["mhz"] for c in core_clocks)
        cur_mult = round(peak_mhz / bus_mhz, 1)
        if min_mult is not None and max_mult is not None:
            mult_str = f"x {cur_mult:.1f} ({min_mult:.0f} - {max_mult:.0f})"
        else:
            mult_str = f"x {cur_mult:.1f}"
    else:
        mult_str = "N/A"

    # Dynamic memory usage
    meminfo = read_file("/proc/meminfo") or ""

    def parse_mem(key):
        m = re.search(rf"{key}:\s+(\d+)", meminfo)
        return round(int(m.group(1)) / (1024**2), 2) if m else 0

    mem_total = parse_mem("MemTotal")
    mem_avail = parse_mem("MemAvailable")
    mem_used = round(mem_total - mem_avail, 2)
    swap_total = parse_mem("SwapTotal")

    return {
        "clocks": core_clocks,
        "cpu_clocks_meta": {
            "bus_speed_mhz": bus_mhz,
            "multiplier": mult_str
        },
        "temps": get_hwmon_sensors(),
        "graphics": get_gpu_details(),
        "storage": get_storage_drives(),
        "memory": {
            "total": f"{mem_total:.2f} GB",
            "used": f"{mem_used:.2f} GB",
            "available": f"{mem_avail:.2f} GB",
            "swap": f"{swap_total:.2f} GB",
            "percent": round((mem_used / mem_total) * 100, 1) if mem_total > 0 else 0
        }
    }


def get_eeprom_timings():
    """Reads JEDEC/XMP timings and manufacturer strings directly from SPD EEPROM via decode-dimms."""
    out = ""
    for cmd in (["/usr/bin/decode-dimms"], ["decode-dimms"]):
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
            if out.strip():
                break
        except Exception:
            continue

    if not out.strip():
        return []

    if "---=== Memory Module" in out:
        dimm_blocks = re.split(r"---=== Memory Module #\d+ ===---", out)[1:]
    else:
        dimm_blocks = re.split(r"Decoding EEPROM:\s+", out)[1:]

    results = []
    for block in dimm_blocks:
        mod_man_m = re.search(r"Module Manufacturer\s+(.+)", block)
        dram_man_m = re.search(r"DRAM Manufacturer\s+(.+)", block)
        part_m = re.search(r"Part Number\s+(.+)", block)

        mod_man = mod_man_m.group(1).strip() if mod_man_m else ""
        dram_man = dram_man_m.group(1).strip() if dram_man_m else ""
        part_num = part_m.group(1).strip() if part_m else ""

        full_man = mod_man
        if dram_man and dram_man != "Unknown" and dram_man.lower() not in mod_man.lower():
            full_man = f"{mod_man} ({dram_man})" if mod_man else dram_man

        timings_list = []
        for line in block.splitlines():
            m = re.search(
                r"((?:DDR\d-\d+|\d+\s*(?:MHz|MT/s))(?:\s*\([^)]+\))?)\s+(\d{1,2}-\d{1,2}-\d{1,2}-\d{1,2}(?:-\d{1,2})?)",
                line
            )
            if m:
                timings_list.append({
                    "freq": m.group(1).strip(),
                    "timings": m.group(2).strip()
                })

        xmp_info = ""
        xmp_block = re.search(r"Extreme Memory Profile.*?(?=\n---|\nDecoding EEPROM:|\Z)", block, re.DOTALL)
        if xmp_block:
            xb = xmp_block.group(0)
            spd_m = re.search(r"(DDR\d-\d+|\d+\s*(?:MT/s|MHz))", xb)
            tim_m = re.search(r"(\d{1,2}-\d{1,2}-\d{1,2}-\d{1,2})", xb)
            vlt_m = re.search(r"(\d\.\d+\s*V)", xb)
            parts = [p.group(1) for p in (spd_m, tim_m, vlt_m) if p]
            if parts:
                xmp_info = "XMP: " + " | ".join(parts)

        results.append({
            "manufacturer": full_man,
            "part_number": part_num,
            "profiles": timings_list,
            "xmp": xmp_info
        })

    return results


def parse_spd_slots(use_pkexec=False):
    cmd = ["sudo", "-n", "/usr/sbin/dmidecode", "-t", "17"]
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
    except Exception:
        return None

    eeprom_data = get_eeprom_timings()
    eeprom_idx = 0

    slots = []
    blocks = output.split("Memory Device")
    for block in blocks[1:]:
        def extract(field):
            m = re.search(rf"^\s*{field}:\s*(.+)$", block, re.MULTILINE)
            return m.group(1).strip() if m else "N/A"

        size = extract("Size")
        if "No Module Installed" in size or size == "N/A":
            slots.append({"locator": extract("Locator"), "installed": False})
        else:
            rank_val = extract("Rank")
            rank_str = f"{rank_val}R" if rank_val.isdigit() else rank_val
            if rank_val == "1":
                rank_str = "Single Rank (1Rx8)"
            elif rank_val == "2":
                rank_str = "Dual Rank (2Rx8)"

            eeprom = eeprom_data[eeprom_idx] if eeprom_idx < len(eeprom_data) else {
                "manufacturer": "", "part_number": "", "profiles": [], "xmp": ""
            }
            eeprom_idx += 1

            dmi_man = extract("Manufacturer")
            final_man = eeprom["manufacturer"] if eeprom["manufacturer"] else dmi_man

            dmi_part = extract("Part Number")
            final_part = eeprom["part_number"] if eeprom["part_number"] else dmi_part

            slots.append({
                "installed": True,
                "locator": extract("Locator"),
                "size": size,
                "type": extract("Type"),
                "speed": extract("Speed"),
                "configured_speed": extract("Configured Memory Speed"),
                "voltage": extract("Configured Voltage"),
                "rank": rank_str,
                "data_width": extract("Data Width"),
                "manufacturer": final_man,
                "part_number": final_part,
                "timings": eeprom["profiles"],
                "xmp": eeprom["xmp"]
            })
    return slots


def detect_arm_platform_gpu(card_path: str, driver: str) -> dict:
    """Detects integrated ARM GPUs (Mali / Panfrost / Panthor / Lima / VideoCore) via sysfs/devfreq."""
    dt_compat = (read_file("/proc/device-tree/compatible") or "").replace("\x00", " ")
    mali_model = "Integrated ARM GPU"
    codename = "ARM GPU"
    litho = "SoC Integrated"

    if "rk3588" in dt_compat:
        mali_model = "ARM Mali-G610 MP4"
        codename = "Valhall 3rd Gen (Odin)"
        litho = "8 nm (SoC)"
    elif "rk3568" in dt_compat or "rk3566" in dt_compat:
        mali_model = "ARM Mali-G52 2EE"
        codename = "Bifrost"
        litho = "22 nm (SoC)"
    elif "rk3399" in dt_compat:
        mali_model = "ARM Mali-T860 MP4"
        codename = "Midgard 4th Gen"
        litho = "28 nm (SoC)"
    elif "sun50i-h61" in dt_compat:
        mali_model = "ARM Mali-G31 MP2"
        codename = "Bifrost"
        litho = "28 nm (SoC)"
    elif "amlogic,sm1" in dt_compat or "amlogic,g12" in dt_compat:
        mali_model = "ARM Mali-G31 MP2 / G52"
        codename = "Bifrost"
        litho = "12 nm (SoC)"
    elif "mt8183" in dt_compat:
        mali_model = "ARM Mali-G72 MP3"
        codename = "Bifrost"
        litho = "12 nm (SoC)"
    elif "mt8195" in dt_compat:
        mali_model = "ARM Mali-G57 MC5"
        codename = "Valhall"
        litho = "6 nm (SoC)"
    elif "bcm2712" in dt_compat:
        mali_model = "Broadcom VideoCore VII"
        codename = "V3D 7.1"
        litho = "16 nm (SoC)"
    elif "bcm2711" in dt_compat:
        mali_model = "Broadcom VideoCore VI"
        codename = "V3D 4.2"
        litho = "28 nm (SoC)"

    sclk = "N/A"
    busy = "N/A"
    devfreq_dirs = (
        glob.glob("/sys/class/devfreq/*gpu*")
        + glob.glob("/sys/class/devfreq/*mali*")
        + glob.glob("/sys/class/devfreq/*v3d*")
    )
    if devfreq_dirs:
        cur_hz = read_file(os.path.join(devfreq_dirs[0], "cur_freq"))
        if cur_hz and cur_hz.isdigit():
            sclk = f"{int(cur_hz) // 1_000_000} MHz"
        load_raw = read_file(os.path.join(devfreq_dirs[0], "load"))
        if load_raw and "@" in load_raw:
            busy = f"{load_raw.split('@')[0].strip()} %"

    return {
        "card": os.path.basename(card_path),
        "name": mali_model,
        "codename": codename,
        "lithography": litho,
        "subvendor": "ARM / SoC Integrated",
        "pci_id": "Platform Bus (SoC)",
        "driver": driver,
        "pcie": "AXI / Internal SoC Bus",
        "vram_total": "Shared System RAM",
        "vram_used": "Dynamic (CMA)",
        "vram_pct": 0,
        "vram_vendor": "LPDDR4 / LPDDR5",
        "busy": busy,
        "sclk": sclk,
        "mclk": "Shared",
        "temp": "See SoC Thermal",
        "power": "Shared SoC"
    }


def get_gpu_details():
    gpus = []
    subvendors = {
        "0x1043": "ASUS",
        "0x1458": "Gigabyte",
        "0x1462": "MSI",
        "0x1da2": "Sapphire",
        "0x148c": "PowerColor",
        "0x1682": "XFX",
        "0x1849": "ASRock",
        "0x1002": "AMD (Reference)",
        "0x10de": "NVIDIA"
    }

    lspci_names = {}
    try:
        lspci_out = subprocess.check_output(["lspci", "-nn"], stderr=subprocess.DEVNULL, text=True)
        for line in lspci_out.splitlines():
            if "VGA" in line or "3D" in line or "Display" in line:
                slot = line.split()[0]
                desc = line.split(": ", 1)[1] if ": " in line else line
                lspci_names[f"0000:{slot}"] = desc
    except Exception:
        pass

    for card in sorted(glob.glob("/sys/class/drm/card[0-9]*")):
        dev_path = os.path.join(card, "device")
        if not os.path.exists(dev_path):
            continue

        uevent = read_file(os.path.join(dev_path, "uevent")) or ""
        pci_slot_m = re.search(r"PCI_SLOT_NAME=(.+)", uevent)
        driver_m = re.search(r"DRIVER=(.+)", uevent)
        pci_id_m = re.search(r"PCI_ID=(.+)", uevent)
        subsys_m = re.search(r"PCI_SUBSYS_ID=(.+)", uevent)

        pci_slot = pci_slot_m.group(1).strip() if pci_slot_m else os.path.basename(card)
        driver = driver_m.group(1).strip() if driver_m else "N/A"
        pci_id = pci_id_m.group(1).strip() if pci_id_m else "N/A"

        # Handle integrated ARM platform GPUs (non-PCIe) and skip display-only DRM nodes
        if pci_id == "N/A":
            arm_3d_drivers = ("panfrost", "panthor", "lima", "mali", "mali_kbase", "v3d", "vc4", "etnaviv", "pvrsrvkm")
            if driver.lower() in arm_3d_drivers:
                gpus.append(detect_arm_platform_gpu(card, driver))
            continue

        revision = read_file(os.path.join(dev_path, "revision")) or "N/A"

        subvendor_name = "Unknown"
        if subsys_m:
            sv_hex = "0x" + subsys_m.group(1).split(":")[0].lower()
            subvendor_name = subvendors.get(sv_hex, f"ID {sv_hex}")

        cur_speed = read_file(os.path.join(dev_path, "current_link_speed")) or "N/A"
        cur_width = read_file(os.path.join(dev_path, "current_link_width")) or "N/A"
        max_width = read_file(os.path.join(dev_path, "max_link_width")) or "N/A"
        pcie_str = f"PCIe x{max_width} @ x{cur_width} ({cur_speed})" if cur_width != "N/A" else "N/A"

        vram_total_b = read_file(os.path.join(dev_path, "mem_info_vram_total"))
        vram_used_b = read_file(os.path.join(dev_path, "mem_info_vram_used"))
        vram_vendor = read_file(os.path.join(dev_path, "mem_info_vram_vendor")) or ""

        vram_total_mb = round(int(vram_total_b) / (1024**2)) if vram_total_b and vram_total_b.isdigit() else 0
        vram_used_mb = round(int(vram_used_b) / (1024**2)) if vram_used_b and vram_used_b.isdigit() else 0
        vram_pct = round((vram_used_mb / vram_total_mb) * 100, 1) if vram_total_mb > 0 else 0

        gpu_busy = read_file(os.path.join(dev_path, "gpu_busy_percent"))

        def get_active_dpm(filename):
            content = read_file(os.path.join(dev_path, filename)) or ""
            for line in content.splitlines():
                if "*" in line:
                    m = re.search(r"(\d+\s*Mhz)", line, re.IGNORECASE)
                    if m:
                        return m.group(1)
            return "N/A"

        sclk = get_active_dpm("pp_dpm_sclk")
        mclk = get_active_dpm("pp_dpm_mclk")

        gpu_temp = "N/A"
        gpu_power = "N/A"
        hwmons = glob.glob(os.path.join(dev_path, "hwmon", "hwmon*"))
        if hwmons:
            hw = hwmons[0]
            t_edge = read_file(os.path.join(hw, "temp1_input"))
            if t_edge and t_edge.isdigit():
                gpu_temp = f"{int(t_edge) / 1000:.1f} °C"

            p_avg = read_file(os.path.join(hw, "power1_average")) or read_file(os.path.join(hw, "power1_input"))
            if p_avg and p_avg.isdigit():
                gpu_power = f"{int(p_avg) / 1_000_000:.1f} W"

        gpu_name = lspci_names.get(pci_slot, f"GPU ({pci_id})")
        gpu_specs = detect_gpu_specs(pci_id, gpu_name)

        gpus.append({
            "card": os.path.basename(card),
            "name": gpu_name,
            "codename": gpu_specs["codename"],
            "lithography": gpu_specs["lithography"],
            "subvendor": subvendor_name,
            "pci_id": f"{pci_id} (Rev {revision})",
            "driver": driver,
            "pcie": pcie_str,
            "vram_total": f"{vram_total_mb} MB ({vram_total_mb / 1024:.0f} GB)" if vram_total_mb else "N/A",
            "vram_used": f"{vram_used_mb} MB",
            "vram_pct": vram_pct,
            "vram_vendor": vram_vendor.upper() if vram_vendor else "GDDR6",
            "busy": f"{gpu_busy} %" if gpu_busy else "N/A",
            "sclk": sclk,
            "mclk": mclk,
            "temp": gpu_temp,
            "power": gpu_power
        })

    # Fallback on ARM SBCs if DRM card node uses display driver only, but devfreq GPU exists
    if not gpus and is_arm_arch():
        if glob.glob("/sys/class/devfreq/*gpu*") or glob.glob("/sys/class/devfreq/*mali*"):
            gpus.append(detect_arm_platform_gpu("gpu0", "panfrost / mali"))

    return gpus


def get_storage_drives():
    drives = []
    mounts_map = {}
    try:
        lsblk_out = subprocess.check_output(
            ["lsblk", "-J", "-b", "-o", "NAME,FSTYPE,SIZE,MOUNTPOINT,LABEL"],
            stderr=subprocess.DEVNULL, text=True
        )
        blk_data = json.loads(lsblk_out)
        for dev in blk_data.get("blockdevices", []):
            mounts_map[dev["name"]] = dev.get("children", [])
    except Exception:
        pass

    block_paths = sorted(
        glob.glob("/sys/block/nvme[0-9]*n[0-9]*") +
        glob.glob("/sys/block/sd[a-z]*") +
        glob.glob("/sys/block/mmcblk[0-9]*")
    )

    for bpath in block_paths:
        dev_name = os.path.basename(bpath)
        if "boot" in dev_name or "rpmb" in dev_name:
            continue
        dev_dir = os.path.join(bpath, "device")

        sectors_str = read_file(os.path.join(bpath, "size"))
        total_bytes = int(sectors_str) * 512 if sectors_str and sectors_str.isdigit() else 0
        if total_bytes == 0:
            continue

        size_gb = total_bytes / 1_000_000_000
        size_gib = total_bytes / (1024 ** 3)
        size_str = f"{size_gb:.0f} GB ({size_gib:.1f} GiB)"

        model = read_file(os.path.join(dev_dir, "model")) or read_file(os.path.join(dev_dir, "name")) or dev_name
        serial = read_file(os.path.join(dev_dir, "serial")) or "N/A"
        firmware = read_file(os.path.join(dev_dir, "firmware_rev")) or read_file(os.path.join(dev_dir, "rev")) or "N/A"
        state = read_file(os.path.join(dev_dir, "state")) or "online"

        rotational = read_file(os.path.join(bpath, "queue", "rotational"))
        if dev_name.startswith("nvme"):
            drive_type = "NVMe SSD"
            ctrl_name = re.match(r"(nvme\d+)", dev_name).group(1)
            ctrl_dev = f"/sys/class/nvme/{ctrl_name}/device"
            cur_spd = read_file(os.path.join(ctrl_dev, "current_link_speed"))
            cur_wid = read_file(os.path.join(ctrl_dev, "current_link_width"))
            max_wid = read_file(os.path.join(ctrl_dev, "max_link_width"))
            if cur_spd and cur_wid:
                interface_str = f"NVMe PCIe x{max_wid} @ x{cur_wid} ({cur_spd})"
            else:
                interface_str = "NVMe PCI Express"
            hwmon_glob = os.path.join(ctrl_dev, "hwmon", "hwmon*")
        elif dev_name.startswith("mmcblk"):
            mmc_type = read_file(os.path.join(dev_dir, "type")) or "MMC"
            drive_type = "eMMC Flash" if mmc_type == "MMC" else "SD / microSD Card"
            interface_str = f"SDHCI / {mmc_type} Bus"
            hwmon_glob = os.path.join(dev_dir, "hwmon", "hwmon*")
        else:
            is_hdd = (rotational == "1")
            drive_type = "HDD (Mechanical)" if is_hdd else "SATA / USB SSD"
            transport = "USB" if "/usb" in os.path.realpath(dev_dir) else "SATA / AHCI"
            interface_str = transport
            hwmon_glob = os.path.join(dev_dir, "hwmon", "hwmon*")

        temp_str = "N/A"
        hwmons = glob.glob(hwmon_glob)
        if hwmons:
            t_raw = read_file(os.path.join(hwmons[0], "temp1_input"))
            if t_raw and t_raw.isdigit():
                temp_str = f"{int(t_raw) / 1000:.1f} °C"

        partitions = []
        for child in mounts_map.get(dev_name, []):
            mp = child.get("mountpoint")
            p_size = int(child.get("size") or 0)
            p_gb = p_size / (1024 ** 3)
            used_pct = 0
            used_str = ""
            if mp and os.path.exists(mp):
                try:
                    st = os.statvfs(mp)
                    tot_p = st.f_blocks * st.f_frsize
                    free_p = st.f_bavail * st.f_frsize
                    used_p = tot_p - free_p
                    if tot_p > 0:
                        used_pct = round((used_p / tot_p) * 100, 1)
                        used_str = f"{used_p / (1024**3):.1f} / {tot_p / (1024**3):.1f} GiB ({used_pct}%)"
                except Exception:
                    pass

            partitions.append({
                "name": child.get("name"),
                "fstype": child.get("fstype") or "raw",
                "size": f"{p_gb:.1f} GiB",
                "mountpoint": mp or "(not mounted)",
                "used_str": used_str,
                "used_pct": used_pct
            })

        drives.append({
            "device": f"/dev/{dev_name}",
            "model": model.strip(),
            "type": drive_type,
            "interface": interface_str,
            "size": size_str,
            "serial": serial.strip(),
            "firmware": firmware.strip(),
            "temp": temp_str,
            "state": state.upper(),
            "partitions": partitions
        })

    return drives


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/static")
def api_static():
    return jsonify(get_static_hardware())


@app.route("/api/dynamic")
def api_dynamic():
    return jsonify(get_dynamic_telemetry())


@app.route("/api/spd")
def api_spd():
    slots = parse_spd_slots(use_pkexec=False)
    if slots is None:
        return jsonify({"authorized": False, "slots": []})
    return jsonify({"authorized": True, "slots": slots})


@app.route("/api/spd/unlock", methods=["POST"])
def api_spd_unlock():
    slots = parse_spd_slots(use_pkexec=True)
    if slots is None:
        return jsonify({"authorized": False, "error": "Permission denied or dmidecode not found."})
    return jsonify({"authorized": True, "slots": slots})


def crop_png_inplace(png_path: pathlib.Path, crop: dict) -> None:
    """Crops the captured PNG to the exact #capture-window bounding box."""
    if not crop or not png_path.exists():
        return
    try:
        x = max(0, int(crop.get("x", 0)))
        y = max(0, int(crop.get("y", 0)))
        w = int(crop.get("w", 0))
        h = int(crop.get("h", 0))
        if w <= 10 or h <= 10:
            return

        tmp_crop = png_path.with_name(png_path.stem + "_crop.png")

        # 1. ImageMagick (magick or convert)
        if shutil.which("magick"):
            subprocess.check_call(
                ["magick", str(png_path), "-crop", f"{w}x{h}+{x}+{y}", "+repage", str(tmp_crop)],
                stderr=subprocess.DEVNULL
            )
            tmp_crop.replace(png_path)
            return
        if shutil.which("convert"):
            subprocess.check_call(
                ["convert", str(png_path), "-crop", f"{w}x{h}+{x}+{y}", "+repage", str(tmp_crop)],
                stderr=subprocess.DEVNULL
            )
            tmp_crop.replace(png_path)
            return

        # 2. FFmpeg fallback
        if shutil.which("ffmpeg"):
            subprocess.check_call(
                ["ffmpeg", "-y", "-i", str(png_path), "-vf", f"crop={w}:{h}:{x}:{y}", str(tmp_crop)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            tmp_crop.replace(png_path)
            return

        # 3. Pillow fallback
        from PIL import Image
        with Image.open(png_path) as im:
            im.crop((x, y, x + w, y + h)).save(png_path)
    except Exception:
        pass


def capture_window_native(target_path: pathlib.Path, crop: dict = None) -> bool:
    """
    Captures the active LHWDI window natively on Wayland (Hyprland/grim) or X11,
    and crops strictly to the rendered UI container.
    """
    captured = False

    # 1. Hyprland + grim (Wayland native, exact window geometry)
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("grim") and shutil.which("hyprctl"):
        try:
            active_raw = subprocess.check_output(["hyprctl", "activewindow", "-j"], text=True)
            win_info = json.loads(active_raw)
            at = win_info.get("at")
            size = win_info.get("size")
            if at and size and size[0] > 0 and size[1] > 0:
                geom = f"{at[0]},{at[1]} {size[0]}x{size[1]}"
                subprocess.check_call(["grim", "-g", geom, str(target_path)])
                captured = target_path.exists()
        except Exception:
            pass

    # 2. X11 active window capture fallback (maim / scrot)
    if not captured and os.environ.get("DISPLAY"):
        if shutil.which("maim") and shutil.which("xdotool"):
            try:
                wid = subprocess.check_output(["xdotool", "getactivewindow"], text=True).strip()
                subprocess.check_call(["maim", "-i", wid, str(target_path)])
                captured = target_path.exists()
            except Exception:
                pass
        elif shutil.which("scrot"):
            try:
                subprocess.check_call(["scrot", "-u", str(target_path)])
                captured = target_path.exists()
            except Exception:
                pass

    if captured and crop:
        crop_png_inplace(target_path, crop)

    return captured


@app.route("/api/screenshot", methods=["POST"])
def save_screenshot_to_downloads():
    """Captures the active tab window natively on Linux (Wayland/X11) and saves to ~/Downloads."""
    try:
        payload = request.get_json(silent=True) or {}
        tab_slug = re.sub(r"[^a-z0-9\-]+", "", (payload.get("tab") or "tab").lower()) or "tab"
        crop = payload.get("crop")

        ts_file = datetime.now().strftime("%Y%m%d-%H%M%S")
        hostname = platform.node()
        filename = f"lhwdi-{tab_slug}-{hostname}-{ts_file}.png"
        target_path = get_downloads_dir() / filename

        # Brief delay so any button hover/toast state settles before capture
        time.sleep(0.12)

        if capture_window_native(target_path, crop=crop):
            return jsonify({
                "ok": True,
                "filename": filename,
                "path": str(target_path)
            })

        return jsonify({
            "ok": False,
            "error": "No native screenshot tool found (install 'grim' for Wayland/Hyprland or 'scrot'/'maim' for X11)."
        }), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def start_server():
    app.run(host="127.0.0.1", port=5055, debug=False, use_reloader=False)


def get_downloads_dir():
    """Returns ~/Downloads (creating it if it does not exist)."""
    dl_dir = pathlib.Path.home() / "Downloads"
    dl_dir.mkdir(parents=True, exist_ok=True)
    return dl_dir


def collect_full_snapshot():
    static = get_static_hardware()
    dynamic = get_dynamic_telemetry()
    spd = parse_spd_slots() or []
    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "hostname": platform.node(),
        "static": static,
        "dynamic": dynamic,
        "spd": spd
    }


def build_txt_report(snap):
    cpu = snap["static"]["cpu"]
    mb = snap["static"]["mainboard"]
    mem = snap["dynamic"]["memory"]
    cpu_pwr = snap["dynamic"].get("cpu_power")
    clocks_meta = snap["dynamic"].get("cpu_clocks_meta", {})

    lines = [
        "=========================================================================",
        "  LHWDI - LINUX HARDWARE DETECTION & INFORMATION REPORT",
        "=========================================================================",
        f"Generated : {snap['timestamp']}",
        f"Host      : {snap['hostname']}",
        f"OS        : {snap['static']['os']}",
        "",
        "-------------------------------------------------------------------------",
        "  PROCESSOR (CPU)",
        "-------------------------------------------------------------------------",
        f"Name            : {cpu['name']}",
        f"Code Name       : {cpu.get('codename', 'N/A')}",
        f"Technology      : {cpu.get('lithography', 'N/A')}",
        f"Package         : {cpu.get('package', 'N/A')}",
        f"Vendor          : {cpu['vendor']}",
        f"Architecture    : {cpu['architecture']}",
        f"Family/Model    : {cpu['family']} / {cpu['model']} (Stepping {cpu['stepping']})",
        f"Topology        : {cpu['cores']} Cores / {cpu['threads']} Threads",
        f"Multiplier      : {clocks_meta.get('multiplier', 'N/A')} @ {clocks_meta.get('bus_speed_mhz', 100.0):.1f} MHz Bus",
        f"Frequency Range : {cpu['min_mhz']} MHz - {cpu['max_mhz']} MHz",
        f"BogoMIPS        : {cpu['bogomips']}",
        f"Package Power   : {f'{cpu_pwr} W' if cpu_pwr is not None else 'N/A'}",
        f"Instructions    : {cpu['flags']}",
        "",
        "Real-Time Core Clocks:",
    ]
    for c in snap["dynamic"]["clocks"]:
        lines.append(f"  - {c['core']:<10} : {c['mhz']:.1f} MHz")

    lines += [
        "",
        "-------------------------------------------------------------------------",
        "  CACHE HIERARCHY",
        "-------------------------------------------------------------------------",
    ]
    for cache in snap["static"]["caches"]:
        lines.append(f"  - {cache['level']:<16} : {cache['size']} ({cache['ways']})")

    lines += [
        "",
        "-------------------------------------------------------------------------",
        "  MAINBOARD & BIOS",
        "-------------------------------------------------------------------------",
        f"Manufacturer    : {mb['manufacturer']}",
        f"Model           : {mb['model']} (Rev {mb['version']})",
        f"BIOS Vendor     : {mb['bios_vendor']}",
        f"BIOS Version    : {mb['bios_version']} ({mb['bios_date']})",
        "",
        "-------------------------------------------------------------------------",
        "  MEMORY & SPD MODULES",
        "-------------------------------------------------------------------------",
        f"Total RAM       : {mem['total']} (Used: {mem['used']} / Avail: {mem['available']})",
        f"Swap Total      : {mem['swap']}",
    ]

    for s in snap["spd"]:
        if s.get("installed"):
            lines.append(f"\n  [{s['locator']}] {s['size']} {s['type']} @ {s['configured_speed']} (Max: {s['speed']})")
            lines.append(f"    Manufacturer : {s['manufacturer']} | Part: {s['part_number']}")
            lines.append(f"    Rank / Width : {s['rank']} | {s['data_width']} | Voltage: {s['voltage']}")
            if s.get("xmp"):
                lines.append(f"    {s['xmp']}")
            for t in s.get("timings", []):
                lines.append(f"      * {t['freq']:<24} -> {t['timings']}")
        else:
            lines.append(f"\n  [{s['locator']}] Empty Slot")

    lines += [
        "",
        "-------------------------------------------------------------------------",
        "  GRAPHICS ADAPTERS (GPU)",
        "-------------------------------------------------------------------------",
    ]
    for g in snap["static"]["graphics"]:
        lines += [
            f"Adapter ({g['card']})  : {g['name']}",
            f"GPU Chip / Tech : {g.get('codename', 'N/A')} | {g.get('lithography', 'N/A')}",
            f"Board Partner   : {g['subvendor']} | PCI ID: {g['pci_id']}",
            f"Driver / Bus    : {g['driver']} | {g['pcie']}",
            f"VRAM            : {g['vram_total']} ({g['vram_vendor']}) | Used: {g['vram_used']}",
            f"Clocks / Load   : Core {g['sclk']} | Mem {g['mclk']} | Load {g['busy']}",
            f"Temp / Power    : {g['temp']} | {g['power']}",
            ""
        ]

    lines += [
        "-------------------------------------------------------------------------",
        "  STORAGE DRIVES & S.M.A.R.T.",
        "-------------------------------------------------------------------------",
    ]
    for d in snap["static"].get("storage", []):
        lines += [
            f"Drive ({d['device']})   : {d['model']} [{d['type']}]",
            f"Interface / Cap : {d['interface']} | {d['size']}",
            f"Serial / FW     : {d['serial']} | FW {d['firmware']} | Temp: {d['temp']}",
        ]
        sm = d.get("smart")
        if sm and sm.get("available"):
            lines.append(
                f"S.M.A.R.T.      : {sm['status']} | Life: {sm['health_pct']} | "
                f"TBW: {sm['tbw']} | TBR: {sm['tbr']} | Power-On: {sm['power_on_hours']} ({sm['power_cycles']} cycles)"
            )
        for p in d.get("partitions", []):
            usage = f" -> Used: {p['used_str']}" if p.get("used_str") else ""
            lines.append(f"  * /dev/{p['name']:<10} [{p['fstype']}] {p['size']} @ {p['mountpoint']}{usage}")
        lines.append("")

    lines += [
        "-------------------------------------------------------------------------",
        "  HARDWARE SENSORS (HWMON)",
        "-------------------------------------------------------------------------",
    ]
    sensor_groups = {}
    for t in snap["dynamic"]["temps"]:
        chip = t.get("chip", "Hardware")
        sensor_groups.setdefault(chip, []).append(t)

    for chip, items in sensor_groups.items():
        lines.append(f"\n  [{chip}]")
        for item in items:
            crit_str = f" (Max: {item['crit']} °C)" if item.get("crit") else ""
            lines.append(f"    - {item['label']:<22} : {item['temp']:.1f} °C{crit_str}")

    return "\n".join(lines)


def build_html_report(snap):
    cpu = snap["static"]["cpu"]
    mb = snap["static"]["mainboard"]
    mem = snap["dynamic"]["memory"]
    cpu_pwr = snap["dynamic"].get("cpu_power")
    cpu_pwr_str = f"{cpu_pwr} W" if cpu_pwr is not None else "N/A"
    clocks_meta = snap["dynamic"].get("cpu_clocks_meta", {})

    spd_html = ""
    for s in snap["spd"]:
        if s.get("installed"):
            timings_rows = "".join(
                f"<tr><td>{t['freq']}</td><td><strong>{t['timings']}</strong></td></tr>"
                for t in s.get("timings", [])
            )
            spd_html += f"""
            <div class="card">
                <h3>Slot: {s['locator']} — {s['size']} ({s['type']})</h3>
                <table>
                    <tr><th>Manufacturer</th><td>{s['manufacturer']}</td><th>Part Number</th><td>{s['part_number']}</td></tr>
                    <tr><th>Max Bandwidth</th><td>{s['speed']}</td><th>Active Clock</th><td>{s['configured_speed']}</td></tr>
                    <tr><th>Rank</th><td>{s['rank']}</td><th>Voltage / Width</th><td>{s['voltage']} / {s['data_width']}</td></tr>
                </table>
                {f'<p class="xmp">{s["xmp"]}</p>' if s.get("xmp") else ''}
                {f'<table class="subtable"><tr><th>Profile</th><th>Timings (CL-tRCD-tRP-tRAS)</th></tr>{timings_rows}</table>' if timings_rows else ''}
            </div>"""

    gpu_html = "".join(f"""
        <div class="card">
            <h3>{g['card'].upper()}: {g['name']}</h3>
            <table>
                <tr><th>GPU Chip</th><td>{g.get('codename', 'N/A')}</td><th>Technology</th><td><strong>{g.get('lithography', 'N/A')}</strong></td></tr>
                <tr><th>Board Partner</th><td>{g['subvendor']}</td><th>Device ID</th><td>{g['pci_id']}</td></tr>
                <tr><th>Driver</th><td>{g['driver']}</td><th>Bus Interface</th><td>{g['pcie']}</td></tr>
                <tr><th>VRAM</th><td>{g['vram_total']} ({g['vram_vendor']})</td><th>VRAM Used</th><td>{g['vram_used']} ({g['vram_pct']}%)</td></tr>
                <tr><th>Clocks</th><td>Core: {g['sclk']} / Mem: {g['mclk']}</td><th>Sensors</th><td>{g['temp']} • {g['power']} • Load {g['busy']}</td></tr>
            </table>
        </div>""" for g in snap["static"]["graphics"])

    drives_html = ""
    for d in snap["static"].get("storage", []):
        sm = d.get("smart")
        smart_rows = ""
        if sm and sm.get("available"):
            smart_rows = f"""
                <tr><th>S.M.A.R.T. Status</th><td><strong>{sm['status']}</strong></td><th>Drive Life</th><td>{sm['health_pct']}</td></tr>
                <tr><th>Total Written (TBW)</th><td>{sm['tbw']}</td><th>Total Read (TBR)</th><td>{sm['tbr']}</td></tr>
                <tr><th>Power-On Hours</th><td>{sm['power_on_hours']}</td><th>Power Cycles</th><td>{sm['power_cycles']}</td></tr>
            """

        part_rows = "".join(
            f"<tr><td><strong>/dev/{p['name']}</strong></td><td>{p['fstype']}</td><td>{p['size']}</td>"
            f"<td><code>{p['mountpoint']}</code></td><td>{p.get('used_str') or '-'}</td></tr>"
            for p in d.get("partitions", [])
        )
        part_table = (
            f"<table class='subtable'><tr><th>Partition</th><th>FS</th><th>Size</th><th>Mount</th><th>Usage</th></tr>{part_rows}</table>"
            if part_rows else ""
        )

        drives_html += f"""
        <div class="card">
            <h3>{d['device']} — {d['model']} ({d['type']})</h3>
            <table>
                <tr><th>Interface</th><td>{d['interface']}</td><th>Capacity</th><td>{d['size']}</td></tr>
                <tr><th>Serial Number</th><td>{d['serial']}</td><th>Firmware / Temp</th><td>{d['firmware']} • <strong>{d['temp']}</strong></td></tr>
                {smart_rows}
            </table>
            {part_table}
        </div>"""

    caches_html = "".join(f"<tr><td>{c['level']}</td><td>{c['size']}</td><td>{c['ways']}</td></tr>" for c in snap["static"]["caches"])
    clocks_html = "".join(f"<span class='chip'>{c['core']}: <b>{c['mhz']:.0f} MHz</b></span>" for c in snap["dynamic"]["clocks"])

    sensor_groups = {}
    for t in snap["dynamic"]["temps"]:
        chip = t.get("chip", "Hardware")
        sensor_groups.setdefault(chip, []).append(t)

    sensors_html = ""
    for chip, items in sensor_groups.items():
        rows_list = []
        for item in items:
            crit_txt = f"{item['crit']} °C" if item.get("crit") else "-"
            rows_list.append(
                f"<tr><th>{item['label']}</th>"
                f"<td><strong>{item['temp']:.1f} °C</strong></td>"
                f"<td>{crit_txt}</td></tr>"
            )
        rows = "".join(rows_list)
        sensors_html += f"""
        <div class="card">
            <h3>{chip}</h3>
            <table>
                <tr><th>Sensor</th><th>Temperature</th><th>Critical Limit</th></tr>
                {rows}
            </table>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>LHWDI Validation Report - {snap['hostname']}</title>
<style>
    body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #f4f4f8; color: #222; margin: 0; padding: 24px; }}
    .container {{ max-width: 860px; margin: auto; background: #fff; border: 1px solid #ccc; border-radius: 6px; overflow: hidden; box-shadow: 0 4px 16px rgba(0,0,0,0.08); }}
    .header {{ background: linear-gradient(90deg, #3b1c57, #6d389e); color: #fff; padding: 18px 24px; display: flex; justify-content: space-between; align-items: center; }}
    .header h1 {{ margin: 0; font-size: 20px; }}
    .meta {{ font-size: 12px; opacity: 0.9; text-align: right; }}
    .section {{ padding: 16px 24px; border-bottom: 1px solid #e5e5ea; }}
    h2 {{ margin: 0 0 12px 0; font-size: 15px; color: #5b2c86; border-left: 4px solid #5b2c86; padding-left: 8px; }}
    h3 {{ margin: 0 0 8px 0; font-size: 13px; color: #333; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; }}
    th {{ background: #f8f8fb; color: #555; width: 18%; }}
    .card {{ background: #fafafa; border: 1px solid #e0e0e6; border-radius: 4px; padding: 12px; margin-bottom: 10px; }}
    .subtable {{ margin-top: 8px; }}
    .xmp {{ color: #5b2c86; font-weight: bold; font-size: 12px; margin: 8px 0 4px 0; }}
    .chip {{ display: inline-block; background: #f0f0f5; border: 1px solid #d0d0da; border-radius: 3px; padding: 3px 8px; margin: 2px; font-family: monospace; font-size: 11px; }}
    .flags {{ font-family: monospace; font-size: 11px; color: #444; background: #f8f8fb; padding: 8px; border: 1px solid #ddd; margin-top: 8px; }}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <div>
            <h1>LHWDI - Linux Hardware Detection &amp; Information</h1>
            <div style="font-size:12px; margin-top:4px;">{snap['static']['os']}</div>
        </div>
        <div class="meta">
            <div>Host: <strong>{snap['hostname']}</strong></div>
            <div>{snap['timestamp']}</div>
        </div>
    </div>

    <div class="section">
        <h2>Processor (CPU)</h2>
        <table>
            <tr><th>Name</th><td colspan="3"><strong>{cpu['name']}</strong></td></tr>
            <tr><th>Code Name</th><td>{cpu.get('codename', 'N/A')}</td><th>Technology</th><td><strong>{cpu.get('lithography', 'N/A')}</strong></td></tr>
            <tr><th>Package</th><td>{cpu.get('package', 'N/A')}</td><th>Architecture</th><td>{cpu['architecture']}</td></tr>
            <tr><th>Vendor</th><td>{cpu['vendor']}</td><th>Family / Model</th><td>{cpu['family']} / {cpu['model']} (Stepping {cpu['stepping']})</td></tr>
            <tr><th>Multiplier</th><td>{clocks_meta.get('multiplier', 'N/A')}</td><th>Bus Speed</th><td>{clocks_meta.get('bus_speed_mhz', 100.0):.1f} MHz</td></tr>
            <tr><th>Topology</th><td>{cpu['cores']} Cores / {cpu['threads']} Threads</td><th>Clock Range</th><td>{cpu['min_mhz']} - {cpu['max_mhz']} MHz</td></tr>
            <tr><th>BogoMIPS</th><td>{cpu['bogomips']}</td><th>Package Power</th><td>{cpu_pwr_str}</td></tr>
        </table>
        <div class="flags"><strong>Instructions:</strong> {cpu['flags']}</div>
        <div style="margin-top:8px;">{clocks_html}</div>
    </div>

    <div class="section">
        <h2>Cache Hierarchy</h2>
        <table><tr><th>Level</th><th>Size</th><th>Associativity</th></tr>{caches_html}</table>
    </div>

    <div class="section">
        <h2>Mainboard &amp; BIOS</h2>
        <table>
            <tr><th>Manufacturer</th><td>{mb['manufacturer']}</td><th>Model</th><td>{mb['model']} (Rev {mb['version']})</td></tr>
            <tr><th>BIOS Vendor</th><td>{mb['bios_vendor']}</td><th>Version / Date</th><td>{mb['bios_version']} ({mb['bios_date']})</td></tr>
        </table>
    </div>

    <div class="section">
        <h2>Memory &amp; SPD Modules (Total: {mem['total']})</h2>
        {spd_html or '<p style="font-size:12px; color:#666;">No detailed SPD data available.</p>'}
    </div>

    <div class="section">
        <h2>Graphics Adapters</h2>
        {gpu_html}
    </div>

    <div class="section">
        <h2>Storage Drives &amp; S.M.A.R.T.</h2>
        {drives_html or '<p style="font-size:12px; color:#666;">No storage drives detected.</p>'}
    </div>

    <div class="section" style="border-bottom:none;">
        <h2>Hardware Sensors (HWMON)</h2>
        {sensors_html or '<p style="font-size:12px; color:#666;">No HWMON sensors found.</p>'}
    </div>

</div>
</body>
</html>"""


@app.route("/api/export/save/<fmt>", methods=["POST"])
def save_report_to_downloads(fmt):
    if fmt not in ("txt", "html"):
        return jsonify({"ok": False, "error": "Invalid export format."}), 400

    try:
        snap = collect_full_snapshot()
        ts_file = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"lhwdi-{snap['hostname']}-{ts_file}.{fmt}"
        target_path = get_downloads_dir() / filename

        content = build_txt_report(snap) if fmt == "txt" else build_html_report(snap)
        target_path.write_text(content, encoding="utf-8")

        return jsonify({
            "ok": True,
            "filename": filename,
            "path": str(target_path)
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/export/<fmt>", methods=["GET"])
def download_report_browser(fmt):
    if fmt not in ("txt", "html"):
        return "Invalid export format", 400
    snap = collect_full_snapshot()
    content = build_txt_report(snap) if fmt == "txt" else build_html_report(snap)
    mime = "text/plain" if fmt == "txt" else "text/html"
    filename = f"lhwdi-{snap['hostname']}.{fmt}"
    return Response(
        content,
        mimetype=mime,
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )


_BENCH_C_SOURCE = r"""
#include <stdint.h>
#include <time.h>

static inline uint64_t now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

uint64_t run_native_workload(double duration_sec) {
    uint64_t start = now_ns();
    uint64_t limit = start + (uint64_t)(duration_sec * 1e9);
    uint64_t ops = 0;

    // 4 independent FPU and ALU accumulators to leverage superscalar ILP
    double f1 = 1.000001, f2 = 1.000002, f3 = 1.000003, f4 = 1.000004;
    uint64_t i1 = 0x9E3779B97F4A7C15ULL;
    uint64_t i2 = 0xBF58476D1CE4E5B9ULL;

    while (now_ns() < limit) {
        for (int k = 0; k < 4096; k++) {
            f1 = f1 * 1.000000119 + 0.0000003;
            f2 = f2 * 0.999999881 + 0.0000007;
            f3 = f3 * 1.000000238 - 0.0000002;
            f4 = f4 * 0.999999762 + 0.0000005;

            i1 ^= (i1 << 13);
            i1 ^= (i1 >> 7);
            i1 ^= (i1 << 17);

            i2 = i2 * 6364136223846793005ULL + 1442695040888963407ULL;
        }
        // Prevent dead-code elimination (DCE) while keeping values bounded
        if (f1 > 1e6) f1 *= 1e-6;
        if (f2 < 1e-6) f2 += 1.0;
        if (f3 > 1e6) f3 *= 1e-6;
        if (f4 < 1e-6) f4 += 1.0;

        ops += 4096;
    }

    // Prevent compiler from optimizing away the computation loop
    volatile double sink_f = f1 + f2 + f3 + f4;
    volatile uint64_t sink_i = i1 ^ i2;
    (void)sink_f;
    (void)sink_i;

    return ops;
}
"""

_BENCH_LIB = None


def _get_native_bench_lib():
    global _BENCH_LIB
    if _BENCH_LIB is not None:
        return _BENCH_LIB

    so_path = "/tmp/lhwdi_native_bench.so"
    c_path = "/tmp/lhwdi_native_bench.c"

    if not os.path.exists(so_path):
        with open(c_path, "w") as f:
            f.write(_BENCH_C_SOURCE)
        subprocess.check_call([
            "gcc", "-O3", "-march=native", "-shared", "-fPIC",
            c_path, "-o", so_path
        ])

    lib = ctypes.CDLL(so_path)
    lib.run_native_workload.argtypes = [ctypes.c_double]
    lib.run_native_workload.restype = ctypes.c_uint64
    _BENCH_LIB = lib
    return lib


def run_cpu_benchmark(duration_per_stage=2.5):
    lib = _get_native_bench_lib()
    threads_count = os.cpu_count() or 1

    # Calibrated scaling factor (~4.8 GHz Zen 3 single thread scores ~650 pts)
    SCALE_DIVISOR = 797_000.0

    # 1. Single-Thread Test (direct C call releases Python GIL)
    t0 = time.perf_counter()
    ops_st = lib.run_native_workload(duration_per_stage)
    elapsed_st = max(time.perf_counter() - t0, 0.001)
    st_score = round((ops_st / elapsed_st) / SCALE_DIVISOR, 1)

    # 2. Multi-Thread Test (native OS threads running C workload concurrently without GIL)
    results = [0] * threads_count
    barrier = threading.Barrier(threads_count)

    def worker(idx):
        barrier.wait()  # Ensure all hardware threads start simultaneously
        results[idx] = lib.run_native_workload(duration_per_stage)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(threads_count)]
    t1 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed_mt = max(time.perf_counter() - t1, 0.001)

    ops_mt = sum(results)
    mt_score = round((ops_mt / elapsed_mt) / SCALE_DIVISOR, 1)
    ratio = round(mt_score / st_score, 2) if st_score > 0 else 1.0

    return {
        "single_thread": st_score,
        "multi_thread": mt_score,
        "ratio": f"{ratio:.2f}x",
        "threads_used": threads_count,
        "duration": round(elapsed_st + elapsed_mt, 2)
    }


@app.route("/api/bench", methods=["POST"])
def api_bench():
    try:
        result = run_cpu_benchmark(duration_per_stage=2.5)
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    try:
        import webview
        t = threading.Thread(target=start_server, daemon=True)
        t.start()
        webview.create_window(
            "LHWDI - Linux Hardware Detection & Information",
            "http://127.0.0.1:5055",
            width=540,
            height=640,
            resizable=False
        )
        webview.start()
    except ImportError:
        print("pywebview is not installed. Opening in default web browser...")
        threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:5055")).start()
        start_server()
