#!/usr/bin/env python3
"""Tkinter editor for Yu-Gi-Oh! Power of Chaos system.dat.

Supported inputs:
* encrypted system.dat: 0x13C6 bytes
* complete decoded image: 0x1190 bytes
* payload-only image: 0x1188 bytes

The GUI keeps binary codec, registry identity handling, card metadata paths and
save editing separate so a transfer problem is not mistaken for a bad checksum.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import struct
import sys
import time
import traceback
from typing import Optional

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Tkinter is not installed. Reinstall Python with the Tcl/Tk component."
    ) from exc

try:
    from ygo_save_codec import (
        CARD_ENTRY_COUNT,
        CARD_TABLE_OFFSET,
        DECODED_SIZE,
        ENCODED_SIZE,
        FLCRC_SIZE,
        HEADER_SIZE,
        INNER_CHECKSUM_OFFSET,
        MAGIC_OFFSET,
        PAYLOAD_SIZE,
        SIGNATURE,
        SIGNATURE_OFFSET,
        TOTAL_CARD_COUNT_OFFSET,
        SaveFormatError,
        SaveImage,
        atomic_write_with_backup,
        calculate_inner_checksum,
        calculate_registry_flcrc,
        calculated_card_total,
        decode_registry_flcrc,
        encode_decoded,
        get_card_count,
        get_card_raw,
        hexdump,
        identity_engine_key,
        load_save_file,
        parse_hex_bytes,
        parse_int,
        parse_u16_table,
        prepare_encoded,
        read_u16,
        read_u32,
        recompute_stored_card_total,
        registry_flcrc_matches_header,
        repair_inner_checksum,
        self_test,
        set_card_count,
        set_card_new_flag,
        stored_card_total,
        validate_image,
        validate_outer_checksum,
        validate_registry_flcrc,
        write_u16,
        write_u32,
    )
except ModuleNotFoundError:
    from ygo_save_codec_v1_3 import (
        CARD_ENTRY_COUNT,
        CARD_TABLE_OFFSET,
        DECODED_SIZE,
        ENCODED_SIZE,
        FLCRC_SIZE,
        HEADER_SIZE,
        INNER_CHECKSUM_OFFSET,
        MAGIC_OFFSET,
        PAYLOAD_SIZE,
        SIGNATURE,
        SIGNATURE_OFFSET,
        TOTAL_CARD_COUNT_OFFSET,
        SaveFormatError,
        SaveImage,
        atomic_write_with_backup,
        calculate_inner_checksum,
        calculate_registry_flcrc,
        calculated_card_total,
        decode_registry_flcrc,
        encode_decoded,
        get_card_count,
        get_card_raw,
        hexdump,
        identity_engine_key,
        load_save_file,
        parse_hex_bytes,
        parse_int,
        parse_u16_table,
        prepare_encoded,
        read_u16,
        read_u32,
        recompute_stored_card_total,
        registry_flcrc_matches_header,
        repair_inner_checksum,
        self_test,
        set_card_count,
        set_card_new_flag,
        stored_card_total,
        validate_image,
        validate_outer_checksum,
        validate_registry_flcrc,
        write_u16,
        write_u32,
    )

APP_NAME = "YGO system.dat Editor"
APP_VERSION = "1.0"
CONFIG_FOLDER = "YGOSystemDatEditor"
CONFIG_FILENAME = "config.json"
CARD_NAME_RECORD_SIZE = 0x40

KNOWN_FIELDS = (
    (0x0000, "u16", "Header field 0", "Unknown; preserve unless tested"),
    (0x0002, "u16", "Header field 1", "Unknown; preserve unless tested"),
    (0x0004, "u16", "Header field 2", "Unknown; preserve unless tested"),
    (0x0006, "u16", "Options bitfield", "Engine option bits; common default 0x00FF"),
    (0x0008, "u16", "Display/window field", "Used during display initialization"),
    (TOTAL_CARD_COUNT_OFFSET, "u16", "Stored total cards", "Sum of owned counts modulo 65536"),
    (0x10F8, "u32", "Packed duel counters 0", "Three packed counters; semantics incomplete"),
    (SIGNATURE_OFFSET, "bytes8", "Save signature", "ASCII YUGIOH01"),
    (MAGIC_OFFSET, "u16", "Inner magic", "Must be 0xFBA5"),
    (INNER_CHECKSUM_OFFSET, "u16", "Inner checksum", "16-bit two's complement checksum"),
    (0x1186, "u16", "Trailing padding", "Usually zero; preserve by default"),
)

REGISTRY_CANDIDATES = (
    (
        "HKEY_CURRENT_USER",
        r"Software\Classes\VirtualStore\MACHINE\SOFTWARE\WOW6432Node\KONAMI\Yu-Gi-Oh! Power Of Chaos\system",
    ),
    (
        "HKEY_CLASSES_ROOT",
        r"VirtualStore\MACHINE\SOFTWARE\WOW6432Node\KONAMI\Yu-Gi-Oh! Power Of Chaos\system",
    ),
    (
        "HKEY_LOCAL_MACHINE",
        r"SOFTWARE\WOW6432Node\KONAMI\Yu-Gi-Oh! Power Of Chaos\system",
    ),
    (
        "HKEY_LOCAL_MACHINE",
        r"SOFTWARE\KONAMI\Yu-Gi-Oh! Power Of Chaos\system",
    ),
    (
        "HKEY_CURRENT_USER",
        r"Software\KONAMI\Yu-Gi-Oh! Power Of Chaos\system",
    ),
)

DATA_FILE_SPECS = (
    ("default_save", "Default system.dat", "system.dat", "Save opened by the editor; optional convenience path."),
    ("card_id", "CARD_ID.bin", "CARD_ID.bin", "Optional: internal save index -> external Card ID (1115 little-endian u16)."),
    ("card_names", "card_nameeng.bin", "card_nameeng.bin", "Recommended: fixed 0x40-byte English name records indexed by internal index."),
)


def bool_text(value: Optional[bool]) -> str:
    if value is None:
        return "N/A"
    return "OK" if value else "INVALID"


def bytes_hex(data: bytes | bytearray | memoryview) -> str:
    return " ".join(f"{b:02X}" for b in data)


def config_root() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", str(Path.home())))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / CONFIG_FOLDER


@dataclass
class RegistryEntry:
    root_name: str
    path: str
    view: int
    view_name: str
    value: Optional[bytes]


class EditorApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("1320x840")
        self.minsize(1020, 680)

        self.image: Optional[SaveImage] = None
        self.current_path: Optional[Path] = None
        self.original_registry_flcrc: Optional[bytes] = None
        self.dirty = False
        self._filter_after_id: Optional[str] = None

        self.card_ids: list[Optional[int]] = [None] * CARD_ENTRY_COUNT
        self.card_names: list[str] = [""] * CARD_ENTRY_COUNT
        self.registry_entries: list[RegistryEntry] = []

        self.repair_checksum_var = tk.BooleanVar(value=True)
        self.ensure_signature_var = tk.BooleanVar(value=True)
        self.recompute_total_var = tk.BooleanVar(value=True)
        self.skip_sentinel_var = tk.BooleanVar(value=True)
        self.search_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")

        saved_config = self._read_config()
        self.data_path_vars: dict[str, tk.StringVar] = {}
        self.data_status_vars: dict[str, tk.StringVar] = {}
        for key, _label, _filename, _description in DATA_FILE_SPECS:
            self.data_path_vars[key] = tk.StringVar(value=str(saved_config.get("paths", {}).get(key, "")))
            self.data_status_vars[key] = tk.StringVar(value="Not checked")

        self.summary_vars = {
            key: tk.StringVar(value="-")
            for key in (
                "path", "format", "source_size", "outer", "inner_magic",
                "inner_checksum", "signature", "roundtrip", "header", "flcrc",
                "identity_key", "stored_total", "actual_total", "data_files",
            )
        }
        self.registry_save_header_var = tk.StringVar(value="-")
        self.registry_save_flcrc_var = tk.StringVar(value="-")
        self.registry_explanation_var = tk.StringVar(
            value="EXACT/ENGINE MATCH are accepted. DIFFERENT requires either batch registry override/import or save rebind."
        )

        self._build_menu()
        self._build_toolbar()
        self._build_tabs()
        self._build_statusbar()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        try:
            self_test()
            self.log("Codec self-test: OK")
        except Exception:
            self.log("Codec self-test FAILED:\n" + traceback.format_exc())
            messagebox.showerror(APP_NAME, "Codec self-test failed; do not write a save.\n\n" + traceback.format_exc())

        self.auto_detect_data_files(quiet=True)
        self.reload_data_files(quiet=True)
        default_save = self.data_path_vars["default_save"].get().strip()
        if default_save and Path(default_save).is_file():
            self.log(f"Configured default save: {default_save}")

    # ---------- UI ----------
    def _build_menu(self) -> None:
        menu = tk.Menu(self)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="Open…", accelerator="Ctrl+O", command=self.open_dialog)
        file_menu.add_command(label="Open configured system.dat", command=self.open_configured_save)
        file_menu.add_command(label="Save", accelerator="Ctrl+S", command=self.save)
        file_menu.add_command(label="Save As…", accelerator="Ctrl+Shift+S", command=self.save_as)
        file_menu.add_separator()
        file_menu.add_command(label="Export decoded image…", command=self.export_decoded)
        file_menu.add_command(label="Export payload…", command=self.export_payload)
        file_menu.add_command(label="Export flcrc.bin…", command=self.export_flcrc)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.on_close)
        menu.add_cascade(label="File", menu=file_menu)

        tools = tk.Menu(menu, tearoff=False)
        tools.add_command(label="Reload configured data files", command=lambda: self.reload_data_files(quiet=False))
        tools.add_command(label="Auto-detect data files", command=lambda: self.auto_detect_data_files(quiet=False))
        tools.add_separator()
        tools.add_command(label="Repair payload checksum", command=self.repair_checksum_now)
        tools.add_command(label="Recompute total cards", command=self.recompute_total_now)
        tools.add_separator()
        tools.add_command(label="Scan registry identity", command=self.scan_registry)
        tools.add_command(label="Run codec self-test", command=self.run_self_test_dialog)
        menu.add_cascade(label="Tools", menu=tools)

        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label="Engine/layout notes", command=self.show_notes)
        help_menu.add_command(label="About", command=self.show_about)
        menu.add_cascade(label="Help", menu=help_menu)
        self.config(menu=menu)

        self.bind_all("<Control-o>", lambda _e: self.open_dialog())
        self.bind_all("<Control-s>", lambda _e: self.save())
        self.bind_all("<Control-Shift-S>", lambda _e: self.save_as())

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(fill="x")
        ttk.Button(bar, text="Open system.dat", command=self.open_dialog).pack(side="left")
        ttk.Button(bar, text="Save", command=self.save).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="Save As", command=self.save_as).pack(side="left", padx=(6, 0))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(bar, text="Scan registry", command=self.scan_registry).pack(side="left")
        ttk.Button(bar, text="Data paths", command=lambda: self.notebook.select(self.data_tab)).pack(side="left", padx=(6, 0))

    def _build_tabs(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        self.summary_tab = ttk.Frame(self.notebook, padding=12)
        self.cards_tab = ttk.Frame(self.notebook, padding=8)
        self.registry_tab = ttk.Frame(self.notebook, padding=8)
        self.data_tab = ttk.Frame(self.notebook, padding=10)
        self.raw_tab = ttk.Frame(self.notebook, padding=8)
        self.log_tab = ttk.Frame(self.notebook, padding=8)
        for tab, title in (
            (self.summary_tab, "Summary"),
            (self.cards_tab, "Cards"),
            (self.registry_tab, "Registry / Identity"),
            (self.data_tab, "Data files"),
            (self.raw_tab, "Raw fields"),
            (self.log_tab, "Log"),
        ):
            self.notebook.add(tab, text=title)
        self._build_summary_tab()
        self._build_cards_tab()
        self._build_registry_tab()
        self._build_data_tab()
        self._build_raw_tab()
        self._build_log_tab()

    def _build_summary_tab(self) -> None:
        self.summary_tab.columnconfigure(1, weight=1)
        rows = (
            ("File", "path"), ("Input format", "format"), ("Source size", "source_size"),
            ("Outer checksum", "outer"), ("Inner magic", "inner_magic"),
            ("Inner checksum", "inner_checksum"), ("Signature", "signature"),
            ("Decode/encode round-trip", "roundtrip"), ("Identity header", "header"),
            ("Derived flcrc", "flcrc"), ("Engine identity key", "identity_key"),
            ("Stored card total", "stored_total"), ("Calculated card total", "actual_total"),
            ("Data files", "data_files"),
        )
        for row, (label, key) in enumerate(rows):
            ttk.Label(self.summary_tab, text=label + ":", width=25).grid(row=row, column=0, sticky="nw", pady=3)
            ttk.Label(self.summary_tab, textvariable=self.summary_vars[key], wraplength=980, justify="left").grid(
                row=row, column=1, sticky="nw", pady=3
            )
        opts = ttk.LabelFrame(self.summary_tab, text="Save safeguards", padding=8)
        opts.grid(row=len(rows), column=0, columnspan=2, sticky="ew", pady=(14, 0))
        ttk.Checkbutton(opts, text="Repair inner checksum/magic", variable=self.repair_checksum_var).pack(side="left")
        ttk.Checkbutton(opts, text="Ensure YUGIOH01 signature", variable=self.ensure_signature_var).pack(side="left", padx=12)
        ttk.Checkbutton(opts, text="Recompute stored card total", variable=self.recompute_total_var).pack(side="left")
        ttk.Label(
            self.summary_tab,
            text=(
                "Normal card/payload edits do not change the 8-byte identity header. Registry flcrc only needs action "
                "when Registry / Identity reports DIFFERENT, or after intentionally rebinding the header."
            ), wraplength=1000, justify="left",
        ).grid(row=len(rows) + 1, column=0, columnspan=2, sticky="w", pady=(14, 0))

    def _build_cards_tab(self) -> None:
        top = ttk.Frame(self.cards_tab)
        top.pack(fill="x", pady=(0, 6))
        ttk.Label(top, text="Search name / ID / index:").pack(side="left")
        ttk.Entry(top, textvariable=self.search_var, width=38).pack(side="left", padx=(6, 10))
        self.search_var.trace_add("write", self._schedule_card_filter)
        ttk.Button(top, text="Clear", command=lambda: self.search_var.set("")).pack(side="left")
        ttk.Button(top, text="Data file paths…", command=lambda: self.notebook.select(self.data_tab)).pack(side="right")

        holder = ttk.Frame(self.cards_tab)
        holder.pack(fill="both", expand=True)
        columns = ("internal", "name", "idhex", "iddec", "count", "deck1", "deck2", "deck3", "new", "reserved", "raw")
        headings = {
            "internal": "Index", "name": "Card name", "idhex": "Card ID hex", "iddec": "Card ID dec",
            "count": "Owned", "deck1": "Deck A", "deck2": "Deck B", "deck3": "Deck C",
            "new": "New", "reserved": "Bit15", "raw": "Raw",
        }
        widths = {
            "internal": 68, "name": 320, "idhex": 90, "iddec": 78, "count": 60,
            "deck1": 58, "deck2": 58, "deck3": 58, "new": 45, "reserved": 48,
            "raw": 68,
        }
        self.card_tree = ttk.Treeview(holder, columns=columns, show="headings", selectmode="browse")
        for col in columns:
            self.card_tree.heading(col, text=headings[col])
            self.card_tree.column(col, width=widths[col], minwidth=42, anchor="w" if col == "name" else "center")
        ys = ttk.Scrollbar(holder, orient="vertical", command=self.card_tree.yview)
        xs = ttk.Scrollbar(holder, orient="horizontal", command=self.card_tree.xview)
        self.card_tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.card_tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        self.card_tree.bind("<<TreeviewSelect>>", self.on_card_select)

        edit = ttk.LabelFrame(self.cards_tab, text="Selected card", padding=8)
        edit.pack(fill="x", pady=(8, 0))
        self.selected_card_var = tk.StringVar(value="No selection")
        self.card_count_var = tk.IntVar(value=0)
        self.card_new_var = tk.BooleanVar(value=False)
        ttk.Label(edit, textvariable=self.selected_card_var, width=58).pack(side="left")
        ttk.Label(edit, text="Owned:").pack(side="left", padx=(8, 4))
        ttk.Spinbox(edit, from_=0, to=255, textvariable=self.card_count_var, width=6).pack(side="left")
        ttk.Checkbutton(edit, text="New flag", variable=self.card_new_var).pack(side="left", padx=10)
        ttk.Button(edit, text="Apply", command=self.apply_selected_card).pack(side="left")

        bulk = ttk.LabelFrame(self.cards_tab, text="Bulk operations", padding=8)
        bulk.pack(fill="x", pady=(8, 0))
        ttk.Button(bulk, text="Set valid card counts…", command=self.bulk_set_counts).pack(side="left")
        ttk.Button(bulk, text="Mark all new", command=lambda: self.bulk_set_new(True)).pack(side="left", padx=6)
        ttk.Button(bulk, text="Clear all new", command=lambda: self.bulk_set_new(False)).pack(side="left")
        ttk.Button(bulk, text="Recompute total", command=self.recompute_total_now).pack(side="left", padx=6)
        ttk.Checkbutton(bulk, text="Skip external ID 0000/FFFF", variable=self.skip_sentinel_var).pack(side="right")

    def _build_registry_tab(self) -> None:
        top = ttk.LabelFrame(self.registry_tab, text="Save identity", padding=8)
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Decoded header:").grid(row=0, column=0, sticky="w")
        ttk.Label(top, textvariable=self.registry_save_header_var).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(top, text="Derived flcrc:").grid(row=1, column=0, sticky="w")
        ttk.Label(top, textvariable=self.registry_save_flcrc_var).grid(row=1, column=1, sticky="w", padx=8)
        ttk.Label(top, textvariable=self.registry_explanation_var, wraplength=1050, justify="left").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(7, 0)
        )

        buttons = ttk.Frame(self.registry_tab)
        buttons.pack(fill="x", pady=8)
        ttk.Button(buttons, text="Scan registry", command=self.scan_registry).pack(side="left")
        ttk.Button(buttons, text="Override selected registry rows from save", command=self.override_selected_registry).pack(side="left", padx=6)
        ttk.Button(buttons, text="Rebind save to selected registry", command=self.rebind_save_to_selected_registry).pack(side="left")
        ttk.Button(buttons, text="Import flcrc.bin to selected registry rows…", command=self.import_flcrc_to_selected_registry).pack(side="left", padx=6)
        ttk.Button(buttons, text="Export save flcrc…", command=self.export_flcrc).pack(side="right")

        holder = ttk.Frame(self.registry_tab)
        holder.pack(fill="both", expand=True)
        cols = ("root", "path", "view", "flcrc", "decoded", "checksum", "match")
        self.registry_tree = ttk.Treeview(holder, columns=cols, show="headings", selectmode="extended")
        labels = {
            "root": "Root", "path": "Key path", "view": "View", "flcrc": "flcrc",
            "decoded": "Decoded identity", "checksum": "Checksum", "match": "Save match",
        }
        sizes = {"root": 140, "path": 400, "view": 78, "flcrc": 300, "decoded": 190, "checksum": 80, "match": 110}
        for col in cols:
            self.registry_tree.heading(col, text=labels[col])
            self.registry_tree.column(col, width=sizes[col], minwidth=60, anchor="w")
        ys = ttk.Scrollbar(holder, orient="vertical", command=self.registry_tree.yview)
        xs = ttk.Scrollbar(holder, orient="horizontal", command=self.registry_tree.xview)
        self.registry_tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.registry_tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        ttk.Label(
            self.registry_tab,
            text=(
                "EXACT: all 8 decoded bytes equal. ENGINE MATCH: byte 0 and DWORD at bytes 4..7 equal; "
                "padding bytes 1..3 differ but this engine build ignores them. DIFFERENT: game may reject/reset the save."
            ), wraplength=1120, justify="left",
        ).pack(fill="x", pady=(8, 0))

    def _build_data_tab(self) -> None:
        self.data_tab.columnconfigure(1, weight=1)
        ttk.Label(
            self.data_tab,
            text=(
                "Only three paths are needed in the editor: an optional default system.dat, optional CARD_ID.bin for external IDs, "
                "and recommended card_nameeng.bin for card names."
            ), wraplength=1120, justify="left",
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))
        for row, (key, label, _filename, description) in enumerate(DATA_FILE_SPECS, start=1):
            ttk.Label(self.data_tab, text=label + ":", width=20).grid(row=row, column=0, sticky="nw", pady=5)
            ttk.Entry(self.data_tab, textvariable=self.data_path_vars[key]).grid(row=row, column=1, sticky="ew", padx=5, pady=5)
            ttk.Button(self.data_tab, text="Browse…", command=lambda k=key: self.browse_data_file(k)).grid(row=row, column=2, pady=5)
            ttk.Label(self.data_tab, textvariable=self.data_status_vars[key], width=28).grid(row=row, column=3, sticky="w", padx=(8, 0), pady=5)
            ttk.Label(self.data_tab, text=description, wraplength=900, justify="left").grid(
                row=row + len(DATA_FILE_SPECS), column=1, columnspan=3, sticky="w", padx=5, pady=(0, 3)
            )
        action_row = 1 + len(DATA_FILE_SPECS) * 2
        actions = ttk.Frame(self.data_tab)
        actions.grid(row=action_row, column=0, columnspan=4, sticky="ew", pady=(16, 0))
        ttk.Button(actions, text="Auto-detect", command=lambda: self.auto_detect_data_files(quiet=False)).pack(side="left")
        ttk.Button(actions, text="Use one folder…", command=self.choose_data_folder).pack(side="left", padx=6)
        ttk.Button(actions, text="Reload all", command=lambda: self.reload_data_files(quiet=False)).pack(side="left")
        ttk.Button(actions, text="Save configuration", command=self.save_config).pack(side="left", padx=6)
        ttk.Button(actions, text="Open configured save", command=self.open_configured_save).pack(side="right")
        ttk.Label(
            self.data_tab,
            text=f"Configuration file: {config_root() / CONFIG_FILENAME}", wraplength=1000, justify="left",
        ).grid(row=action_row + 1, column=0, columnspan=4, sticky="w", pady=(12, 0))

    def _build_raw_tab(self) -> None:
        pane = ttk.Panedwindow(self.raw_tab, orient="horizontal")
        pane.pack(fill="both", expand=True)
        left, right = ttk.Frame(pane), ttk.Frame(pane)
        pane.add(left, weight=2)
        pane.add(right, weight=3)
        self.known_tree = ttk.Treeview(left, columns=("offset", "type", "name", "value"), show="headings", selectmode="browse")
        for col, title, width in (("offset", "Offset", 75), ("type", "Type", 65), ("name", "Field", 190), ("value", "Value", 140)):
            self.known_tree.heading(col, text=title)
            self.known_tree.column(col, width=width, anchor="w")
        self.known_tree.pack(fill="both", expand=True)
        self.known_tree.bind("<<TreeviewSelect>>", self.on_known_field_select)

        editor = ttk.LabelFrame(right, text="Offset editor (payload-relative)", padding=8)
        editor.pack(fill="x")
        self.raw_offset_var = tk.StringVar(value="0x0000")
        self.raw_type_var = tk.StringVar(value="u16")
        self.raw_value_var = tk.StringVar(value="0x0000")
        ttk.Label(editor, text="Offset:").grid(row=0, column=0, sticky="w")
        ttk.Entry(editor, textvariable=self.raw_offset_var, width=12).grid(row=0, column=1, padx=5)
        ttk.Label(editor, text="Type:").grid(row=0, column=2, padx=(10, 0))
        ttk.Combobox(editor, textvariable=self.raw_type_var, values=("u8", "u16", "u32", "bytes"), state="readonly", width=9).grid(row=0, column=3, padx=5)
        ttk.Button(editor, text="Read", command=self.raw_read).grid(row=0, column=4, padx=(10, 4))
        ttk.Button(editor, text="Write", command=self.raw_write).grid(row=0, column=5)
        ttk.Label(editor, text="Value:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(editor, textvariable=self.raw_value_var).grid(row=1, column=1, columnspan=5, sticky="ew", padx=5, pady=(8, 0))
        editor.columnconfigure(5, weight=1)
        ttk.Label(right, text="Numbers accept decimal or 0xHEX. Bytes accept hexadecimal pairs.", justify="left").pack(fill="x", pady=(6, 8))
        dump = ttk.LabelFrame(right, text="Payload hex around offset", padding=4)
        dump.pack(fill="both", expand=True)
        self.hex_text = tk.Text(dump, wrap="none", font=("TkFixedFont", 10), state="disabled")
        ys, xs = ttk.Scrollbar(dump, orient="vertical", command=self.hex_text.yview), ttk.Scrollbar(dump, orient="horizontal", command=self.hex_text.xview)
        self.hex_text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.hex_text.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        dump.rowconfigure(0, weight=1)
        dump.columnconfigure(0, weight=1)

    def _build_log_tab(self) -> None:
        self.log_text = tk.Text(self.log_tab, wrap="word", state="disabled")
        scroll = ttk.Scrollbar(self.log_tab, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self, relief="sunken", padding=(6, 3))
        bar.pack(fill="x", side="bottom")
        ttk.Label(bar, textvariable=self.status_var).pack(side="left")

    # ---------- common ----------
    def log(self, text: str) -> None:
        if not hasattr(self, "log_text"):
            return
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def set_status(self, text: str) -> None:
        self.status_var.set(text)
        self.update_idletasks()

    def set_dirty(self, dirty: bool = True) -> None:
        self.dirty = dirty
        path = str(self.current_path) if self.current_path else "untitled"
        self.title(f"{APP_NAME} {APP_VERSION} — {path}{' *' if dirty else ''}")

    def _require_image(self) -> SaveImage:
        if self.image is None:
            raise SaveFormatError("No save is open.")
        return self.image

    def _confirm_discard(self) -> bool:
        return not self.dirty or messagebox.askyesno(APP_NAME, "Unsaved changes will be lost. Continue?", icon="warning")

    # ---------- config/data files ----------
    def _read_config(self) -> dict:
        path = config_root() / CONFIG_FILENAME
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}

    def save_config(self, *, quiet: bool = False) -> None:
        try:
            root = config_root()
            root.mkdir(parents=True, exist_ok=True)
            data = {"version": 1, "paths": {key: var.get().strip() for key, var in self.data_path_vars.items()}}
            (root / CONFIG_FILENAME).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            self.log(f"Configuration saved: {root / CONFIG_FILENAME}")
            if not quiet:
                messagebox.showinfo(APP_NAME, "Configuration saved.")
        except Exception as exc:
            if not quiet:
                messagebox.showerror(APP_NAME, f"Could not save configuration:\n{exc}")

    def browse_data_file(self, key: str) -> None:
        filename = next(spec[2] for spec in DATA_FILE_SPECS if spec[0] == key)
        path = filedialog.askopenfilename(title=f"Select {filename}", initialfile=filename, filetypes=(("Binary/data", "*.bin *.dat"), ("All files", "*.*")))
        if path:
            self.data_path_vars[key].set(path)
            self.reload_data_files(quiet=True)
            self.save_config(quiet=True)

    def choose_data_folder(self) -> None:
        folder = filedialog.askdirectory(title="Folder containing system.dat / card data files")
        if not folder:
            return
        base = Path(folder)
        for key, _label, filename, _description in DATA_FILE_SPECS:
            candidate = base / filename
            if candidate.is_file():
                self.data_path_vars[key].set(str(candidate))
        self.reload_data_files(quiet=False)
        self.save_config(quiet=True)

    def auto_detect_data_files(self, *, quiet: bool) -> None:
        folders: list[Path] = [Path(__file__).resolve().parent, Path.cwd()]
        if self.current_path:
            folders.insert(0, self.current_path.parent)
        save_text = self.data_path_vars["default_save"].get().strip()
        if save_text:
            folders.insert(0, Path(save_text).expanduser().parent)
        seen: set[Path] = set()
        for folder in folders:
            try:
                folder = folder.resolve()
            except OSError:
                continue
            if folder in seen:
                continue
            seen.add(folder)
            for key, _label, filename, _description in DATA_FILE_SPECS:
                current = self.data_path_vars[key].get().strip()
                if current and Path(current).is_file():
                    continue
                candidate = folder / filename
                if candidate.is_file():
                    self.data_path_vars[key].set(str(candidate))
        self.reload_data_files(quiet=True)
        self.save_config(quiet=True)
        if not quiet:
            messagebox.showinfo(APP_NAME, "Auto-detection completed. Review the Data files tab.")

    @staticmethod
    def _parse_card_names(path: Path) -> tuple[list[str], int]:
        data = path.read_bytes()
        minimum = CARD_ENTRY_COUNT * CARD_NAME_RECORD_SIZE
        if len(data) < minimum:
            raise SaveFormatError(
                f"{path.name} must contain at least 0x{minimum:X} bytes for {CARD_ENTRY_COUNT} records; got 0x{len(data):X}."
            )
        # The supplied file has 1318 complete 0x40-byte records plus eight
        # trailing zero bytes. Engine name lookup is base + index*0x40, so the
        # non-record tail is intentionally ignored.
        record_count, _trailing = divmod(len(data), CARD_NAME_RECORD_SIZE)
        names: list[str] = []
        for index in range(record_count):
            offset = index * CARD_NAME_RECORD_SIZE
            raw = data[offset:offset + CARD_NAME_RECORD_SIZE].split(b"\0", 1)[0]
            names.append(raw.decode("cp1252", errors="replace").strip())
        return names[:CARD_ENTRY_COUNT], record_count

    def reload_data_files(self, *, quiet: bool) -> None:
        errors: list[str] = []
        self.card_ids = [None] * CARD_ENTRY_COUNT
        self.card_names = [""] * CARD_ENTRY_COUNT
        for key, label, _filename, _description in DATA_FILE_SPECS:
            text = self.data_path_vars[key].get().strip()
            if not text:
                self.data_status_vars[key].set("Not configured")
                continue
            path = Path(text).expanduser()
            if not path.is_file():
                self.data_status_vars[key].set("Missing")
                errors.append(f"{label}: file not found")
                continue
            try:
                if key == "card_id":
                    self.card_ids = list(parse_u16_table(path))
                    self.data_status_vars[key].set(f"Loaded {len(self.card_ids)} IDs")
                elif key == "card_names":
                    names, total_records = self._parse_card_names(path)
                    self.card_names[:len(names)] = names
                    nonempty = sum(bool(name) for name in names)
                    self.data_status_vars[key].set(f"Loaded {nonempty} names / {total_records} records")
                elif key == "default_save":
                    size = path.stat().st_size
                    status = "Supported" if size in (ENCODED_SIZE, DECODED_SIZE, PAYLOAD_SIZE) else f"Unexpected size 0x{size:X}"
                    self.data_status_vars[key].set(status)
                else:
                    self.data_status_vars[key].set(f"Present, 0x{path.stat().st_size:X} bytes (not required)")
            except Exception as exc:
                self.data_status_vars[key].set("Invalid")
                errors.append(f"{label}: {exc}")
        self.refresh_cards()
        self.refresh_summary()
        self.log("Data files reloaded: " + self._data_file_summary())
        if errors:
            self.log("Data file errors:\n" + "\n".join(errors))
            if not quiet:
                messagebox.showwarning(APP_NAME, "Some data files could not be loaded:\n\n" + "\n".join(errors))
        elif not quiet:
            messagebox.showinfo(APP_NAME, "Configured data files loaded.")

    def _data_file_summary(self) -> str:
        names = sum(bool(v) for v in self.card_names)
        ids = sum(v is not None for v in self.card_ids)
        return f"names={names}/{CARD_ENTRY_COUNT}, external IDs={ids}/{CARD_ENTRY_COUNT}"

    # ---------- load/save ----------
    def open_dialog(self) -> None:
        if not self._confirm_discard():
            return
        path = filedialog.askopenfilename(title="Open system.dat or decoded save", filetypes=(("Yu-Gi-Oh save", "*.dat *.bin"), ("All files", "*.*")))
        if path:
            self.open_file(Path(path))

    def open_configured_save(self) -> None:
        text = self.data_path_vars["default_save"].get().strip()
        if not text:
            messagebox.showinfo(APP_NAME, "Configure Default system.dat in the Data files tab first.")
            return
        if self._confirm_discard():
            self.open_file(Path(text))

    def open_file(self, path: Path) -> None:
        try:
            self.set_status(f"Reading {path.name}…")
            image = load_save_file(path)
            self.image = image
            self.current_path = path
            self.original_registry_flcrc = calculate_registry_flcrc(image.original_encoded) if image.original_encoded else None
            self.data_path_vars["default_save"].set(str(path))
            self.set_dirty(False)
            self.auto_detect_data_files(quiet=True)
            self.refresh_all()
            report = validate_image(image)
            self.log(
                f"Opened {path} | format={report.source_format} | outer={bool_text(report.outer_checksum_valid)} | "
                f"inner={bool_text(report.inner_valid)} | roundtrip={bool_text(report.roundtrip_exact)}"
            )
            self.set_status(f"Opened {path.name}")
            if report.outer_checksum_valid is False or not report.inner_valid:
                messagebox.showwarning(APP_NAME, "The save opened, but one or more checks are invalid. Saving with repair enabled will rebuild checksums.")
        except Exception as exc:
            self.set_status("Open failed")
            self.log(traceback.format_exc())
            messagebox.showerror(APP_NAME, f"Could not open file:\n{exc}")

    def save(self) -> None:
        try:
            image = self._require_image()
        except SaveFormatError as exc:
            messagebox.showinfo(APP_NAME, str(exc))
            return
        if self.current_path is None or image.source_format != "encrypted":
            self.save_as()
        else:
            self._save_to(self.current_path, create_backup=True)

    def save_as(self) -> None:
        try:
            self._require_image()
        except SaveFormatError as exc:
            messagebox.showinfo(APP_NAME, str(exc))
            return
        path = filedialog.asksaveasfilename(title="Save encrypted system.dat", initialfile="system.dat", defaultextension=".dat", filetypes=(("system.dat", "*.dat"), ("All files", "*.*")))
        if path:
            self._save_to(Path(path), create_backup=True)

    def _save_to(self, path: Path, *, create_backup: bool) -> None:
        try:
            image = self._require_image()
            self.set_status("Repairing and encoding…")
            encoded = prepare_encoded(
                image,
                repair_checksum=self.repair_checksum_var.get(),
                ensure_signature=self.ensure_signature_var.get(),
                recompute_total=self.recompute_total_var.get(),
            )
            if not validate_outer_checksum(encoded):
                raise AssertionError("Internal error: outer checksum failed after encoding")
            backup = atomic_write_with_backup(path, encoded, create_backup=create_backup)
            new_flcrc = calculate_registry_flcrc(encoded)
            old_flcrc = self.original_registry_flcrc
            image.original_encoded = encoded
            image.source_format = "encrypted"
            image.source_path = path
            self.current_path = path
            self.original_registry_flcrc = new_flcrc
            self.data_path_vars["default_save"].set(str(path))
            self.save_config(quiet=True)
            self.set_dirty(False)
            self.refresh_all()
            self.log(f"Saved encrypted save: {path}")
            if backup:
                self.log(f"Backup: {backup}")
            detail = f"Saved:\n{path}"
            if backup:
                detail += f"\n\nBackup:\n{backup}"
            if old_flcrc is None:
                detail += (
                    "\n\nThe source was not an encrypted system.dat with a trusted identity baseline. "
                    "Check Registry / Identity and rebind or override before running the game."
                )
            elif old_flcrc != new_flcrc:
                detail += "\n\nIdentity/flcrc changed. Check Registry / Identity before running the game."
            else:
                detail += "\n\nIdentity header was preserved; normal payload edits do not require a registry change."
            messagebox.showinfo(APP_NAME, detail)
            self.set_status(f"Saved {path.name}")
        except Exception as exc:
            self.set_status("Save failed")
            self.log(traceback.format_exc())
            messagebox.showerror(APP_NAME, f"Could not save file:\n{exc}")

    def export_decoded(self) -> None:
        try:
            image = self._require_image()
        except SaveFormatError as exc:
            messagebox.showinfo(APP_NAME, str(exc)); return
        path = filedialog.asksaveasfilename(title="Export complete decoded image", initialfile="system.decoded.bin", defaultextension=".bin")
        if path:
            Path(path).write_bytes(image.decoded)

    def export_payload(self) -> None:
        try:
            image = self._require_image()
        except SaveFormatError as exc:
            messagebox.showinfo(APP_NAME, str(exc)); return
        path = filedialog.asksaveasfilename(title="Export decoded payload", initialfile="system.payload.bin", defaultextension=".bin")
        if path:
            Path(path).write_bytes(bytes(image.payload))

    # ---------- refresh ----------
    def refresh_all(self) -> None:
        self.refresh_summary()
        self.refresh_cards()
        self.refresh_known_fields()
        self.raw_read(silent=True)
        self.refresh_registry_tree()

    def refresh_summary(self) -> None:
        self.summary_vars["data_files"].set(self._data_file_summary())
        if self.image is None:
            for key, var in self.summary_vars.items():
                if key != "data_files":
                    var.set("-")
            self.registry_save_header_var.set("-")
            self.registry_save_flcrc_var.set("-")
            return
        report = validate_image(self.image)
        encoded = encode_decoded(self.image.decoded)
        flcrc = calculate_registry_flcrc(encoded)
        stored_checksum = read_u16(self.image.payload, INNER_CHECKSUM_OFFSET)
        expected_checksum = calculate_inner_checksum(self.image.payload)
        key_type, key_time = identity_engine_key(self.image.header)
        values = {
            "path": str(self.current_path or "(memory)"),
            "format": report.source_format,
            "source_size": f"0x{report.source_size:X} ({report.source_size} bytes)",
            "outer": bool_text(report.outer_checksum_valid),
            "inner_magic": f"{bool_text(report.inner_magic_valid)} — stored 0x{read_u16(self.image.payload, MAGIC_OFFSET):04X}",
            "inner_checksum": f"{bool_text(report.inner_checksum_valid)} — stored 0x{stored_checksum:04X}, expected 0x{expected_checksum:04X}",
            "signature": f"{bool_text(report.signature_valid)} — {bytes(self.image.payload[SIGNATURE_OFFSET:SIGNATURE_OFFSET+8])!r}",
            "roundtrip": bool_text(report.roundtrip_exact),
            "header": bytes_hex(self.image.header),
            "flcrc": bytes_hex(flcrc),
            "identity_key": f"type=0x{key_type:02X}, time/scalar=0x{key_time:08X} (bytes 1..3 are padding)",
            "stored_total": str(report.stored_total_cards),
            "actual_total": f"{report.calculated_total_cards} (mod 65536 = {report.calculated_total_cards & 0xFFFF})",
        }
        for key, value in values.items():
            self.summary_vars[key].set(value)
        self.registry_save_header_var.set(bytes_hex(self.image.header))
        self.registry_save_flcrc_var.set(bytes_hex(flcrc))

    def refresh_cards(self) -> None:
        if not hasattr(self, "card_tree"):
            return
        self.card_tree.delete(*self.card_tree.get_children())
        if self.image is None:
            return
        query = self.search_var.get().strip().casefold()
        payload = self.image.payload
        for index in range(CARD_ENTRY_COUNT):
            ext, name = self.card_ids[index], self.card_names[index]
            raw = get_card_raw(payload, index)
            ext_hex = "----" if ext is None else f"{ext:04X}"
            ext_dec = "-" if ext is None else str(ext)
            ext_prefixed = "" if ext is None else f"0x{ext:04x}"
            haystack = f"{index} {index:04x} 0x{index:04x} {ext_hex} {ext_prefixed} {ext_dec} {name}".casefold()
            if query and query not in haystack:
                continue
            self.card_tree.insert("", "end", iid=str(index), values=(
                f"{index:04X}", name or "(unnamed)", ext_hex, ext_dec, raw & 0xFF,
                (raw >> 8) & 3, (raw >> 10) & 3, (raw >> 12) & 3,
                "Y" if raw & 0x4000 else "", "Y" if raw & 0x8000 else "",
                f"{raw:04X}",
            ))

    def _schedule_card_filter(self, *_args: object) -> None:
        if self._filter_after_id is not None:
            self.after_cancel(self._filter_after_id)
        self._filter_after_id = self.after(180, self._apply_card_filter)

    def _apply_card_filter(self) -> None:
        self._filter_after_id = None
        self.refresh_cards()

    # ---------- card editing ----------
    def on_card_select(self, _event: object = None) -> None:
        if self.image is None:
            return
        selected = self.card_tree.selection()
        if not selected:
            return
        index = int(selected[0])
        raw = get_card_raw(self.image.payload, index)
        ext = self.card_ids[index]
        name = self.card_names[index] or "(unnamed)"
        self.selected_card_var.set(f"Index 0x{index:04X} — {name} — ID {'----' if ext is None else f'0x{ext:04X}'}")
        self.card_count_var.set(raw & 0xFF)
        self.card_new_var.set(bool(raw & 0x4000))

    def apply_selected_card(self) -> None:
        try:
            image = self._require_image()
            selected = self.card_tree.selection()
            if not selected:
                raise ValueError("Select a card first")
            index = int(selected[0])
            count = int(self.card_count_var.get())
            if not 0 <= count <= 255:
                raise ValueError("Owned count must be 0..255")
            set_card_count(image.payload, index, count)
            set_card_new_flag(image.payload, index, self.card_new_var.get())
            self.set_dirty()
            self.refresh_cards()
            self.refresh_summary()
            self.card_tree.selection_set(str(index))
            self.card_tree.see(str(index))
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def _card_is_valid_for_bulk(self, index: int) -> bool:
        ext = self.card_ids[index]
        if ext is None:
            return True
        return not (self.skip_sentinel_var.get() and ext in (0x0000, 0xFFFF))

    def bulk_set_counts(self) -> None:
        try:
            image = self._require_image()
            count = simpledialog.askinteger(APP_NAME, "Owned count for valid card entries (0..255):", minvalue=0, maxvalue=255, parent=self)
            if count is None:
                return
            changed = 0
            for index in range(CARD_ENTRY_COUNT):
                if self._card_is_valid_for_bulk(index):
                    set_card_count(image.payload, index, count)
                    changed += 1
            recompute_stored_card_total(image.payload)
            self.set_dirty()
            self.refresh_cards(); self.refresh_summary(); self.refresh_known_fields()
            self.log(f"Bulk owned count set to {count} for {changed} entries")
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def bulk_set_new(self, enabled: bool) -> None:
        try:
            image = self._require_image()
            for index in range(CARD_ENTRY_COUNT):
                if self._card_is_valid_for_bulk(index):
                    set_card_new_flag(image.payload, index, enabled)
            self.set_dirty(); self.refresh_cards()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def recompute_total_now(self) -> None:
        try:
            image = self._require_image()
            actual, stored = recompute_stored_card_total(image.payload)
            self.set_dirty(); self.refresh_summary(); self.refresh_known_fields()
            messagebox.showinfo(APP_NAME, f"Calculated total: {actual}\nStored uint16: {stored}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def repair_checksum_now(self) -> None:
        try:
            image = self._require_image()
            checksum = repair_inner_checksum(image.payload, ensure_signature=self.ensure_signature_var.get())
            self.set_dirty(); self.refresh_summary(); self.refresh_known_fields()
            messagebox.showinfo(APP_NAME, f"Inner checksum = 0x{checksum:04X}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    # ---------- raw fields ----------
    def refresh_known_fields(self) -> None:
        self.known_tree.delete(*self.known_tree.get_children())
        if self.image is None:
            return
        for index, (offset, ftype, name, _desc) in enumerate(KNOWN_FIELDS):
            try:
                value = self._read_field_value(self.image.payload, offset, ftype)
            except Exception:
                value = "ERROR"
            self.known_tree.insert("", "end", iid=str(index), values=(f"0x{offset:04X}", ftype, name, value))

    def _read_field_value(self, payload: memoryview, offset: int, ftype: str) -> str:
        if ftype == "u8": return f"0x{payload[offset]:02X} ({payload[offset]})"
        if ftype == "u16":
            value = read_u16(payload, offset); return f"0x{value:04X} ({value})"
        if ftype == "u32":
            value = read_u32(payload, offset); return f"0x{value:08X} ({value})"
        if ftype == "bytes8": return bytes_hex(payload[offset:offset+8])
        raise ValueError(ftype)

    def on_known_field_select(self, _event: object = None) -> None:
        selected = self.known_tree.selection()
        if not selected:
            return
        offset, ftype, _name, _desc = KNOWN_FIELDS[int(selected[0])]
        self.raw_offset_var.set(f"0x{offset:04X}")
        self.raw_type_var.set("bytes" if ftype == "bytes8" else ftype)
        if ftype == "bytes8" and self.image:
            self.raw_value_var.set(bytes_hex(self.image.payload[offset:offset+8]))
        else:
            self.raw_read(silent=True)

    def raw_read(self, silent: bool = False) -> None:
        try:
            image = self._require_image()
            offset = parse_int(self.raw_offset_var.get())
            ftype = self.raw_type_var.get()
            if ftype == "u8": value = f"0x{image.payload[offset]:02X}"
            elif ftype == "u16": value = f"0x{read_u16(image.payload, offset):04X}"
            elif ftype == "u32": value = f"0x{read_u32(image.payload, offset):08X}"
            else:
                length = 16
                existing = parse_hex_bytes(self.raw_value_var.get()) if self.raw_value_var.get().strip() else b""
                if existing: length = len(existing)
                value = bytes_hex(image.payload[offset:offset+length])
            self.raw_value_var.set(value)
            self._show_hex_region(offset)
        except Exception as exc:
            if not silent and self.image is not None:
                messagebox.showerror(APP_NAME, str(exc))

    def raw_write(self) -> None:
        try:
            image = self._require_image()
            offset = parse_int(self.raw_offset_var.get())
            ftype, text = self.raw_type_var.get(), self.raw_value_var.get()
            if ftype == "u8":
                value = parse_int(text, bits=8)
                if offset >= PAYLOAD_SIZE: raise IndexError("Offset out of range")
                image.payload[offset] = value
            elif ftype == "u16": write_u16(image.payload, offset, parse_int(text, bits=16))
            elif ftype == "u32": write_u32(image.payload, offset, parse_int(text, bits=32))
            else:
                data = parse_hex_bytes(text)
                if offset + len(data) > PAYLOAD_SIZE: raise IndexError("Write exceeds payload")
                image.payload[offset:offset+len(data)] = data
            self.set_dirty(); self.refresh_all(); self.raw_offset_var.set(f"0x{offset:04X}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def _show_hex_region(self, offset: int) -> None:
        if self.image is None:
            return
        start = max(0, offset - 64) & ~0xF
        text = hexdump(self.image.payload, start, 160)
        self.hex_text.configure(state="normal")
        self.hex_text.delete("1.0", "end")
        self.hex_text.insert("1.0", text)
        self.hex_text.configure(state="disabled")

    # ---------- flcrc/registry ----------
    def current_flcrc(self) -> bytes:
        image = self._require_image()
        return calculate_registry_flcrc(encode_decoded(image.decoded))

    def export_flcrc(self) -> None:
        try:
            value = self.current_flcrc()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc)); return
        path = filedialog.asksaveasfilename(title="Export 13-byte registry flcrc", initialfile="flcrc.bin", defaultextension=".bin")
        if path:
            Path(path).write_bytes(value)

    def _winreg_module(self):
        if os.name != "nt":
            raise RuntimeError("Registry operations are available only on Windows.")
        import winreg  # type: ignore
        return winreg

    def _detect_registry_entries(self) -> list[RegistryEntry]:
        winreg = self._winreg_module()
        root_map = {"HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER, "HKEY_CLASSES_ROOT": winreg.HKEY_CLASSES_ROOT, "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE}
        views: list[tuple[str, int]] = [("default", 0)]
        if hasattr(winreg, "KEY_WOW64_32KEY"):
            views.extend((("32-bit", winreg.KEY_WOW64_32KEY), ("64-bit", winreg.KEY_WOW64_64KEY)))
        found: list[RegistryEntry] = []
        seen: set[tuple[str, str, int]] = set()
        for root_name, path in REGISTRY_CANDIDATES:
            for view_name, view in views:
                token = (root_name, path, view)
                if token in seen: continue
                seen.add(token)
                try:
                    with winreg.OpenKey(root_map[root_name], path, 0, winreg.KEY_READ | view) as key:
                        try:
                            value, kind = winreg.QueryValueEx(key, "flcrc")
                            value = bytes(value) if kind == winreg.REG_BINARY else None
                        except FileNotFoundError:
                            value = None
                        found.append(RegistryEntry(root_name, path, view, view_name, value))
                except (FileNotFoundError, PermissionError, OSError):
                    continue
        return found

    def _registry_match_status(self, value: Optional[bytes]) -> str:
        if value is None: return "MISSING"
        if len(value) != FLCRC_SIZE or not validate_registry_flcrc(value): return "INVALID"
        if self.image is None: return "UNCHECKED"
        if registry_flcrc_matches_header(value, self.image.header, exact=True): return "EXACT"
        if registry_flcrc_matches_header(value, self.image.header): return "ENGINE MATCH"
        return "DIFFERENT"

    def scan_registry(self) -> None:
        try:
            self.registry_entries = self._detect_registry_entries()
            self.refresh_registry_tree()
            if not self.registry_entries:
                messagebox.showwarning(APP_NAME, "No existing game registry key was found in the known locations.")
            else:
                self.log(f"Registry scan: {len(self.registry_entries)} existing key/view entries")
                self.notebook.select(self.registry_tab)
        except Exception as exc:
            self.log(traceback.format_exc())
            messagebox.showerror(APP_NAME, str(exc))

    def refresh_registry_tree(self) -> None:
        if not hasattr(self, "registry_tree"):
            return
        self.registry_tree.delete(*self.registry_tree.get_children())
        for index, entry in enumerate(self.registry_entries):
            value = entry.value
            checksum = "MISSING" if value is None else ("OK" if validate_registry_flcrc(value) else "INVALID")
            decoded = "-"
            if value is not None and validate_registry_flcrc(value):
                try: decoded = bytes_hex(decode_registry_flcrc(value))
                except SaveFormatError: decoded = "decode error"
            self.registry_tree.insert("", "end", iid=str(index), values=(
                entry.root_name, entry.path, entry.view_name,
                "(missing)" if value is None else bytes_hex(value), decoded, checksum,
                self._registry_match_status(value),
            ))

    def _selected_registry_entries(self) -> list[RegistryEntry]:
        selected = self.registry_tree.selection()
        if not selected:
            raise RuntimeError("Select at least one registry row first.")
        return [self.registry_entries[int(item)] for item in selected]

    def _selected_single_registry_entry(self) -> RegistryEntry:
        entries = self._selected_registry_entries()
        if len(entries) != 1:
            raise RuntimeError("Select exactly one registry row for this action.")
        return entries[0]

    def _registry_backup_dir(self) -> Path:
        path = config_root() / "registry_backups"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _backup_registry_value(self, entry: RegistryEntry) -> tuple[Optional[Path], Path]:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safe_root = entry.root_name.replace("HKEY_", "")
        base = self._registry_backup_dir() / f"flcrc-{stamp}-{safe_root}-{entry.view_name}"
        suffix = 0
        candidate = base
        while candidate.with_suffix(".txt").exists():
            suffix += 1
            candidate = Path(str(base) + f"-{suffix}")
        bin_path: Optional[Path] = None
        if entry.value is not None:
            bin_path = candidate.with_suffix(".bin")
            bin_path.write_bytes(entry.value)
        meta_path = candidate.with_suffix(".txt")
        meta_path.write_text(
            f"root={entry.root_name}\npath={entry.path}\nview={entry.view_name}\n"
            f"flcrc={'MISSING' if entry.value is None else bytes_hex(entry.value)}\n",
            encoding="utf-8",
        )
        return bin_path, meta_path

    def _write_selected_registry_value(self, entry: RegistryEntry, value: bytes, *, operation: str) -> None:
        if len(value) != FLCRC_SIZE or not validate_registry_flcrc(value):
            raise SaveFormatError("flcrc must be a valid 13-byte REG_BINARY value")
        bin_backup, meta_backup = self._backup_registry_value(entry)
        winreg = self._winreg_module()
        root_map = {
            "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
            "HKEY_CLASSES_ROOT": winreg.HKEY_CLASSES_ROOT,
            "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
        }
        with winreg.OpenKey(
            root_map[entry.root_name], entry.path, 0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE | entry.view,
        ) as key:
            winreg.SetValueEx(key, "flcrc", 0, winreg.REG_BINARY, value)
            readback, kind = winreg.QueryValueEx(key, "flcrc")
        if kind != winreg.REG_BINARY or bytes(readback) != value:
            raise RuntimeError("Registry read-back verification failed")
        self.log(f"Registry {operation} verified: {entry.root_name}\\{entry.path} [{entry.view_name}]")
        self.log(f"Registry backup metadata: {meta_backup}")
        if bin_backup:
            self.log(f"Registry backup bytes: {bin_backup}")

    def override_selected_registry(self) -> None:
        try:
            save_value = self.current_flcrc()
            entries = self._selected_registry_entries()
            lines = []
            for entry in entries:
                old_text = "(missing)" if entry.value is None else bytes_hex(entry.value)
                lines.append(f"{entry.root_name}\\{entry.path} [{entry.view_name}]\nOld: {old_text}")
            preview = "\n\n".join(lines[:6])
            if len(lines) > 6:
                preview += f"\n\n... and {len(lines) - 6} more selected rows"
            if not messagebox.askyesno(
                APP_NAME,
                "Override all selected registry rows from the open save?\n\n"
                f"Selected rows: {len(entries)}\nNew flcrc: {bytes_hex(save_value)}\n\n{preview}\n\n"
                "Each old value will be backed up first.",
                icon="warning",
            ):
                return
            for entry in entries:
                self._write_selected_registry_value(entry, save_value, operation="override from save")
            self.scan_registry()
            messagebox.showinfo(APP_NAME, f"Registry flcrc written and read-back verified for {len(entries)} row(s).")
        except Exception as exc:
            self.log(traceback.format_exc())
            messagebox.showerror(APP_NAME, f"Could not override registry:\n{exc}")

    def import_flcrc_to_selected_registry(self) -> None:
        try:
            entries = self._selected_registry_entries()
            path_text = filedialog.askopenfilename(
                title="Select raw 13-byte flcrc.bin",
                filetypes=(("flcrc binary", "*.bin"), ("All files", "*.*")),
            )
            if not path_text:
                return
            path = Path(path_text)
            value = path.read_bytes()
            if len(value) != FLCRC_SIZE:
                raise SaveFormatError(
                    f"{path.name} must be exactly {FLCRC_SIZE} bytes; got {len(value)} bytes"
                )
            if not validate_registry_flcrc(value):
                raise SaveFormatError(f"{path.name} has an invalid flcrc checksum")
            match = self._registry_match_status(value)
            lines = []
            for entry in entries:
                old_text = "(missing)" if entry.value is None else bytes_hex(entry.value)
                lines.append(f"{entry.root_name}\\{entry.path} [{entry.view_name}]\nOld: {old_text}")
            preview = "\n\n".join(lines[:6])
            if len(lines) > 6:
                preview += f"\n\n... and {len(lines) - 6} more selected rows"
            if not messagebox.askyesno(
                APP_NAME,
                "Import this flcrc into all selected registry rows?\n\n"
                f"File: {path}\nSelected rows: {len(entries)}\nMatch with open save: {match}\nNew: {bytes_hex(value)}\n\n{preview}\n\n"
                "Each old value will be backed up first.",
                icon="warning",
            ):
                return
            for entry in entries:
                self._write_selected_registry_value(entry, value, operation=f"import from {path.name}")
            self.scan_registry()
            messagebox.showinfo(APP_NAME, f"Imported flcrc written and read-back verified for {len(entries)} row(s).")
        except Exception as exc:
            self.log(traceback.format_exc())
            messagebox.showerror(APP_NAME, f"Could not import flcrc:\n{exc}")

    def rebind_save_to_selected_registry(self) -> None:
        try:
            image = self._require_image()
            entry = self._selected_single_registry_entry()
            if entry.value is None or not validate_registry_flcrc(entry.value):
                raise RuntimeError("Selected registry entry has no valid 13-byte flcrc")
            identity = decode_registry_flcrc(entry.value)
            old = bytes(image.header)
            if old == identity:
                messagebox.showinfo(APP_NAME, "The save identity is already an exact match.")
                return
            if not messagebox.askyesno(
                APP_NAME,
                "Replace the open save's 8-byte identity header with the selected registry identity?\n\n"
                f"Old: {bytes_hex(old)}\nNew: {bytes_hex(identity)}\n\n"
                "This changes only the decoded header in memory. Press Save afterward to write system.dat.",
                icon="warning",
            ):
                return
            image.decoded[:HEADER_SIZE] = identity
            self.set_dirty()
            self.refresh_all()
            self.log(f"Save identity rebound in memory: {bytes_hex(old)} -> {bytes_hex(identity)}")
            messagebox.showinfo(APP_NAME, "Save identity rebound in memory. Press Save to encode and write the file.")
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    # ---------- help ----------
    def run_self_test_dialog(self) -> None:
        try:
            self_test(); messagebox.showinfo(APP_NAME, "Codec self-test: OK")
        except Exception:
            messagebox.showerror(APP_NAME, traceback.format_exc())

    def show_notes(self) -> None:
        messagebox.showinfo(
            APP_NAME,
            "Confirmed for the supplied engine build:\n\n"
            "• encrypted file 0x13C6; decoded image 0x1190; payload 0x1188\n"
            "• first 8 decoded bytes are the save identity\n"
            "• flcrc is a registry/profile token, not a hardware fingerprint\n"
            "• engine identity check compares byte 0 and DWORD at bytes 4..7\n"
            "• bytes 1..3 are padding, preserved but ignored by that checker\n"
            "• card table payload+0x000C, 0x45B uint16 entries\n"
            "• card_nameeng.bin uses fixed 0x40-byte records by internal index\n"
            "• signature YUGIOH01 at payload+0x117A; magic/checksum +0x1182/+0x1184\n\n"
            "See README_VI.md and ENGINE_ANALYSIS_VI.md for transfer workflows and caveats.",
        )

    def show_about(self) -> None:
        messagebox.showinfo(APP_NAME, f"{APP_NAME} {APP_VERSION}\n\nStandard-library Python/Tkinter.\nClose the game and keep backups before writing save or registry data.")

    def on_close(self) -> None:
        if self._confirm_discard():
            self.save_config(quiet=True)
            self.destroy()


def print_info(path: Path) -> int:
    image = load_save_file(path)
    report = validate_image(image)
    encoded = image.original_encoded or encode_decoded(image.decoded)
    key_type, key_time = identity_engine_key(image.header)
    print(f"path: {path}")
    print(f"format: {report.source_format}")
    print(f"source_size: 0x{report.source_size:X}")
    print(f"outer_checksum: {bool_text(report.outer_checksum_valid)}")
    print(f"inner_magic: {bool_text(report.inner_magic_valid)}")
    print(f"inner_checksum: {bool_text(report.inner_checksum_valid)}")
    print(f"signature: {bool_text(report.signature_valid)}")
    print(f"roundtrip_exact: {bool_text(report.roundtrip_exact)}")
    print(f"header: {bytes_hex(image.header)}")
    print(f"engine_identity_key: type=0x{key_type:02X}, scalar=0x{key_time:08X}")
    print(f"flcrc: {bytes_hex(calculate_registry_flcrc(encoded))}")
    print(f"stored_total_cards: {report.stored_total_cards}")
    print(f"calculated_total_cards: {report.calculated_total_cards}")
    return 0


def cli_decode(source: Path, destination: Path) -> int:
    image = load_save_file(source)
    destination.write_bytes(image.decoded)
    print(f"Wrote 0x{DECODED_SIZE:X} decoded bytes to {destination}")
    return 0


def cli_encode(source: Path, destination: Path) -> int:
    image = load_save_file(source)
    encoded = prepare_encoded(image)
    destination.write_bytes(encoded)
    print(f"Wrote 0x{ENCODED_SIZE:X} encrypted bytes to {destination}")
    print(f"flcrc: {bytes_hex(calculate_registry_flcrc(encoded))}")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="run codec tests and exit")
    parser.add_argument("--info", type=Path, metavar="FILE", help="print save validation info")
    parser.add_argument("--decode", nargs=2, type=Path, metavar=("INPUT", "OUTPUT"))
    parser.add_argument("--encode", nargs=2, type=Path, metavar=("INPUT", "OUTPUT"))
    parser.add_argument("file", nargs="?", type=Path, help="file to open in the GUI")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        if args.self_test:
            self_test(); print("Codec self-test: OK"); return 0
        if args.info: return print_info(args.info)
        if args.decode: return cli_decode(args.decode[0], args.decode[1])
        if args.encode: return cli_encode(args.encode[0], args.encode[1])
        app = EditorApp()
        if args.file: app.after(50, lambda: app.open_file(args.file))
        app.mainloop(); return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if os.environ.get("YGO_EDITOR_DEBUG"): traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
