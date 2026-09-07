from __future__ import annotations

import ctypes
import json
import locale
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk
from tkinter import ttk
from typing import Any, Iterable


APP_NAME = "Gallery-DL"
APP_DIR = Path(__file__).resolve().parent
ASSET_DIR = APP_DIR / "assets"
STATE_FILE = APP_DIR / ".gallery-dl-gui.json"

# Palette ufficiale rilevata da gallery-dl.com (Elementor kit 11).
COLOR_BG = "#110B38"
COLOR_PANEL = "#1E1E1E"
COLOR_SURFACE = "#21136D"
COLOR_SURFACE_2 = "#1E2761"
COLOR_BORDER = "#6B3BD0"
COLOR_ACCENT = "#B2F962"
COLOR_ACCENT_HOVER = "#C8FF8A"
COLOR_TEXT = "#FFFFFF"
COLOR_MUTED = "#99A2DB"
COLOR_ERROR = "#FF3791"

ACTION_FLAGS = {
    "Scarica file": [],
    "Simula (nessun download)": ["--simulate"],
    "Mostra URL diretti": ["--get-urls"],
    "Risolvi URL intermedi": ["--resolve-urls"],
    "Esporta dati JSON": ["--dump-json"],
    "JSON con URL risolti": ["--resolve-json"],
    "Informazioni extractor": ["--extractor-info"],
    "Elenca keyword disponibili": ["--list-keywords"],
}

INPUT_FLAGS = {
    "Normale": "--input-file",
    "Commenta gli URL completati": "--input-file-comment",
    "Elimina gli URL completati": "--input-file-delete",
}

VERBOSITY_FLAGS = {
    "Normale": [],
    "Dettagliata": ["--verbose"],
    "Solo avvisi/errori": ["--warning"],
    "Silenziosa": ["--quiet"],
}


def default_config_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "gallery-dl" / "config.json"
    return Path.home() / "gallery-dl" / "config.json"


def split_nonempty_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def split_windows_arguments(command_line: str) -> list[str]:
    """Use Windows' own parser so quoted advanced arguments behave like cmd.exe."""
    command_line = command_line.strip()
    if not command_line:
        return []
    argc = ctypes.c_int()
    parser = ctypes.windll.shell32.CommandLineToArgvW
    parser.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    parser.restype = ctypes.POINTER(ctypes.c_wchar_p)
    argv = parser(command_line, ctypes.byref(argc))
    if not argv:
        raise ValueError("Impossibile interpretare gli argomenti avanzati.")
    try:
        return [argv[index] for index in range(argc.value)]
    finally:
        local_free = ctypes.windll.kernel32.LocalFree
        local_free.argtypes = [ctypes.c_void_p]
        local_free.restype = ctypes.c_void_p
        local_free(argv)


def looks_like_gallery_input(value: str) -> bool:
    value = value.strip()
    if not value or any(char in value for char in "\r\n"):
        return False
    if re.match(r"^https?://", value, re.IGNORECASE):
        return True
    # gallery-dl accepts extractor prefixes (pixiv:https://...), recursive URLs,
    # oauth:SITE, and other pseudo schemes.
    return bool(re.match(r"^[a-zA-Z0-9_-]+:(?:https?://|[a-zA-Z0-9_.-]+$)", value))


def add_value(args: list[str], flag: str, value: Any) -> None:
    text = str(value).strip()
    if text:
        args.extend((flag, text))


def add_repeated_lines(args: list[str], flag: str, value: str) -> None:
    for line in split_nonempty_lines(value):
        args.extend((flag, line))


def build_gallery_command(engine: list[str], values: dict[str, Any], urls: list[str]) -> list[str]:
    """Build a shell-free gallery-dl command from GUI values."""
    args = list(engine)
    args.extend(("--no-colors", "--no-input"))

    add_value(args, "--destination", values.get("destination"))
    add_value(args, "--directory", values.get("directory"))
    add_value(args, "--filename", values.get("filename"))
    add_value(args, "--restrict-filenames", values.get("restrict_filenames"))
    add_value(args, "--extractors", values.get("external_extractors"))
    if values.get("compat"):
        args.append("--compat")

    args.extend(ACTION_FLAGS.get(values.get("action", "Scarica file"), []))
    args.extend(VERBOSITY_FLAGS.get(values.get("verbosity", "Normale"), []))
    if values.get("write_pages"):
        args.append("--write-pages")
    if values.get("print_traffic"):
        args.append("--print-traffic")
    add_value(args, "--write-log", values.get("log_file"))
    add_value(args, "--write-unsupported", values.get("unsupported_file"))
    add_value(args, "--error-file", values.get("error_file"))
    add_repeated_lines(args, "--Print", values.get("print_formats", ""))

    add_value(args, "--retries", values.get("retries"))
    add_value(args, "--user-agent", values.get("user_agent"))
    add_value(args, "--http-timeout", values.get("timeout"))
    add_value(args, "--proxy", values.get("proxy"))
    add_value(args, "--xff", values.get("xff"))
    add_value(args, "--source-address", values.get("source_address"))
    ip_version = values.get("ip_version")
    if ip_version == "Solo IPv4":
        args.append("--force-ipv4")
    elif ip_version == "Solo IPv6":
        args.append("--force-ipv6")
    if values.get("no_check_certificate"):
        args.append("--no-check-certificate")

    add_value(args, "--limit-rate", values.get("limit_rate"))
    add_value(args, "--chunk-size", values.get("chunk_size"))
    if values.get("no_part"):
        args.append("--no-part")
    if values.get("overwrite"):
        args.append("--no-skip")
    if values.get("no_mtime"):
        args.append("--no-mtime")
    if values.get("no_download"):
        args.append("--no-download")

    for key, flag in (
        ("sleep", "--sleep"),
        ("sleep_skip", "--sleep-skip"),
        ("sleep_extractor", "--sleep-extractor"),
        ("sleep_request", "--sleep-request"),
        ("sleep_retries", "--sleep-retries"),
        ("sleep_429", "--sleep-429"),
    ):
        add_value(args, flag, values.get(key))

    config_file = str(values.get("config_file", "")).strip()
    if config_file:
        config_type = values.get("config_type", "Auto")
        config_flag = {
            "JSON": "--config-json",
            "YAML": "--config-yaml",
            "TOML": "--config-toml",
        }.get(config_type, "--config")
        args.extend((config_flag, config_file))
    if values.get("ignore_config"):
        args.append("--config-ignore")
    add_repeated_lines(args, "--option", values.get("custom_options", ""))

    add_value(args, "--username", values.get("username"))
    add_value(args, "--password", values.get("password"))
    if values.get("netrc"):
        args.append("--netrc")
    add_value(args, "--cookies", values.get("cookies_file"))
    add_value(args, "--cookies-export", values.get("cookies_export"))
    add_value(args, "--cookies-from-browser", values.get("browser_cookies"))

    for key, flag in (
        ("abort", "--abort"),
        ("terminate", "--terminate"),
        ("filesize_min", "--filesize-min"),
        ("filesize_max", "--filesize-max"),
        ("archive", "--download-archive"),
        ("date_before", "--date-before"),
        ("date_after", "--date-after"),
        ("blacklist", "--blacklist"),
        ("whitelist", "--whitelist"),
        ("tags_blacklist", "--tags-blacklist"),
        ("tags_whitelist", "--tags-whitelist"),
        ("range", "--range"),
        ("post_range", "--post-range"),
        ("child_range", "--child-range"),
        ("filter", "--filter"),
        ("post_filter", "--post-filter"),
        ("child_filter", "--child-filter"),
    ):
        add_value(args, flag, values.get(key))

    if values.get("write_metadata"):
        args.append("--write-metadata")
    if values.get("write_info_json"):
        args.append("--write-info-json")
    if values.get("write_tags"):
        args.append("--write-tags")
    container = values.get("container")
    if container == "ZIP":
        args.append("--zip")
    elif container == "CBZ":
        args.append("--cbz")
    add_value(args, "--mtime", values.get("metadata_mtime"))
    add_value(args, "--ugoira", values.get("ugoira"))
    add_value(args, "--rename", values.get("rename_from"))
    add_value(args, "--rename-to", values.get("rename_to"))
    add_value(args, "--exec", values.get("exec_each"))
    add_value(args, "--exec-after", values.get("exec_after"))
    add_repeated_lines(args, "--postprocessor", values.get("postprocessors", ""))
    add_repeated_lines(args, "--postprocessor-option", values.get("postprocessor_options", ""))
    if values.get("no_postprocessors"):
        args.append("--no-postprocessors")

    input_file = str(values.get("input_file", "")).strip()
    if input_file:
        args.extend((INPUT_FLAGS.get(values.get("input_mode"), "--input-file"), input_file))

    args.extend(split_windows_arguments(str(values.get("raw_arguments", ""))))
    args.extend(urls)
    return args


def engine_candidates() -> list[list[str]]:
    candidates: list[list[str]] = []
    venv_python = APP_DIR / ".venv" / "Scripts" / "python.exe"
    if venv_python.is_file():
        candidates.append([str(venv_python), "-m", "gallery_dl"])

    current = Path(sys.executable)
    current_console = current.with_name("python.exe") if current.name.lower() == "pythonw.exe" else current
    if current_console.is_file():
        candidates.append([str(current_console), "-m", "gallery_dl"])

    executable = shutil.which("gallery-dl")
    if executable:
        candidates.append([executable])

    unique: list[list[str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase("\0".join(candidate))
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def probe_engine(engine: list[str], timeout: int = 15) -> str:
    completed = subprocess.run(
        engine + ["--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding=locale.getpreferredencoding(False),
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        timeout=timeout,
        check=False,
    )
    version = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else ""
    if completed.returncode != 0 or not re.match(r"^\d+\.\d+(?:\.\d+)?", version):
        raise RuntimeError(completed.stdout.strip() or "gallery-dl non risponde correttamente.")
    return version


def locate_engine() -> tuple[list[str], str]:
    errors: list[str] = []
    for candidate in engine_candidates():
        try:
            return candidate, probe_engine(candidate)
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            errors.append(str(exc))
    detail = f" ({'; '.join(errors)})" if errors else ""
    raise RuntimeError("gallery-dl non è installato. Avvia INSTALL.bat." + detail)


class ToolTip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.popup: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _event: tk.Event) -> None:
        if self.popup or not self.text:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        self.popup = tk.Toplevel(self.widget)
        self.popup.wm_overrideredirect(True)
        self.popup.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self.popup,
            text=self.text,
            justify="left",
            background="#FFF6C8",
            foreground="#1E1E1E",
            relief="solid",
            borderwidth=1,
            padx=7,
            pady=4,
            font=("Segoe UI", 9),
        ).pack()

    def _hide(self, _event: tk.Event | None = None) -> None:
        if self.popup:
            self.popup.destroy()
            self.popup = None


class ConfigEditor:
    def __init__(self, parent: tk.Tk, path: Path) -> None:
        self.path = path
        self.window = tk.Toplevel(parent)
        self.window.title(f"Configurazione gallery-dl — {path}")
        self.window.geometry("850x650")
        self.window.minsize(620, 420)
        self.window.configure(bg=COLOR_BG)

        bar = ttk.Frame(self.window, padding=(12, 10))
        bar.pack(fill="x")
        ttk.Label(bar, text=str(path), style="Subtitle.TLabel").pack(side="left", fill="x", expand=True)
        ttk.Button(bar, text="Formatta / valida", command=self.validate, style="Action.TButton").pack(side="right")
        ttk.Button(bar, text="Salva", command=self.save, style="Primary.TButton").pack(side="right", padx=(0, 8))

        body = ttk.Frame(self.window, padding=(12, 0, 12, 12))
        body.pack(fill="both", expand=True)
        self.text = tk.Text(
            body,
            wrap="none",
            undo=True,
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
            insertbackground=COLOR_ACCENT,
            selectbackground=COLOR_BORDER,
            font=("Cascadia Mono", 10),
            padx=10,
            pady=10,
        )
        ybar = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        xbar = ttk.Scrollbar(body, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        if path.is_file():
            content = path.read_text(encoding="utf-8-sig")
        else:
            content = json.dumps(
                {
                    "extractor": {
                        "base-directory": str(Path.home() / "Downloads" / "gallery-dl"),
                        "path-restrict": "windows+",
                    }
                },
                indent=4,
                ensure_ascii=False,
            )
        self.text.insert("1.0", content + ("\n" if not content.endswith("\n") else ""))
        self.text.focus_set()

    def _parsed(self) -> Any:
        try:
            return json.loads(self.text.get("1.0", "end-1c"))
        except json.JSONDecodeError as exc:
            self.text.mark_set("insert", f"{exc.lineno}.{exc.colno - 1}")
            self.text.see("insert")
            raise ValueError(f"JSON non valido alla riga {exc.lineno}, colonna {exc.colno}:\n{exc.msg}") from exc

    def validate(self) -> None:
        try:
            data = self._parsed()
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self.window)
            return
        formatted = json.dumps(data, indent=4, ensure_ascii=False)
        self.text.delete("1.0", "end")
        self.text.insert("1.0", formatted + "\n")
        messagebox.showinfo(APP_NAME, "Configurazione JSON valida.", parent=self.window)

    def save(self) -> None:
        try:
            data = self._parsed()
            if not isinstance(data, dict):
                raise ValueError("La configurazione principale deve essere un oggetto JSON.")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
        except (OSError, ValueError) as exc:
            messagebox.showerror(APP_NAME, f"Impossibile salvare:\n{exc}", parent=self.window)
            return
        messagebox.showinfo(APP_NAME, "Configurazione salvata.", parent=self.window)


class GalleryDlApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1120x850")
        self.root.minsize(900, 700)
        self.root.configure(bg=COLOR_BG)

        self.messages: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.cancel_requested = False
        self.active_kind: str | None = None
        self.active_action = "Scarica file"
        self.destination_before: dict[str, tuple[int, int]] = {}
        self.engine: list[str] | None = None
        self.engine_version = "non verificata"
        self.vars: dict[str, tk.Variable] = {}
        self.action_buttons: list[ttk.Button] = []

        self._configure_style()
        self._create_variables()
        self._load_state()
        self._build_ui()
        self._load_logo()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._drain_messages)
        self.root.after(250, self._probe_engine_async)

    def sv(self, name: str, default: str = "") -> tk.StringVar:
        var = tk.StringVar(value=default)
        self.vars[name] = var
        return var

    def bv(self, name: str, default: bool = False) -> tk.BooleanVar:
        var = tk.BooleanVar(value=default)
        self.vars[name] = var
        return var

    def _create_variables(self) -> None:
        downloads = Path.home() / "Downloads" / "gallery-dl"
        self.sv("destination", str(downloads))
        self.sv("directory")
        self.sv("filename")
        self.sv("restrict_filenames", "windows+")
        self.sv("external_extractors")
        self.bv("compat")
        self.sv("action", "Scarica file")
        self.sv("input_file")
        self.sv("input_mode", "Normale")
        self.sv("verbosity", "Normale")
        self.bv("write_pages")
        self.bv("print_traffic")
        self.sv("log_file")
        self.sv("unsupported_file")
        self.sv("error_file")
        self.sv("print_formats")
        self.sv("retries", "4")
        self.sv("user_agent")
        self.sv("timeout", "30")
        self.sv("proxy")
        self.sv("xff")
        self.sv("source_address")
        self.sv("ip_version", "Automatico")
        self.bv("no_check_certificate")
        self.sv("limit_rate")
        self.sv("chunk_size", "32k")
        self.bv("no_part")
        self.bv("overwrite")
        self.bv("no_mtime")
        self.bv("no_download")
        for name in ("sleep", "sleep_skip", "sleep_extractor", "sleep_request", "sleep_retries", "sleep_429"):
            self.sv(name)
        self.sv("config_file")
        self.sv("config_type", "Auto")
        self.bv("ignore_config")
        self.sv("custom_options")
        self.sv("username")
        self.sv("password")
        self.bv("netrc")
        self.sv("cookies_file")
        self.sv("cookies_export")
        self.sv("browser_cookies")
        for name in (
            "abort", "terminate", "filesize_min", "filesize_max", "archive", "date_before", "date_after",
            "blacklist", "whitelist", "tags_blacklist", "tags_whitelist", "range", "post_range", "child_range",
            "filter", "post_filter", "child_filter",
        ):
            self.sv(name)
        self.bv("write_metadata")
        self.bv("write_info_json")
        self.bv("write_tags")
        self.sv("container", "Nessuno")
        self.sv("metadata_mtime")
        self.sv("ugoira")
        self.sv("rename_from")
        self.sv("rename_to")
        self.sv("exec_each")
        self.sv("exec_after")
        self.sv("postprocessors")
        self.sv("postprocessor_options")
        self.bv("no_postprocessors")
        self.sv("raw_arguments")
        self.sv("update_channel", "Stabile")
        self.sv("status", "Pronto")
        self.sv("detail", "Incolla uno o più URL per iniziare.")
        self.sv("engine_status", "gallery-dl: verifica in corso…")
        self.progress_var = tk.DoubleVar(value=0)

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=COLOR_BG)
        style.configure("Panel.TFrame", background=COLOR_PANEL)
        style.configure("TLabel", background=COLOR_BG, foreground=COLOR_TEXT, font=("Segoe UI", 10))
        style.configure("Subtitle.TLabel", foreground=COLOR_MUTED, font=("Segoe UI", 9))
        style.configure("Section.TLabel", foreground=COLOR_ACCENT, font=("Segoe UI Semibold", 10))
        style.configure("Status.TLabel", foreground=COLOR_ACCENT, font=("Segoe UI Semibold", 10))
        # ttk's canonical style name uses a lowercase "f" in Labelframe.
        style.configure("TLabelframe", background=COLOR_BG, foreground=COLOR_ACCENT, bordercolor=COLOR_BORDER)
        style.configure("TLabelframe.Label", background=COLOR_BG, foreground=COLOR_ACCENT, font=("Segoe UI Semibold", 10))
        style.configure(
            "TEntry", fieldbackground=COLOR_PANEL, foreground=COLOR_TEXT, insertcolor=COLOR_TEXT,
            bordercolor=COLOR_BORDER, lightcolor=COLOR_BORDER, darkcolor=COLOR_BORDER, padding=5,
        )
        style.map("TEntry", bordercolor=[("focus", COLOR_ACCENT)], foreground=[("disabled", COLOR_MUTED)])
        style.configure(
            "TCombobox", fieldbackground=COLOR_PANEL, background=COLOR_SURFACE, foreground=COLOR_TEXT,
            arrowcolor=COLOR_TEXT, bordercolor=COLOR_BORDER, lightcolor=COLOR_BORDER, darkcolor=COLOR_BORDER, padding=5,
        )
        style.map(
            "TCombobox", fieldbackground=[("readonly", COLOR_PANEL)], background=[("active", COLOR_SURFACE_2)],
            foreground=[("readonly", COLOR_TEXT)], bordercolor=[("focus", COLOR_ACCENT)],
        )
        style.configure("TCheckbutton", background=COLOR_BG, foreground=COLOR_TEXT, focuscolor=COLOR_BG)
        style.map(
            "TCheckbutton", background=[("active", COLOR_BG)], foreground=[("active", COLOR_TEXT)],
            indicatorbackground=[("selected", COLOR_ACCENT), ("!selected", COLOR_PANEL)],
        )
        style.configure(
            "Primary.TButton", font=("Segoe UI Semibold", 10), padding=(16, 8), background=COLOR_ACCENT,
            foreground=COLOR_BG, bordercolor=COLOR_ACCENT, lightcolor=COLOR_ACCENT, darkcolor=COLOR_ACCENT,
        )
        style.map(
            "Primary.TButton", background=[("active", COLOR_ACCENT_HOVER), ("disabled", COLOR_SURFACE_2)],
            foreground=[("disabled", COLOR_MUTED)],
        )
        style.configure(
            "Action.TButton", font=("Segoe UI", 9), padding=(10, 6), background=COLOR_SURFACE_2,
            foreground=COLOR_TEXT, bordercolor=COLOR_BORDER, lightcolor=COLOR_BORDER, darkcolor=COLOR_BORDER,
        )
        style.map("Action.TButton", background=[("active", COLOR_SURFACE), ("pressed", COLOR_SURFACE)])
        style.configure("TNotebook", background=COLOR_BG, bordercolor=COLOR_BORDER, tabmargins=(0, 4, 0, 0))
        style.configure("TNotebook.Tab", background=COLOR_PANEL, foreground=COLOR_MUTED, padding=(13, 8))
        style.map(
            "TNotebook.Tab", background=[("selected", COLOR_SURFACE_2), ("active", COLOR_SURFACE)],
            foreground=[("selected", COLOR_ACCENT), ("active", COLOR_TEXT)],
        )
        style.configure(
            "Lime.Horizontal.TProgressbar", troughcolor=COLOR_PANEL, background=COLOR_ACCENT,
            bordercolor=COLOR_BORDER, lightcolor=COLOR_ACCENT, darkcolor=COLOR_ACCENT,
        )
        style.configure("Vertical.TScrollbar", background=COLOR_SURFACE_2, troughcolor=COLOR_PANEL, arrowcolor=COLOR_TEXT)

    def _build_ui(self) -> None:
        root = ttk.Frame(self.root, padding=(18, 14, 18, 14))
        root.grid(row=0, column=0, sticky="nsew")
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=3)
        root.rowconfigure(4, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header.columnconfigure(1, weight=1)
        self.logo_label = ttk.Label(header)
        self.logo_label.grid(row=0, column=0, rowspan=2, sticky="w")
        ttk.Label(
            header, text="Interfaccia completa per gallerie, raccolte, profili e immagini",
            style="Subtitle.TLabel",
        ).grid(row=1, column=1, sticky="sw", padx=(14, 0))
        ttk.Label(header, textvariable=self.vars["engine_status"], style="Subtitle.TLabel").grid(
            row=0, column=2, rowspan=2, sticky="e"
        )

        self.notebook = ttk.Notebook(root)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        self._build_download_tab()
        self._build_selection_tab()
        self._build_auth_tab()
        self._build_output_tab()
        self._build_advanced_tab()

        actions = ttk.Frame(root)
        actions.grid(row=2, column=0, sticky="ew", pady=(12, 8))
        self.start_button = ttk.Button(actions, text="Avvia", command=self.start, style="Primary.TButton")
        self.start_button.pack(side="left")
        self.cancel_button = ttk.Button(
            actions, text="Annulla", command=self.cancel, state="disabled", style="Action.TButton"
        )
        self.cancel_button.pack(side="left", padx=(8, 0))
        self._action_button(actions, "Anteprima comando", self.show_command).pack(side="left", padx=(8, 0))
        self._action_button(actions, "Aggiorna", self.start_update).pack(side="left", padx=(8, 0))
        self._action_button(actions, "Apri cartella", self.open_destination).pack(side="right")

        progress = ttk.Frame(root)
        progress.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        progress.columnconfigure(0, weight=1)
        ttk.Label(progress, textvariable=self.vars["status"], style="Status.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(progress, textvariable=self.vars["detail"], style="Subtitle.TLabel").grid(row=0, column=1, sticky="e")
        self.progress = ttk.Progressbar(
            progress, maximum=100, variable=self.progress_var, style="Lime.Horizontal.TProgressbar"
        )
        self.progress.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0))

        log_frame = ttk.Frame(root)
        log_frame.grid(row=4, column=0, sticky="nsew")
        log_frame.rowconfigure(1, weight=1)
        log_frame.columnconfigure(0, weight=1)
        log_bar = ttk.Frame(log_frame)
        log_bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 5))
        ttk.Label(log_bar, text="Attività", style="Section.TLabel").pack(side="left")
        ttk.Button(log_bar, text="Pulisci", command=self.clear_log, style="Action.TButton").pack(side="right")
        self.log = tk.Text(
            log_frame, height=7, wrap="word", state="disabled", bg=COLOR_PANEL, fg=COLOR_TEXT,
            insertbackground=COLOR_TEXT, selectbackground=COLOR_BORDER, font=("Cascadia Mono", 9),
            relief="solid", borderwidth=1, highlightthickness=1, highlightbackground=COLOR_BORDER,
            highlightcolor=COLOR_ACCENT, padx=8, pady=8,
        )
        self.log.grid(row=1, column=0, sticky="nsew")
        bar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        bar.grid(row=1, column=1, sticky="ns")
        self.log.configure(yscrollcommand=bar.set)

    def _new_tab(self, title: str) -> ttk.Frame:
        tab = ttk.Frame(self.notebook, padding=(14, 12))
        self.notebook.add(tab, text=title)
        tab.columnconfigure(0, weight=1)
        return tab

    def _action_button(self, parent: tk.Widget, text: str, command: Any) -> ttk.Button:
        button = ttk.Button(parent, text=text, command=command, style="Action.TButton")
        self.action_buttons.append(button)
        return button

    def _entry_row(
        self, parent: ttk.Frame, row: int, label: str, name: str, *, browse: str | None = None,
        tooltip: str = "", secret: bool = False,
    ) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
        entry = ttk.Entry(parent, textvariable=self.vars[name], show="•" if secret else "")
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        if tooltip:
            ToolTip(entry, tooltip)
        if browse:
            ttk.Button(
                parent, text="Sfoglia…", command=lambda n=name, kind=browse: self._browse(n, kind), style="Action.TButton"
            ).grid(row=row, column=2, padx=(7, 0), pady=4)
        return entry

    def _combo_row(self, parent: ttk.Frame, row: int, label: str, name: str, choices: Iterable[str]) -> ttk.Combobox:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
        combo = ttk.Combobox(parent, textvariable=self.vars[name], values=list(choices), state="readonly")
        combo.grid(row=row, column=1, sticky="ew", pady=4)
        return combo

    def _check(self, parent: ttk.Frame, row: int, text: str, name: str, column: int = 0) -> ttk.Checkbutton:
        check = ttk.Checkbutton(parent, text=text, variable=self.vars[name])
        check.grid(row=row, column=column, columnspan=2, sticky="w", pady=3)
        return check

    def _build_download_tab(self) -> None:
        tab = self._new_tab("Download")
        tab.rowconfigure(1, weight=1)
        ttk.Label(tab, text="URL (uno per riga)", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        url_area = ttk.Frame(tab)
        url_area.grid(row=1, column=0, sticky="nsew", pady=(5, 9))
        url_area.columnconfigure(0, weight=1)
        url_area.rowconfigure(0, weight=1)
        self.urls_text = tk.Text(
            url_area, height=6, wrap="word", bg=COLOR_PANEL, fg=COLOR_TEXT, insertbackground=COLOR_ACCENT,
            selectbackground=COLOR_BORDER, font=("Segoe UI", 10), padx=8, pady=7,
            highlightthickness=1, highlightbackground=COLOR_BORDER, highlightcolor=COLOR_ACCENT,
        )
        self.urls_text.grid(row=0, column=0, sticky="nsew")
        buttons = ttk.Frame(url_area)
        buttons.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        ttk.Button(buttons, text="Incolla", command=self.paste_urls, style="Action.TButton").pack(fill="x")
        ttk.Button(buttons, text="Pulisci", command=lambda: self.urls_text.delete("1.0", "end"), style="Action.TButton").pack(
            fill="x", pady=(6, 0)
        )

        options = ttk.Frame(tab)
        options.grid(row=2, column=0, sticky="ew")
        options.columnconfigure(1, weight=1)
        options.columnconfigure(4, weight=1)
        ttk.Label(options, text="Destinazione").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(options, textvariable=self.vars["destination"]).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(options, text="Sfoglia…", command=lambda: self._browse("destination", "directory"), style="Action.TButton").grid(
            row=0, column=2, padx=(7, 18), pady=4
        )
        ttk.Label(options, text="Operazione").grid(row=0, column=3, sticky="w", padx=(0, 8), pady=4)
        ttk.Combobox(
            options, textvariable=self.vars["action"], values=list(ACTION_FLAGS), state="readonly", width=26
        ).grid(row=0, column=4, sticky="ew", pady=4)

        ttk.Label(options, text="File con URL").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(options, textvariable=self.vars["input_file"]).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Button(options, text="Sfoglia…", command=lambda: self._browse("input_file", "file"), style="Action.TButton").grid(
            row=1, column=2, padx=(7, 18), pady=4
        )
        ttk.Label(options, text="Gestione file").grid(row=1, column=3, sticky="w", padx=(0, 8), pady=4)
        ttk.Combobox(
            options, textvariable=self.vars["input_mode"], values=list(INPUT_FLAGS), state="readonly", width=26
        ).grid(row=1, column=4, sticky="ew", pady=4)

        ttk.Label(options, text="Sottocartella esatta").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(options, textvariable=self.vars["directory"]).grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Label(options, text="Nome file").grid(row=2, column=3, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(options, textvariable=self.vars["filename"]).grid(row=2, column=4, sticky="ew", pady=4)
        ttk.Label(
            tab, text="Lascia vuoti sottocartella e nome per usare i formati predefiniti dell’extractor.", style="Subtitle.TLabel"
        ).grid(row=3, column=0, sticky="w", pady=(4, 0))

    def _build_selection_tab(self) -> None:
        tab = self._new_tab("Selezione")
        columns = ttk.Frame(tab)
        columns.grid(row=0, column=0, sticky="nsew")
        columns.columnconfigure((0, 1), weight=1, uniform="selection")
        left = ttk.LabelFrame(columns, text="Intervalli e filtri", padding=10)
        right = ttk.LabelFrame(columns, text="Archivio e limiti", padding=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        right.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        for frame in (left, right):
            frame.columnconfigure(1, weight=1)

        self._entry_row(left, 0, "Intervallo file", "range", tooltip="Esempi: 5, 8-20, 1:24:3")
        self._entry_row(left, 1, "Intervallo post", "post_range")
        self._entry_row(left, 2, "Intervallo figli", "child_range")
        self._entry_row(left, 3, "Filtro file", "filter", tooltip="Espressione Python sui metadati dell’extractor")
        self._entry_row(left, 4, "Filtro post", "post_filter")
        self._entry_row(left, 5, "Filtro figli", "child_filter")
        self._entry_row(left, 6, "Categorie escluse", "blacklist")
        self._entry_row(left, 7, "Categorie ammesse", "whitelist")
        self._entry_row(left, 8, "Tag esclusi", "tags_blacklist")
        self._entry_row(left, 9, "Tag ammessi", "tags_whitelist")

        self._entry_row(right, 0, "Archivio download", "archive", browse="save_db")
        self._entry_row(right, 1, "Data successiva", "date_after", tooltip="ISO 8601, ad es. 2026-01-31")
        self._entry_row(right, 2, "Data precedente", "date_before")
        self._entry_row(right, 3, "Dimensione minima", "filesize_min", tooltip="Esempi: 500k, 2.5M")
        self._entry_row(right, 4, "Dimensione massima", "filesize_max")
        self._entry_row(right, 5, "Ferma dopo skip", "abort", tooltip="N[:TARGET], ad es. 5 oppure 5:manga")
        self._entry_row(right, 6, "Termina dopo skip", "terminate")
        self._check(right, 7, "Sovrascrivi file esistenti", "overwrite")
        ttk.Label(
            right,
            text="L’archivio evita duplicati anche quando cambiano cartella o nome file.",
            style="Subtitle.TLabel",
            wraplength=390,
        ).grid(row=8, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def _build_auth_tab(self) -> None:
        tab = self._new_tab("Accesso e rete")
        columns = ttk.Frame(tab)
        columns.grid(row=0, column=0, sticky="nsew")
        columns.columnconfigure((0, 1), weight=1, uniform="auth")
        auth = ttk.LabelFrame(columns, text="Autenticazione e cookie", padding=10)
        network = ttk.LabelFrame(columns, text="Rete e velocità", padding=10)
        auth.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        network.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        for frame in (auth, network):
            frame.columnconfigure(1, weight=1)

        self._entry_row(auth, 0, "Cookie dal browser", "browser_cookies", tooltip="Esempio: chrome oppure firefox:default-release")
        quick = ttk.Frame(auth)
        quick.grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Label(quick, text="Scelta rapida:").pack(side="left")
        for browser in ("chrome", "edge", "firefox", "brave", "opera"):
            ttk.Button(
                quick, text=browser.title(), command=lambda b=browser: self.vars["browser_cookies"].set(b),
                style="Action.TButton",
            ).pack(side="left", padx=(5, 0))
        self._entry_row(auth, 2, "File cookies.txt", "cookies_file", browse="file")
        self._entry_row(auth, 3, "Esporta cookie in", "cookies_export", browse="save_txt")
        self._entry_row(auth, 4, "Nome utente", "username")
        self._entry_row(auth, 5, "Password / API key", "password", secret=True)
        self._check(auth, 6, "Usa credenziali .netrc", "netrc")
        ttk.Label(
            auth, text="La password non viene salvata nelle preferenze della GUI.", style="Subtitle.TLabel"
        ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(8, 0))

        self._entry_row(network, 0, "Proxy", "proxy", tooltip="HTTP(S) o SOCKS, ad es. socks5h://127.0.0.1:1080")
        self._entry_row(network, 1, "User-Agent", "user_agent")
        self._entry_row(network, 2, "Tentativi", "retries")
        self._entry_row(network, 3, "Timeout (secondi)", "timeout")
        self._entry_row(network, 4, "Limite velocità", "limit_rate", tooltip="Esempi: 500k, 2.5M, 800k-2M")
        self._entry_row(network, 5, "Dimensione chunk", "chunk_size")
        self._entry_row(network, 6, "X-Forwarded-For", "xff")
        self._entry_row(network, 7, "Indirizzo sorgente", "source_address")
        self._combo_row(network, 8, "Versione IP", "ip_version", ("Automatico", "Solo IPv4", "Solo IPv6"))
        self._check(network, 9, "Disabilita verifica certificato HTTPS", "no_check_certificate")

    def _build_output_tab(self) -> None:
        tab = self._new_tab("Output")
        columns = ttk.Frame(tab)
        columns.grid(row=0, column=0, sticky="nsew")
        columns.columnconfigure((0, 1), weight=1, uniform="output")
        files = ttk.LabelFrame(columns, text="File e metadati", padding=10)
        logs = ttk.LabelFrame(columns, text="Diagnostica e post-processing", padding=10)
        files.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        logs.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        for frame in (files, logs):
            frame.columnconfigure(1, weight=1)

        self._check(files, 0, "Scrivi metadati per ogni file", "write_metadata")
        self._check(files, 1, "Scrivi info.json della galleria", "write_info_json")
        self._check(files, 2, "Scrivi tag in file di testo", "write_tags")
        self._combo_row(files, 3, "Contenitore", "container", ("Nessuno", "ZIP", "CBZ"))
        self._entry_row(files, 4, "mtime dai metadati", "metadata_mtime", tooltip="Esempio: date oppure status[date]")
        self._entry_row(files, 5, "Converti Ugoira", "ugoira", tooltip="webm, mp4, gif, vp8, vp9, vp9-lossless, copy, zip")
        self._entry_row(files, 6, "Rinomina dal formato", "rename_from")
        self._entry_row(files, 7, "Rinomina al formato", "rename_to")
        self._check(files, 8, "Non creare file .part", "no_part")
        self._check(files, 9, "Non applicare Last-Modified", "no_mtime")
        self._check(files, 10, "Estrai senza scaricare file", "no_download")

        self._combo_row(logs, 0, "Livello log", "verbosity", VERBOSITY_FLAGS.keys())
        self._entry_row(logs, 1, "File log", "log_file", browse="save_log")
        self._entry_row(logs, 2, "URL non supportati", "unsupported_file", browse="save_txt")
        self._entry_row(logs, 3, "URL con errore", "error_file", browse="save_txt")
        self._check(logs, 4, "Salva pagine intermedie (debug)", "write_pages")
        self._check(logs, 5, "Mostra traffico HTTP", "print_traffic")
        self._entry_row(logs, 6, "Comando per ogni file", "exec_each")
        self._entry_row(logs, 7, "Comando finale", "exec_after")
        self._check(logs, 8, "Disabilita tutti i postprocessor", "no_postprocessors")
        ttk.Label(logs, text="--Print (uno per riga)").grid(row=9, column=0, sticky="nw", padx=(0, 8), pady=4)
        self.print_text = tk.Text(
            logs, height=3, bg=COLOR_PANEL, fg=COLOR_TEXT, insertbackground=COLOR_ACCENT,
            highlightthickness=1, highlightbackground=COLOR_BORDER, font=("Cascadia Mono", 9),
        )
        self.print_text.grid(row=9, column=1, columnspan=2, sticky="ew", pady=4)

    def _build_advanced_tab(self) -> None:
        tab = self._new_tab("Avanzate")
        tab.columnconfigure((0, 1), weight=1, uniform="advanced")
        left = ttk.LabelFrame(tab, text="Configurazione completa", padding=10)
        right = ttk.LabelFrame(tab, text="Attese, manutenzione e strumenti", padding=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        right.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        for frame in (left, right):
            frame.columnconfigure(1, weight=1)

        self._entry_row(left, 0, "File config", "config_file", browse="config")
        self._combo_row(left, 1, "Tipo config", "config_type", ("Auto", "JSON", "YAML", "TOML"))
        self._check(left, 2, "Ignora configurazioni predefinite", "ignore_config")
        self._entry_row(left, 3, "Restrizione nomi", "restrict_filenames")
        self._entry_row(left, 4, "Extractor esterni", "external_extractors", browse="directory")
        self._check(left, 5, "Compatibilità nomi legacy", "compat")
        ttk.Label(left, text="Opzioni KEY=VALUE (una per riga)").grid(row=6, column=0, columnspan=3, sticky="w", pady=(3, 2))
        self.options_text = self._multiline(left, 7, 1)
        pp_grid = ttk.Frame(left)
        pp_grid.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(2, 0))
        pp_grid.columnconfigure((0, 1), weight=1, uniform="pp")
        pp_names = ttk.Frame(pp_grid)
        pp_options = ttk.Frame(pp_grid)
        pp_names.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        pp_options.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        pp_names.columnconfigure(0, weight=1)
        pp_options.columnconfigure(0, weight=1)
        ttk.Label(pp_names, text="Postprocessor (uno per riga)").grid(row=0, column=0, sticky="w", pady=(0, 2))
        self.postprocessors_text = self._multiline(pp_names, 1, 1)
        ttk.Label(pp_options, text="Opzioni KEY=VALUE").grid(row=0, column=0, sticky="w", pady=(0, 2))
        self.postprocessor_options_text = self._multiline(pp_options, 1, 1)
        ttk.Label(left, text="Argomenti CLI aggiuntivi").grid(row=9, column=0, columnspan=3, sticky="w", pady=(2, 2))
        ttk.Entry(left, textvariable=self.vars["raw_arguments"]).grid(row=10, column=0, columnspan=3, sticky="ew")

        self._entry_row(right, 0, "Attesa download", "sleep")
        self._entry_row(right, 1, "Attesa dopo skip", "sleep_skip")
        self._entry_row(right, 2, "Attesa extractor", "sleep_extractor")
        self._entry_row(right, 3, "Attesa richieste", "sleep_request")
        self._entry_row(right, 4, "Attesa retry", "sleep_retries")
        self._entry_row(right, 5, "Attesa errore 429", "sleep_429")
        tools = ttk.Frame(right)
        tools.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        for column in range(4):
            tools.columnconfigure(column, weight=1)
        commands = (
            ("Editor config", self.open_config_editor),
            ("Stato config", lambda: self.run_utility(["--config-status"], "Configurazione")),
            ("Extractor", lambda: self.run_utility(["--list-extractors"], "Extractor supportati")),
            ("Moduli", lambda: self.run_utility(["--list-modules"], "Moduli extractor")),
            ("Stato cache", lambda: self.run_utility(["--cache-status"], "Cache")),
            ("Pulisci cache", lambda: self.run_utility(["--cache-clear", "EXP"], "Pulizia cache")),
            ("Ottimizza cache", lambda: self.run_utility(["--cache-vacuum"], "Ottimizzazione cache")),
            ("Documentazione", lambda: webbrowser.open("https://gdl-org.github.io/docs/")),
        )
        for index, (text, command) in enumerate(commands):
            self._action_button(tools, text, command).grid(
                row=index // 4, column=index % 4, sticky="ew", padx=2, pady=3
            )
        ttk.Label(right, text="Canale aggiornamenti").grid(row=7, column=0, sticky="w", pady=(12, 4))
        ttk.Combobox(
            right, textvariable=self.vars["update_channel"], values=("Stabile", "Sviluppo"), state="readonly"
        ).grid(row=7, column=1, columnspan=2, sticky="ew", pady=(12, 4))

    def _multiline(self, parent: ttk.Frame, row: int, height: int) -> tk.Text:
        widget = tk.Text(
            parent, height=height, wrap="none", bg=COLOR_PANEL, fg=COLOR_TEXT, insertbackground=COLOR_ACCENT,
            selectbackground=COLOR_BORDER, highlightthickness=1, highlightbackground=COLOR_BORDER,
            highlightcolor=COLOR_ACCENT, font=("Cascadia Mono", 9), padx=6, pady=5,
        )
        widget.grid(row=row, column=0, columnspan=3, sticky="ew")
        return widget

    def _load_logo(self) -> None:
        logo_path = ASSET_DIR / "gallery-dl-logo.png"
        try:
            original = tk.PhotoImage(file=str(logo_path))
            self.logo_image = original.subsample(4, 4)
            self.logo_label.configure(image=self.logo_image)
            self.root.iconphoto(True, original)
        except tk.TclError:
            self.logo_label.configure(text="GALLERY-DL", style="Section.TLabel")

    def _browse(self, name: str, kind: str) -> None:
        current = str(self.vars[name].get()).strip()
        initial = str(Path(current).parent if current and Path(current).suffix else Path.home())
        if kind == "directory":
            selected = filedialog.askdirectory(initialdir=current or initial)
        elif kind == "save_db":
            selected = filedialog.asksaveasfilename(initialdir=initial, defaultextension=".sqlite3", filetypes=[("SQLite", "*.sqlite3"), ("Tutti", "*.*")])
        elif kind == "save_txt":
            selected = filedialog.asksaveasfilename(initialdir=initial, defaultextension=".txt", filetypes=[("Testo", "*.txt"), ("Tutti", "*.*")])
        elif kind == "save_log":
            selected = filedialog.asksaveasfilename(initialdir=initial, defaultextension=".log", filetypes=[("Log", "*.log"), ("Tutti", "*.*")])
        elif kind == "config":
            selected = filedialog.askopenfilename(initialdir=initial, filetypes=[("Configurazioni", "*.json *.conf *.yaml *.yml *.toml"), ("Tutti", "*.*")])
        else:
            selected = filedialog.askopenfilename(initialdir=initial, filetypes=[("File di testo", "*.txt"), ("Tutti", "*.*")])
        if selected:
            self.vars[name].set(selected)

    def paste_urls(self) -> None:
        try:
            value = self.root.clipboard_get().strip()
        except tk.TclError:
            messagebox.showinfo(APP_NAME, "Gli appunti non contengono testo.")
            return
        if self.urls_text.get("1.0", "end-1c").strip():
            self.urls_text.insert("end", "\n")
        self.urls_text.insert("end", value)

    def _collect_values(self) -> dict[str, Any]:
        values = {name: variable.get() for name, variable in self.vars.items()}
        values["print_formats"] = self.print_text.get("1.0", "end-1c")
        values["custom_options"] = self.options_text.get("1.0", "end-1c")
        values["postprocessors"] = self.postprocessors_text.get("1.0", "end-1c")
        values["postprocessor_options"] = self.postprocessor_options_text.get("1.0", "end-1c")
        return values

    def _get_urls(self) -> list[str]:
        return split_nonempty_lines(self.urls_text.get("1.0", "end-1c"))

    def _validate_inputs(self, values: dict[str, Any], urls: list[str]) -> bool:
        if not urls and not str(values.get("input_file", "")).strip():
            messagebox.showwarning(APP_NAME, "Inserisci almeno un URL oppure seleziona un file con URL.")
            self.notebook.select(0)
            return False
        invalid = [value for value in urls if not looks_like_gallery_input(value)]
        if invalid:
            messagebox.showwarning(APP_NAME, f"Input non riconosciuto:\n{invalid[0]}")
            self.notebook.select(0)
            return False
        input_file = str(values.get("input_file", "")).strip()
        if input_file and not Path(input_file).is_file():
            messagebox.showwarning(APP_NAME, "Il file con URL selezionato non esiste.")
            return False
        destination = Path(str(values.get("destination", "")).strip())
        try:
            destination.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Destinazione non utilizzabile:\n{exc}")
            return False
        return True

    def _ensure_engine(self) -> bool:
        if self.engine:
            return True
        try:
            self.engine, self.engine_version = locate_engine()
        except RuntimeError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return False
        self.vars["engine_status"].set(f"gallery-dl {self.engine_version}")
        return True

    def _probe_engine_async(self) -> None:
        def worker() -> None:
            try:
                engine, version = locate_engine()
                self.messages.put(("engine", engine, version))
            except RuntimeError as exc:
                self.messages.put(("engine_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def show_command(self) -> None:
        values = self._collect_values()
        urls = self._get_urls()
        if not self._ensure_engine():
            return
        try:
            command = build_gallery_command(self.engine or [], values, urls)
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        preview = subprocess.list2cmdline(command)
        self.clear_log()
        self._append_log(preview)
        self.vars["status"].set("Anteprima comando")
        self.vars["detail"].set("Il comando non è stato eseguito.")

    def start(self) -> None:
        if self.process and self.process.poll() is None:
            return
        values = self._collect_values()
        urls = self._get_urls()
        if not self._validate_inputs(values, urls) or not self._ensure_engine():
            return
        try:
            command = build_gallery_command(self.engine or [], values, urls)
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return

        destination = Path(str(values["destination"]))
        self.destination_before = self._snapshot(destination)
        self.cancel_requested = False
        self.active_kind = "download"
        self.active_action = str(values["action"])
        self.clear_log()
        self.progress.stop()
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.progress_var.set(0)
        self.vars["status"].set("Avvio…")
        self.vars["detail"].set(f"gallery-dl {self.engine_version}")
        self._append_log(f"Motore: gallery-dl {self.engine_version}")
        self._append_log(f"Destinazione: {destination}")
        self._set_running(True, cancellable=True)
        threading.Thread(target=self._process_worker, args=(command, "download"), daemon=True).start()

    def run_utility(self, args: list[str], title: str) -> None:
        if self.process and self.process.poll() is None:
            return
        if not self._ensure_engine():
            return
        self.clear_log()
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.active_kind = "utility"
        self.vars["status"].set(title)
        self.vars["detail"].set("Operazione in corso…")
        self._set_running(True, cancellable=True)
        threading.Thread(target=self._process_worker, args=((self.engine or []) + args, "utility"), daemon=True).start()

    def start_update(self) -> None:
        if self.process and self.process.poll() is None:
            return
        venv_python = APP_DIR / ".venv" / "Scripts" / "python.exe"
        if not venv_python.is_file():
            messagebox.showerror(APP_NAME, "Ambiente locale non trovato. Avvia INSTALL.bat per installare o aggiornare.")
            return
        channel = str(self.vars["update_channel"].get())
        if channel == "Sviluppo":
            package = "https://codeberg.org/mikf/gallery-dl/archive/master.tar.gz"
            command = [str(venv_python), "-m", "pip", "install", "--upgrade", "--force-reinstall", package]
        else:
            command = [str(venv_python), "-m", "pip", "install", "--upgrade", "gallery-dl"]
        self.clear_log()
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.active_kind = "update"
        self.vars["status"].set("Aggiornamento gallery-dl")
        self.vars["detail"].set(f"Canale: {channel.lower()}")
        self._set_running(True, cancellable=False)
        threading.Thread(target=self._process_worker, args=(command, "update"), daemon=True).start()

    def _process_worker(self, command: list[str], kind: str) -> None:
        environment = os.environ.copy()
        scripts = str(APP_DIR / ".venv" / "Scripts")
        environment["PATH"] = scripts + os.pathsep + environment.get("PATH", "")
        try:
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding=locale.getpreferredencoding(False),
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env=environment,
            )
            assert self.process.stdout is not None
            for raw_line in self.process.stdout:
                line = raw_line.rstrip("\r\n")
                if not line:
                    continue
                match = re.search(r"\[download\]\s+(\d+(?:[.,]\d+)?)%", line)
                if match:
                    self.messages.put(("progress", float(match.group(1).replace(",", ".")), line))
                else:
                    self.messages.put(("log", line))
            return_code = self.process.wait()
            self.messages.put(("done", kind, return_code, self.cancel_requested))
        except Exception as exc:
            self.messages.put(("worker_error", kind, str(exc)))
        finally:
            self.process = None

    def _set_running(self, running: bool, *, cancellable: bool = False) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        self.cancel_button.configure(state="normal" if running and cancellable else "disabled")
        for button in self.action_buttons:
            button.configure(state="disabled" if running else "normal")

    def cancel(self) -> None:
        process = self.process
        if not process or process.poll() is not None:
            return
        self.cancel_requested = True
        self.vars["status"].set("Annullamento…")
        self.cancel_button.configure(state="disabled")

        def worker() -> None:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
            )

        threading.Thread(target=worker, daemon=True).start()

    def _drain_messages(self) -> None:
        try:
            while True:
                message = self.messages.get_nowait()
                kind = message[0]
                if kind == "engine":
                    self.engine, self.engine_version = message[1], message[2]
                    self.vars["engine_status"].set(f"gallery-dl {self.engine_version}")
                elif kind == "engine_error":
                    self.vars["engine_status"].set("gallery-dl non installato")
                elif kind == "log":
                    self._append_log(str(message[1]))
                elif kind == "progress":
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.progress_var.set(message[1])
                    self.vars["status"].set(f"Download {message[1]:.0f}%")
                    self.vars["detail"].set(str(message[2]))
                elif kind == "done":
                    self._handle_done(str(message[1]), int(message[2]), bool(message[3]))
                elif kind == "worker_error":
                    self._handle_worker_error(str(message[1]), str(message[2]))
        except queue.Empty:
            pass
        self.root.after(100, self._drain_messages)

    def _handle_done(self, kind: str, return_code: int, cancelled: bool) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self._set_running(False)
        self.active_kind = None
        if cancelled:
            self.progress_var.set(0)
            self.vars["status"].set("Operazione annullata")
            self.vars["detail"].set("")
            self._append_log("Operazione annullata dall’utente.")
            return
        if return_code != 0:
            self.progress_var.set(0)
            self.vars["status"].set("Operazione non riuscita")
            self.vars["detail"].set(f"Codice errore {return_code}")
            messagebox.showerror(APP_NAME, "L’operazione non è riuscita. Controlla il registro attività.")
            return
        if kind == "update":
            self.engine = None
            try:
                self.engine, self.engine_version = locate_engine()
                self.vars["engine_status"].set(f"gallery-dl {self.engine_version}")
            except RuntimeError:
                pass
            self.progress_var.set(100)
            self.vars["status"].set("Aggiornamento completato")
            self.vars["detail"].set(f"gallery-dl {self.engine_version}")
            messagebox.showinfo(APP_NAME, f"Aggiornamento completato.\nVersione attiva: {self.engine_version}")
            return
        if kind == "utility":
            self.progress_var.set(100)
            self.vars["status"].set("Operazione completata")
            self.vars["detail"].set("")
            return

        destination = Path(str(self.vars["destination"].get()))
        changed = self._changed_files(destination, self.destination_before)
        self.progress_var.set(100)
        self.vars["status"].set("Completato")
        if self.active_action == "Scarica file":
            count = len(changed)
            self.vars["detail"].set(f"{count} file nuovi o modificati" if count else "Nessun nuovo file rilevato")
            for path in changed[:100]:
                self._append_log(f"Salvato: {path}")
        else:
            self.vars["detail"].set(self.active_action)
        self.root.bell()

    def _handle_worker_error(self, kind: str, error: str) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress_var.set(0)
        self._set_running(False)
        self.active_kind = None
        self.vars["status"].set("Errore")
        self.vars["detail"].set("")
        self._append_log(error)
        messagebox.showerror(APP_NAME, f"Errore durante {kind}:\n{error}")

    @staticmethod
    def _snapshot(folder: Path) -> dict[str, tuple[int, int]]:
        snapshot: dict[str, tuple[int, int]] = {}
        try:
            for item in folder.rglob("*"):
                if item.is_file():
                    stat = item.stat()
                    snapshot[os.path.normcase(str(item.resolve()))] = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            pass
        return snapshot

    @staticmethod
    def _changed_files(folder: Path, before: dict[str, tuple[int, int]]) -> list[Path]:
        changed: list[Path] = []
        ignored = {".part", ".temp", ".sqlite", ".sqlite3", ".log"}
        try:
            for item in folder.rglob("*"):
                if not item.is_file() or item.suffix.lower() in ignored:
                    continue
                stat = item.stat()
                key = os.path.normcase(str(item.resolve()))
                if before.get(key) != (stat.st_size, stat.st_mtime_ns):
                    changed.append(item)
        except OSError:
            return changed
        return sorted(changed, key=lambda item: item.stat().st_mtime_ns)

    def _append_log(self, value: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", value.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def open_destination(self) -> None:
        path = Path(str(self.vars["destination"].get()).strip())
        try:
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(str(path))  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Impossibile aprire la cartella:\n{exc}")

    def open_config_editor(self) -> None:
        custom = str(self.vars["config_file"].get()).strip()
        path = Path(custom) if custom else default_config_path()
        if path.suffix.lower() not in {".json", ".conf", ""}:
            messagebox.showinfo(
                APP_NAME, "L’editor integrato valida JSON. Per YAML/TOML apri il file con il tuo editor di testo."
            )
            try:
                os.startfile(str(path))  # type: ignore[attr-defined]
            except OSError as exc:
                messagebox.showerror(APP_NAME, str(exc))
            return
        try:
            ConfigEditor(self.root, path)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Impossibile aprire la configurazione:\n{exc}")

    def _load_state(self) -> None:
        if not STATE_FILE.is_file():
            return
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            saved = data.get("variables", {})
            for name, value in saved.items():
                if name in self.vars and name not in {"password", "username", "status", "detail", "engine_status"}:
                    self.vars[name].set(value)
        except (OSError, ValueError, TypeError):
            pass

    def _save_state(self) -> None:
        excluded = {"password", "username", "status", "detail", "engine_status"}
        data = {"variables": {name: variable.get() for name, variable in self.vars.items() if name not in excluded}}
        try:
            STATE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError:
            pass

    def _on_close(self) -> None:
        process = self.process
        if process and process.poll() is None:
            if not messagebox.askyesno(APP_NAME, "È in corso un’operazione. Interromperla e chiudere?"):
                return
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
            )
        self._save_state()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    GalleryDlApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
