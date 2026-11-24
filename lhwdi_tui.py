#!/usr/bin/env python3
"""
LHWDI TUI - Linux Hardware Detection & Information (Console / Curses Edition)
Styled after CPU-X & HWiNFO DOS, reusing core telemetry from lhwdi.py.
"""

import curses
import os
import sys
import time
import threading
from datetime import datetime

# Importa toda a engine de detecção, relatórios e benchmark C do lhwdi.py
import lhwdi as backend


TABS = [
    "CPU", "Caches", "Mainboard", "Memory", "SPD",
    "Graphics", "Drives", "Sensors", "Bench", "About"
]

BENCH_REFS = [
    ("AMD Ryzen 9 5900XT (16C/32T)", 650.0, 14750.0),
    ("AMD Ryzen 9 5950X (16C/32T)", 655.0, 14900.0),
    ("AMD Ryzen 9 5900X (12C/24T)", 648.0, 11850.0),
    ("AMD Ryzen 5 5600X (6C/12T)", 565.0, 5850.0),
    ("AMD Ryzen 5 3600 (6C/12T)", 495.0, 4050.0),
    ("AMD Ryzen 7 7700X (8C/16T)", 765.0, 12600.0),
    ("Intel Core i7-13700K (16C/24T)", 820.0, 17100.0),
]


class LHWDIConsoleApp:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.current_tab = 0
        self.dos_theme = True  # True = HWiNFO DOS Blue/Cyan, False = LHWDI Dark/Magenta

        # Dados estáticos e dinâmicos
        self.static_data = backend.get_static_hardware()
        self.dynamic_data = backend.get_dynamic_telemetry()
        self.spd_slots = backend.parse_spd_slots(use_pkexec=False)

        # Seletores e scroll por aba
        self.spd_idx = 0
        self.gpu_idx = 0
        self.drive_idx = 0
        self.bench_ref_idx = 0
        self.scroll_offset = 0

        # Estado do Benchmark
        self.bench_running = False
        self.bench_result = None
        self.bench_status = "Ready (~5s) — Press [B] to run"

        # Toast / Mensagem de status
        self.toast_msg = ""
        self.toast_expire = 0.0
        self.toast_is_err = False

        # Controle de atualização
        self.last_poll = 0.0
        self.running = True

        self.init_colors()

    def init_colors(self):
        curses.start_color()
        curses.use_default_colors()

        if self.dos_theme:
            # Estilo HWiNFO DOS Clássico (Fundo Azul Marinho, Caixas Ciano/Branco, Destaque Amarelo)
            bg = curses.COLOR_BLUE
            curses.init_pair(1, curses.COLOR_WHITE, bg)               # Texto padrão
            curses.init_pair(2, curses.COLOR_WHITE, curses.COLOR_CYAN) # Barra de título / Aba ativa
            curses.init_pair(3, curses.COLOR_YELLOW, bg)              # Títulos de seção (Legend) & Destaques
            curses.init_pair(4, curses.COLOR_CYAN, bg)                # Rótulos (Labels) & Bordas
            curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_WHITE) # Campo de valor (Field Box)
            curses.init_pair(6, curses.COLOR_GREEN, bg)               # Temperatura OK / Barra
            curses.init_pair(7, curses.COLOR_YELLOW, bg)              # Temperatura Morna
            curses.init_pair(8, curses.COLOR_RED, bg)                 # Temperatura Crítica / Erro
            curses.init_pair(9, curses.COLOR_BLACK, curses.COLOR_CYAN) # Rodapé / Hotkeys
        else:
            # Estilo LHWDI Dark Moderno (Fundo Preto/Cinza, Roxo/Magenta + Ciano)
            bg = curses.COLOR_BLACK
            curses.init_pair(1, curses.COLOR_WHITE, bg)
            curses.init_pair(2, curses.COLOR_WHITE, curses.COLOR_MAGENTA)
            curses.init_pair(3, curses.COLOR_MAGENTA, bg)
            curses.init_pair(4, curses.COLOR_CYAN, bg)
            curses.init_pair(5, curses.COLOR_CYAN, bg)
            curses.init_pair(6, curses.COLOR_GREEN, bg)
            curses.init_pair(7, curses.COLOR_YELLOW, bg)
            curses.init_pair(8, curses.COLOR_RED, bg)
            curses.init_pair(9, curses.COLOR_WHITE, curses.COLOR_MAGENTA)

        self.stdscr.bkgd(' ', curses.color_pair(1))

    def show_toast(self, msg: str, is_error: bool = False, duration: float = 3.5):
        self.toast_msg = msg
        self.toast_is_err = is_error
        self.toast_expire = time.time() + duration

    def safe_addstr(self, y: int, x: int, text: str, attr=0, max_w: int = None):
        h, w = self.stdscr.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        limit = w - x if max_w is None else min(max_w, w - x)
        if limit <= 0:
            return
        try:
            self.stdscr.addnstr(y, x, text, limit, attr)
        except curses.error:
            pass

    def draw_box(self, y: int, x: int, h: int, w: int, title: str = ""):
        max_h, max_w = self.stdscr.getmaxyx()
        if h < 2 or w < 4 or y >= max_h or x >= max_w:
            return
        w = min(w, max_w - x)
        h = min(h, max_h - y)

        border_attr = curses.color_pair(4)
        title_attr = curses.color_pair(3) | curses.A_BOLD

        self.safe_addstr(y, x, "┌" + "─" * (w - 2) + "┐", border_attr)
        for row in range(1, h - 1):
            self.safe_addstr(y + row, x, "│", border_attr)
            self.safe_addstr(y + row, x + w - 1, "│", border_attr)
        self.safe_addstr(y + h - 1, x, "└" + "─" * (w - 2) + "┘", border_attr)

        if title and w > 6:
            t_str = f" {title[:w - 6]} "
            self.safe_addstr(y, x + 2, t_str, title_attr)

    def draw_field(self, y: int, x: int, label: str, value: str, label_w: int, total_w: int, val_attr=None):
        if total_w <= label_w + 2:
            return
        self.safe_addstr(y, x, f"{label:<{label_w}}", curses.color_pair(4))
        val_w = total_w - label_w - 1
        val_str = str(value) if value is not None else "-"
        if len(val_str) > val_w - 2:
            val_str = val_str[:max(1, val_w - 3)] + "…"
        padded = f" {val_str:<{max(1, val_w - 2)}} "
        attr = val_attr if val_attr is not None else curses.color_pair(5)
        self.safe_addstr(y, x + label_w + 1, padded, attr, max_w=val_w)

    def draw_progress_bar(self, y: int, x: int, width: int, pct: float, label: str = ""):
        if width < 10:
            return
        pct = max(0.0, min(100.0, float(pct)))
        bar_w = width - 8
        filled = int(round((pct / 100.0) * bar_w))
        bar = "█" * filled + "░" * (bar_w - filled)
        color = curses.color_pair(6) if pct < 70 else (curses.color_pair(7) if pct < 88 else curses.color_pair(8))
        self.safe_addstr(y, x, f"[{bar}] {pct:5.1f}%", color | curses.A_BOLD)
        if label:
            self.safe_addstr(y, x + width + 2, label, curses.color_pair(1))

    # -------------------------------------------------------------------------
    # HEADER & FOOTER
    # -------------------------------------------------------------------------
    def draw_header_and_tabs(self):
        _, w = self.stdscr.getmaxyx()
        title = " LHWDI - Linux Hardware Detection & Information (TUI) "
        theme_tag = "[DOS-Blue]" if self.dos_theme else "[LHWDI-Dark]"
        bar = f"{title:<{max(1, w - len(theme_tag) - 1)}}{theme_tag} "
        self.safe_addstr(0, 0, bar, curses.color_pair(2) | curses.A_BOLD)

        # Barra de Abas estilo CPU-X
        x = 1
        for idx, name in enumerate(TABS):
            tag = f" {name} "
            if x + len(tag) >= w - 1:
                break
            if idx == self.current_tab:
                attr = curses.color_pair(2) | curses.A_BOLD
            else:
                attr = curses.color_pair(4)
            self.safe_addstr(1, x, tag, attr)
            x += len(tag) + 1

        self.safe_addstr(2, 0, "═" * w, curses.color_pair(4))

    def draw_footer(self):
        h, w = self.stdscr.getmaxyx()
        os_str = self.static_data.get("os", "Linux")
        keys = "[←/→]Tab [↑/↓]Select/Scroll [T]TXT [H]HTML [C]Theme [Q]Quit"
        if self.current_tab == 8:
            keys = "[B]Run Bench [↑/↓]Ref CPU [←/→]Tab [T]TXT [H]HTML [Q]Quit"
        elif self.current_tab == 4 and self.spd_slots is None:
            keys = "[U]Unlock SPD [←/→]Tab [T]TXT [H]HTML [C]Theme [Q]Quit"

        line_y = h - 2
        self.safe_addstr(line_y, 0, "─" * w, curses.color_pair(4))
        status_line = f" {os_str} │ {keys}"
        self.safe_addstr(h - 1, 0, f"{status_line:<{w}}", curses.color_pair(9) | curses.A_BOLD)

        # Toast flutuante sobre o rodapé
        if self.toast_msg and time.time() < self.toast_expire:
            t_attr = (curses.color_pair(8) if self.toast_is_err else curses.color_pair(6)) | curses.A_BOLD | curses.A_REVERSE
            msg = f" {self.toast_msg} "
            tx = max(1, (w - len(msg)) // 2)
            self.safe_addstr(h - 3, tx, msg, t_attr)

    # -------------------------------------------------------------------------
    # ABAS DE CONTEÚDO
    # -------------------------------------------------------------------------
    def draw_tab_cpu(self, top: int, bottom: int, w: int):
        cpu = self.static_data["cpu"]
        meta = self.dynamic_data.get("cpu_clocks_meta", {})
        clocks = self.dynamic_data.get("clocks", [])

        # Caixa 1: Processor
        self.draw_box(top, 1, 9, w - 2, "Processor")
        inner_w = w - 6
        col_w = (inner_w - 2) // 2

        self.draw_field(top + 1, 3, "Name", cpu["name"], 13, inner_w, curses.color_pair(5) | curses.A_BOLD)
        self.draw_field(top + 2, 3, "Code Name", cpu.get("codename", "-"), 13, col_w)
        self.draw_field(top + 2, 3 + col_w + 2, "Technology", cpu.get("lithography", "-"), 13, col_w, curses.color_pair(3) | curses.A_BOLD)

        self.draw_field(top + 3, 3, "Package", cpu.get("package", "-"), 13, inner_w)
        self.draw_field(top + 4, 3, "Vendor", cpu["vendor"], 13, col_w)
        self.draw_field(top + 4, 3 + col_w + 2, "Architecture", cpu["architecture"], 13, col_w)

        self.draw_field(top + 5, 3, "Family/Model", f"{cpu['family']} / {cpu['model']}", 13, col_w)
        self.draw_field(top + 5, 3 + col_w + 2, "Stepping", cpu["stepping"], 13, col_w)

        flags_txt = cpu.get("flags", "-")
        self.draw_field(top + 6, 3, "Instructions", flags_txt, 13, inner_w)
        self.safe_addstr(top + 7, 3, f"Total Flags : {len(flags_txt.split())} extensions supported", curses.color_pair(1))

        # Caixa 2: Clocks & Topology (lado a lado)
        mid_y = top + 9
        half_box = (w - 3) // 2
        self.draw_box(mid_y, 1, 5, half_box, "Clocks")
        self.draw_field(mid_y + 1, 3, "Multiplier", meta.get("multiplier", "-"), 11, half_box - 4, curses.color_pair(3) | curses.A_BOLD)
        self.draw_field(mid_y + 2, 3, "Bus Speed", f"{meta.get('bus_speed_mhz', 100.0):.1f} MHz", 11, half_box - 4)
        self.draw_field(mid_y + 3, 3, "Min / Max", f"{float(cpu['min_mhz']):.0f} - {float(cpu['max_mhz']):.0f} MHz", 11, half_box - 4)

        right_x = 1 + half_box + 1
        right_w = w - right_x - 1
        self.draw_box(mid_y, right_x, 5, right_w, "Topology")
        self.draw_field(mid_y + 1, right_x + 2, "Cores", cpu["cores"], 10, right_w - 4)
        self.draw_field(mid_y + 2, right_x + 2, "Threads", cpu["threads"], 10, right_w - 4)
        self.draw_field(mid_y + 3, right_x + 2, "BogoMIPS", cpu["bogomips"], 10, right_w - 4)

        # Caixa 3: Real-Time Core Clocks
        clk_y = mid_y + 5
        clk_h = bottom - clk_y
        if clk_h >= 4:
            self.draw_box(clk_y, 1, clk_h, w - 2, f"Real-Time Core Clocks ({len(clocks)} Threads) [↑/↓ Scroll]")
            cols = 4 if w >= 96 else (3 if w >= 72 else 2)
            rows_avail = clk_h - 2
            per_page = cols * rows_avail
            max_scroll = max(0, len(clocks) - per_page)
            self.scroll_offset = min(self.scroll_offset, max_scroll)

            cell_w = (w - 6) // cols
            visible = clocks[self.scroll_offset:self.scroll_offset + per_page]
            for idx, item in enumerate(visible):
                r = idx // cols
                c = idx % cols
                cx = 3 + c * cell_w
                cy = clk_y + 1 + r
                lbl = f"{item['core']:<9}: {item['mhz']:6.1f} MHz"
                self.safe_addstr(cy, cx, lbl, curses.color_pair(1), max_w=cell_w - 1)

    def draw_tab_caches(self, top: int, bottom: int, w: int):
        caches = self.static_data.get("caches", [])
        box_h = max(6, min(bottom - top, len(caches) * 2 + 4))
        self.draw_box(top, 1, box_h, w - 2, "CPU Cache Hierarchy")
        if not caches:
            self.safe_addstr(top + 2, 3, "No cache topology exposed in /sys/devices/system/cpu.", curses.color_pair(1))
            return
        for i, c in enumerate(caches):
            y = top + 2 + i * 2
            if y >= top + box_h - 1:
                break
            self.draw_field(y, 3, c["level"], f"{c['size']}  ({c['ways']})", 18, w - 6)

    def draw_tab_mainboard(self, top: int, bottom: int, w: int):
        mb = self.static_data["mainboard"]
        self.draw_box(top, 1, 6, w - 2, "Motherboard")
        self.draw_field(top + 1, 3, "Manufacturer", mb["manufacturer"], 15, w - 6)
        self.draw_field(top + 2, 3, "Model", mb["model"], 15, w - 6, curses.color_pair(5) | curses.A_BOLD)
        self.draw_field(top + 3, 3, "Version", mb["version"], 15, w - 6)

        self.draw_box(top + 7, 1, 6, w - 2, "BIOS / UEFI")
        self.draw_field(top + 8, 3, "Brand", mb["bios_vendor"], 15, w - 6)
        self.draw_field(top + 9, 3, "Version", mb["bios_version"], 15, w - 6)
        self.draw_field(top + 10, 3, "Date", mb["bios_date"], 15, w - 6)

    def draw_tab_memory(self, top: int, bottom: int, w: int):
        mem = self.dynamic_data["memory"]
        self.draw_box(top, 1, 10, w - 2, "System RAM Usage")
        self.draw_field(top + 1, 3, "Total RAM", mem["total"], 16, w - 6, curses.color_pair(5) | curses.A_BOLD)
        self.draw_field(top + 2, 3, "Used RAM", f"{mem['used']} ({mem['percent']}%)", 16, w - 6)
        self.draw_field(top + 3, 3, "Available RAM", mem["available"], 16, w - 6)
        self.draw_field(top + 4, 3, "Swap Total", mem["swap"], 16, w - 6)

        self.safe_addstr(top + 6, 3, "Memory Utilization:", curses.color_pair(4))
        self.draw_progress_bar(top + 7, 3, min(60, w - 12), mem["percent"])

    def draw_tab_spd(self, top: int, bottom: int, w: int):
        if self.spd_slots is None:
            self.draw_box(top, 1, 7, w - 2, "Memory Slot Selection (SPD / DMI)")
            self.safe_addstr(top + 2, 3, "Reading via dmidecode is not authorized without root/sudo.", curses.color_pair(7) | curses.A_BOLD)
            self.safe_addstr(top + 4, 3, "Press [U] to attempt unlocking SPD modules via sudo -n dmidecode.", curses.color_pair(1))
            return

        if not self.spd_slots:
            self.draw_box(top, 1, 5, w - 2, "Memory Slots")
            self.safe_addstr(top + 2, 3, "No DIMM slots reported.", curses.color_pair(1))
            return

        self.spd_idx %= len(self.spd_slots)
        slot = self.spd_slots[self.spd_idx]

        # Seletor de slot no topo
        slot_tabs = []
        for i, s in enumerate(self.spd_slots):
            tag = f"[{i+1}:{s['locator']}]"
            slot_tabs.append(f"*{tag}*" if i == self.spd_idx else tag)

        self.draw_box(top, 1, 9, w - 2, f"Memory Slot ({self.spd_idx + 1}/{len(self.spd_slots)}) [↑/↓ Change Slot]")
        self.safe_addstr(top + 1, 3, "Slots: " + "  ".join(slot_tabs), curses.color_pair(3) | curses.A_BOLD, max_w=w - 6)

        inner_w = w - 6
        col_w = (inner_w - 2) // 2

        if not slot.get("installed"):
            self.safe_addstr(top + 4, 3, f"{slot.get('locator', 'Slot')}: Empty Slot (No Module Installed)", curses.color_pair(1))
            return

        self.draw_field(top + 3, 3, "Module Size", f"{slot['size']} ({slot['type']})", 14, col_w)
        self.draw_field(top + 3, 3 + col_w + 2, "Max Bandwidth", slot["speed"], 14, col_w)

        self.draw_field(top + 4, 3, "Ranks", slot["rank"], 14, col_w)
        self.draw_field(top + 4, 3 + col_w + 2, "Active Clock", slot["configured_speed"], 14, col_w, curses.color_pair(3) | curses.A_BOLD)

        self.draw_field(top + 5, 3, "Voltage", slot["voltage"], 14, col_w)
        self.draw_field(top + 5, 3 + col_w + 2, "Data Width", slot["data_width"], 14, col_w)

        self.draw_field(top + 6, 3, "Manufacturer", slot["manufacturer"], 14, inner_w)
        self.draw_field(top + 7, 3, "Part Number", slot["part_number"], 14, inner_w)

        # Tabela de Timings JEDEC / XMP
        t_y = top + 9
        t_h = bottom - t_y
        if t_h >= 4:
            self.draw_box(t_y, 1, t_h, w - 2, "Timings Table (JEDEC / XMP EEPROM)")
            row = t_y + 1
            if slot.get("xmp"):
                self.safe_addstr(row, 3, slot["xmp"], curses.color_pair(3) | curses.A_BOLD, max_w=w - 6)
                row += 1
            timings = slot.get("timings", [])
            if timings:
                for t in timings:
                    if row >= t_y + t_h - 1:
                        break
                    line = f"{t['freq']:<32} ->  {t['timings']}"
                    self.safe_addstr(row, 3, line, curses.color_pair(1), max_w=w - 6)
                    row += 1
            else:
                self.safe_addstr(row, 3, "Load ee1004 / i2c-piix4 kernel modules to decode EEPROM timings.", curses.color_pair(4))

    def draw_tab_graphics(self, top: int, bottom: int, w: int):
        gpus = self.dynamic_data.get("graphics") or self.static_data.get("graphics", [])
        if not gpus:
            self.draw_box(top, 1, 5, w - 2, "Graphics Adapters")
            self.safe_addstr(top + 2, 3, "No DRM/PCI graphics adapters detected.", curses.color_pair(1))
            return

        self.gpu_idx %= len(gpus)
        g = gpus[self.gpu_idx]

        self.draw_box(top, 1, 8, w - 2, f"Display Adapter ({self.gpu_idx + 1}/{len(gpus)}: {g['card']}) [↑/↓ Switch GPU]")
        inner_w = w - 6
        col_w = (inner_w - 2) // 2

        self.draw_field(top + 1, 3, "GPU Name", g["name"], 14, inner_w, curses.color_pair(5) | curses.A_BOLD)
        self.draw_field(top + 2, 3, "Board Partner", g["subvendor"], 14, col_w)
        self.draw_field(top + 2, 3 + col_w + 2, "Device ID", g["pci_id"], 14, col_w)

        self.draw_field(top + 3, 3, "GPU Chip", g.get("codename", "-"), 14, col_w)
        self.draw_field(top + 3, 3 + col_w + 2, "Technology", g.get("lithography", "-"), 14, col_w, curses.color_pair(3) | curses.A_BOLD)

        self.draw_field(top + 4, 3, "Kernel Driver", g["driver"], 14, col_w)
        self.draw_field(top + 4, 3 + col_w + 2, "Bus Interface", g["pcie"], 14, col_w)

        # Clocks & Sensors
        mid_y = top + 8
        half_box = (w - 3) // 2
        self.draw_box(mid_y, 1, 4, half_box, "Clocks (Real-Time)")
        self.draw_field(mid_y + 1, 3, "GPU Core", g["sclk"], 10, half_box - 4, curses.color_pair(3) | curses.A_BOLD)
        self.draw_field(mid_y + 2, 3, "Memory", g["mclk"], 10, half_box - 4)

        right_x = 1 + half_box + 1
        right_w = w - right_x - 1
        self.draw_box(mid_y, right_x, 4, right_w, "Sensors & Load")
        self.draw_field(mid_y + 1, right_x + 2, "GPU Load", g["busy"], 12, right_w - 4)
        self.draw_field(mid_y + 2, right_x + 2, "Temp/Power", f"{g['temp']} • {g['power']}", 12, right_w - 4)

        # VRAM
        v_y = mid_y + 4
        if bottom - v_y >= 5:
            self.draw_box(v_y, 1, 6, w - 2, "Memory (VRAM)")
            self.draw_field(v_y + 1, 3, "Size", g["vram_total"], 14, col_w)
            self.draw_field(v_y + 1, 3 + col_w + 2, "Type/Vendor", g["vram_vendor"], 14, col_w)
            self.draw_field(v_y + 2, 3, "Used VRAM", f"{g['vram_used']} ({g['vram_pct']}%)", 14, inner_w)
            self.draw_progress_bar(v_y + 4, 3, min(55, w - 12), g["vram_pct"])

    def draw_tab_drives(self, top: int, bottom: int, w: int):
        drives = self.dynamic_data.get("storage") or self.static_data.get("storage", [])
        if not drives:
            self.draw_box(top, 1, 5, w - 2, "Storage Devices")
            self.safe_addstr(top + 2, 3, "No block storage devices detected.", curses.color_pair(1))
            return

        self.drive_idx %= len(drives)
        d = drives[self.drive_idx]

        self.draw_box(top, 1, 8, w - 2, f"Storage Device ({self.drive_idx + 1}/{len(drives)}: {d['device']}) [↑/↓ Switch Drive]")
        inner_w = w - 6
        col_w = (inner_w - 2) // 2

        self.draw_field(top + 1, 3, "Model", d["model"], 14, inner_w, curses.color_pair(5) | curses.A_BOLD)
        self.draw_field(top + 2, 3, "Type", d["type"], 14, col_w)
        self.draw_field(top + 2, 3 + col_w + 2, "Capacity", d["size"], 14, col_w)

        self.draw_field(top + 3, 3, "Interface", d["interface"], 14, inner_w)
        self.draw_field(top + 4, 3, "Firmware", d["firmware"], 14, col_w)
        self.draw_field(top + 4, 3 + col_w + 2, "Temperature", d["temp"], 14, col_w, curses.color_pair(3) | curses.A_BOLD)

        self.draw_field(top + 5, 3, "Serial Number", d["serial"], 14, col_w)
        self.draw_field(top + 5, 3 + col_w + 2, "Node / State", f"{d['device']} ({d['state']})", 14, col_w)

        p_y = top + 8
        p_h = bottom - p_y
        if p_h >= 4:
            self.draw_box(p_y, 1, p_h, w - 2, "Partitions & Mount Points")
            parts = d.get("partitions", [])
            if not parts:
                self.safe_addstr(p_y + 1, 3, "No partitions detected on this drive.", curses.color_pair(1))
                return
            row = p_y + 1
            for p in parts:
                if row >= p_y + p_h - 1:
                    break
                header = f"/dev/{p['name']:<10} [{p['fstype']:<6}]  {p['size']:<10}  Mount: {p['mountpoint']}"
                self.safe_addstr(row, 3, header, curses.color_pair(3) | curses.A_BOLD, max_w=w - 6)
                row += 1
                if p.get("used_str") and row < p_y + p_h - 1:
                    self.draw_progress_bar(row, 5, min(36, w - 38), p["used_pct"], p["used_str"])
                    row += 1

    def draw_tab_sensors(self, top: int, bottom: int, w: int):
        temps = self.dynamic_data.get("temps", [])
        gpus = self.dynamic_data.get("graphics", [])
        gpu_pwr = gpus[0]["power"] if gpus else "N/A"

        self.draw_box(top, 1, 4, w - 2, "Power Consumption (HWMON)")
        inner_w = w - 6
        col_w = (inner_w - 2) // 2
        self.draw_field(top + 1, 3, "Host", self.static_data.get("system", {}).get("hostname", "-"), 14, col_w)
        self.draw_field(top + 1, 3 + col_w + 2, "GPU Board", gpu_pwr, 14, col_w, curses.color_pair(3) | curses.A_BOLD)

        s_y = top + 4
        s_h = bottom - s_y
        if s_h < 4:
            return

        self.draw_box(s_y, 1, s_h, w - 2, f"Hardware Temperatures (HWMON - {len(temps)} sensors) [↑/↓ Scroll]")
        if not temps:
            self.safe_addstr(s_y + 2, 3, "No HWMON thermal sensors found.", curses.color_pair(1))
            return

        # Constrói linhas agrupadas por chip
        lines = []
        last_chip = None
        for t in temps:
            chip = t.get("chip", "Hardware")
            if chip != last_chip:
                lines.append(("header", chip, None))
                last_chip = chip
            lines.append(("sensor", t, None))

        rows_avail = s_h - 2
        max_scroll = max(0, len(lines) - rows_avail)
        self.scroll_offset = min(self.scroll_offset, max_scroll)
        visible = lines[self.scroll_offset:self.scroll_offset + rows_avail]

        for idx, (kind, payload, _) in enumerate(visible):
            ry = s_y + 1 + idx
            if kind == "header":
                self.safe_addstr(ry, 3, f"▸ {payload}", curses.color_pair(3) | curses.A_BOLD, max_w=w - 6)
            else:
                temp_val = payload["temp"]
                crit_val = payload.get("crit")
                t_color = curses.color_pair(8) if temp_val >= 85 else (curses.color_pair(7) if temp_val >= 70 else curses.color_pair(6))
                crit_str = f" (Max: {crit_val:.1f} °C)" if crit_val else ""
                self.safe_addstr(ry, 5, f"{payload['label']:<26}", curses.color_pair(1))
                self.safe_addstr(ry, 32, f"{temp_val:6.1f} °C", t_color | curses.A_BOLD)
                if crit_str:
                    self.safe_addstr(ry, 43, crit_str, curses.color_pair(4))

    def draw_tab_bench(self, top: int, bottom: int, w: int):
        self.draw_box(top, 1, 7, w - 2, "CPU Benchmark (Native C11 Multi-Thread Engine)")
        status_attr = (curses.color_pair(7) if self.bench_running else curses.color_pair(6)) | curses.A_BOLD
        self.safe_addstr(top + 1, 3, f"Status: {self.bench_status}", status_attr, max_w=w - 6)

        inner_w = w - 6
        col_w = (inner_w - 2) // 2

        st_str = f"{self.bench_result['single_thread']:.1f}" if self.bench_result else "-"
        mt_str = f"{self.bench_result['multi_thread']:.1f}" if self.bench_result else "-"
        rat_str = self.bench_result["ratio"] if self.bench_result else "-"
        thr_str = f"{self.bench_result['threads_used']} Threads" if self.bench_result else "-"

        self.draw_field(top + 3, 3, "Single Thread", st_str, 15, col_w, curses.color_pair(3) | curses.A_BOLD)
        self.draw_field(top + 3, 3 + col_w + 2, "Multi Thread", mt_str, 15, col_w, curses.color_pair(3) | curses.A_BOLD)
        self.draw_field(top + 4, 3, "MT Ratio", rat_str, 15, col_w)
        self.draw_field(top + 4, 3 + col_w + 2, "Threads Used", thr_str, 15, col_w)

        # Comparação com CPU de referência
        r_y = top + 7
        r_h = bottom - r_y
        if r_h >= 8:
            self.bench_ref_idx %= len(BENCH_REFS)
            ref_name, ref_st, ref_mt = BENCH_REFS[self.bench_ref_idx]
            self.draw_box(r_y, 1, r_h, w - 2, "Reference Comparison [↑/↓ Change Reference CPU]")
            self.draw_field(r_y + 1, 3, "Reference CPU", ref_name, 15, inner_w)

            your_st = self.bench_result["single_thread"] if self.bench_result else 0.0
            your_mt = self.bench_result["multi_thread"] if self.bench_result else 0.0

            max_st = max(your_st, ref_st, 1.0)
            max_mt = max(your_mt, ref_mt, 1.0)
            bar_w = min(46, w - 34)

            self.safe_addstr(r_y + 3, 3, "Your CPU (ST) :", curses.color_pair(1))
            self.draw_progress_bar(r_y + 3, 19, bar_w, (your_st / max_st) * 100.0, f"{your_st:.1f} pts")

            self.safe_addstr(r_y + 4, 3, "Ref CPU  (ST) :", curses.color_pair(4))
            self.draw_progress_bar(r_y + 4, 19, bar_w, (ref_st / max_st) * 100.0, f"{ref_st:.1f} pts")

            self.safe_addstr(r_y + 6, 3, "Your CPU (MT) :", curses.color_pair(1))
            self.draw_progress_bar(r_y + 6, 19, bar_w, (your_mt / max_mt) * 100.0, f"{your_mt:.1f} pts")

            self.safe_addstr(r_y + 7, 3, "Ref CPU  (MT) :", curses.color_pair(4))
            self.draw_progress_bar(r_y + 7, 19, bar_w, (ref_mt / max_mt) * 100.0, f"{ref_mt:.1f} pts")

    def draw_tab_about(self, top: int, bottom: int, w: int):
        sys_env = self.static_data.get("system", {})
        self.draw_box(top, 1, 7, w - 2, "LHWDI — Linux Hardware Detection & Information")
        self.safe_addstr(top + 1, 3, "LHWDI - Linux Hardware Detection & Information v1.0 (Console TUI)", curses.color_pair(3) | curses.A_BOLD)
        self.safe_addstr(top + 2, 3, "Author & Engineering : David Martinz (gandhimartinz.eng.br)", curses.color_pair(1))
        self.safe_addstr(top + 3, 3, "Co-Development       : Google Gemini AI", curses.color_pair(1))
        self.safe_addstr(top + 4, 3, "Tech Stack           : Python 3 • Curses TUI • C11 Native Engine (gcc -O3)", curses.color_pair(1))
        self.safe_addstr(top + 5, 3, "Export Shortcuts     : [T] Save .TXT Report   |   [H] Save .HTML Report", curses.color_pair(4))

        self.draw_box(top + 7, 1, 8, w - 2, "Operating System & Runtime Environment")
        inner_w = w - 6
        col_w = (inner_w - 2) // 2
        self.draw_field(top + 8, 3, "Distribution", self.static_data.get("os", "-"), 15, inner_w)
        self.draw_field(top + 9, 3, "Kernel", sys_env.get("kernel", "-"), 15, col_w)
        self.draw_field(top + 9, 3 + col_w + 2, "Uptime", sys_env.get("uptime", "-"), 15, col_w)
        self.draw_field(top + 10, 3, "Hostname", sys_env.get("hostname", "-"), 15, col_w)
        self.draw_field(top + 10, 3 + col_w + 2, "Python", sys_env.get("python_version", "-"), 15, col_w)
        self.draw_field(top + 11, 3, "Compiler", sys_env.get("gcc_version", "-"), 15, inner_w)

    # -------------------------------------------------------------------------
    # AÇÕES ASSÍNCRONAS & EXPORTAÇÃO
    # -------------------------------------------------------------------------
    def trigger_benchmark(self):
        if self.bench_running:
            return
        self.bench_running = True
        self.bench_status = "Compiling & running Single/Multi-Thread C workload (~5s)..."

        def worker():
            try:
                res = backend.run_cpu_benchmark(duration_per_stage=2.5)
                self.bench_result = res
                self.bench_status = f"Completed in {res['duration']}s — Press [B] to re-run"
                self.show_toast(f"Benchmark finished: ST {res['single_thread']} / MT {res['multi_thread']}")
            except Exception as exc:
                self.bench_status = f"Error: {exc}"
                self.show_toast(f"Benchmark error: {exc}", is_error=True)
            finally:
                self.bench_running = False

        threading.Thread(target=worker, daemon=True).start()

    def export_report(self, fmt: str):
        try:
            snap = backend.collect_full_snapshot()
            ts_file = datetime.now().strftime("%Y%m%d-%H%M%S")
            filename = f"lhwdi-{snap['hostname']}-{ts_file}.{fmt}"
            target_path = backend.get_downloads_dir() / filename
            content = backend.build_txt_report(snap) if fmt == "txt" else backend.build_html_report(snap)
            target_path.write_text(content, encoding="utf-8")
            self.show_toast(f"Saved to ~/Downloads/{filename}")
        except Exception as exc:
            self.show_toast(f"Export error: {exc}", is_error=True)

    # -------------------------------------------------------------------------
    # LOOP PRINCIPAL
    # -------------------------------------------------------------------------
    def handle_input(self, ch: int):
        if ch == -1:
            return
        if ch in (ord('q'), ord('Q'), 27):
            self.running = False
        elif ch in (curses.KEY_RIGHT, 9):  # Right arrow ou Tab
            self.current_tab = (self.current_tab + 1) % len(TABS)
            self.scroll_offset = 0
        elif ch in (curses.KEY_LEFT, curses.KEY_BTAB):  # Left arrow ou Shift+Tab
            self.current_tab = (self.current_tab - 1) % len(TABS)
            self.scroll_offset = 0
        elif ord('1') <= ch <= ord('9'):
            self.current_tab = ch - ord('1')
            self.scroll_offset = 0
        elif ch == ord('0'):
            self.current_tab = 9
            self.scroll_offset = 0
        elif ch == curses.KEY_DOWN:
            if self.current_tab == 4 and self.spd_slots:
                self.spd_idx = (self.spd_idx + 1) % len(self.spd_slots)
            elif self.current_tab == 5:
                self.gpu_idx += 1
            elif self.current_tab == 6:
                self.drive_idx += 1
            elif self.current_tab == 8:
                self.bench_ref_idx = (self.bench_ref_idx + 1) % len(BENCH_REFS)
            else:
                self.scroll_offset += 1
        elif ch == curses.KEY_UP:
            if self.current_tab == 4 and self.spd_slots:
                self.spd_idx = (self.spd_idx - 1) % len(self.spd_slots)
            elif self.current_tab == 5:
                self.gpu_idx = max(0, self.gpu_idx - 1)
            elif self.current_tab == 6:
                self.drive_idx = max(0, self.drive_idx - 1)
            elif self.current_tab == 8:
                self.bench_ref_idx = (self.bench_ref_idx - 1) % len(BENCH_REFS)
            else:
                self.scroll_offset = max(0, self.scroll_offset - 1)
        elif ch == curses.KEY_NPAGE:
            self.scroll_offset += 8
        elif ch == curses.KEY_PPAGE:
            self.scroll_offset = max(0, self.scroll_offset - 8)
        elif ch in (ord('b'), ord('B')):
            self.current_tab = 8
            self.trigger_benchmark()
        elif ch in (ord('u'), ord('U')):
            slots = backend.parse_spd_slots(use_pkexec=True)
            if slots is not None:
                self.spd_slots = slots
                self.show_toast("SPD / DMI modules unlocked!")
            else:
                self.show_toast("Permission denied (configure NOPASSWD for dmidecode).", is_error=True)
        elif ch in (ord('t'), ord('T')):
            self.export_report("txt")
        elif ch in (ord('h'), ord('H')):
            self.export_report("html")
        elif ch in (ord('c'), ord('C')):
            self.dos_theme = not self.dos_theme
            self.init_colors()
            self.show_toast("Theme switched: " + ("HWiNFO DOS Blue" if self.dos_theme else "LHWDI Dark"))

    def run(self):
        curses.curs_set(0)
        self.stdscr.nodelay(True)
        self.stdscr.keypad(True)

        while self.running:
            now = time.time()
            if now - self.last_poll >= 1.0:
                self.dynamic_data = backend.get_dynamic_telemetry()
                self.last_poll = now

            self.stdscr.erase()
            h, w = self.stdscr.getmaxyx()

            if h < 18 or w < 64:
                self.safe_addstr(1, 2, f"Terminal too small ({w}x{h}). Resize to at least 64x18.", curses.color_pair(8) | curses.A_BOLD)
            else:
                self.draw_header_and_tabs()
                top = 3
                bottom = h - 2

                tab_handlers = [
                    self.draw_tab_cpu,
                    self.draw_tab_caches,
                    self.draw_tab_mainboard,
                    self.draw_tab_memory,
                    self.draw_tab_spd,
                    self.draw_tab_graphics,
                    self.draw_tab_drives,
                    self.draw_tab_sensors,
                    self.draw_tab_bench,
                    self.draw_tab_about,
                ]
                tab_handlers[self.current_tab](top, bottom, w)
                self.draw_footer()

            self.stdscr.refresh()

            # Leitura não bloqueante com polling suave (50 ms)
            ch = self.stdscr.getch()
            if ch != -1:
                self.handle_input(ch)
            else:
                time.sleep(0.05)


def main():
    os.environ.setdefault("ESCDELAY", "25")
    try:
        curses.wrapper(lambda stdscr: LHWDIConsoleApp(stdscr).run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
