"""Paste a link, choose a folder, download."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from engine import folder_name_from_url, normalize_urls

APP_DIR = Path(__file__).resolve().parent
STATE_FILE = APP_DIR / '.gallery-dl-gui.json'
BG, PANEL, TEXT, MUTED, GREEN = '#110B38', '#1E1E1E', '#FFFFFF', '#99A2DB', '#B2F962'


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Gallery-DL')
        self.configure(bg=BG)
        self.geometry('800x690')
        self.minsize(650, 560)
        self.events = queue.Queue()
        self.process = None
        self.busy = self.cancelled = False
        self.saved = self.existing = 0
        destination = str(Path('Downloads') / 'gallery-dl')
        try:
            prefs = json.loads(STATE_FILE.read_text(encoding='utf-8'))
            destination = prefs.get('destination') or destination
            previous_default = str(Path.home() / 'Downloads' / 'gallery-dl')
            if os.path.normcase(destination) == os.path.normcase(previous_default):
                destination = str(Path('Downloads') / 'gallery-dl')
        except (OSError, ValueError):
            pass
        self.destination = tk.StringVar(value=destination)
        self.collection = tk.StringVar()
        self._automatic_collection = ''
        self._setting_collection = False
        self.status = tk.StringVar(value='Incolla un link e premi Scarica.')
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TProgressbar', background=GREEN, troughcolor=PANEL, borderwidth=0)
        body = tk.Frame(self, bg=BG, padx=24, pady=20)
        body.pack(fill='both', expand=True)
        try:
            self.logo = tk.PhotoImage(file=str(APP_DIR / 'assets/gallery-dl-logo.png')).subsample(4)
            tk.Label(body, image=self.logo, bg=BG).pack(anchor='w', pady=(0, 16))
        except tk.TclError:
            self.label(body, 'Gallery-DL', 22).pack(anchor='w')
        self.label(body, 'Incolla il link', 12).pack(anchor='w', pady=(0, 6))
        self.urls = tk.Text(body, height=3, wrap='word', bg=PANEL, fg=TEXT,
                            insertbackground=GREEN, relief='flat', padx=12, pady=10,
                            font=('Segoe UI', 11), undo=True)
        self.urls.pack(fill='x')
        self.urls.bind('<<Modified>>', self.url_changed)
        self.urls.edit_modified(False)
        self.label(body, 'Una pagina, un’immagine, un post o una galleria. Anche più link.', 10, MUTED).pack(anchor='w', pady=(6, 18))
        self.label(body, 'Salva in', 11).pack(anchor='w', pady=(0, 6))
        folder = tk.Frame(body, bg=BG)
        folder.pack(fill='x')
        self.folder_entry = tk.Entry(folder, textvariable=self.destination, bg=PANEL, fg=TEXT,
                                     insertbackground=GREEN, relief='flat', font=('Segoe UI', 10))
        self.folder_entry.pack(side='left', fill='x', expand=True, ipady=9)
        self.browse = self.button(folder, 'Sfoglia…', self.choose_folder)
        self.browse.pack(side='left', padx=(8, 0))
        self.label(body, 'Cartella', 11).pack(anchor='w', pady=(14, 6))
        self.collection_entry = tk.Entry(body, textvariable=self.collection, bg=PANEL, fg=TEXT,
                                         insertbackground=GREEN, relief='flat', font=('Segoe UI', 10))
        self.collection_entry.pack(fill='x', ipady=9)
        self.collection.trace_add('write', self.collection_changed)
        actions = tk.Frame(body, bg=BG)
        actions.pack(fill='x', pady=(20, 16))
        self.download = self.button(actions, 'Scarica', self.start, primary=True)
        self.download.pack(side='left')
        self.stop = self.button(actions, 'Annulla', self.cancel)
        self.stop.pack(side='left', padx=8)
        self.stop.configure(state='disabled')
        self.button(actions, 'Apri cartella', self.open_folder).pack(side='right')
        self.update_button = self.button(actions, 'Aggiorna', self.update_engine)
        self.update_button.pack(side='right', padx=8)
        tk.Label(body, textvariable=self.status, bg=BG, fg=GREEN, font=('Segoe UI', 11),
                 anchor='w', wraplength=730).pack(fill='x', pady=(0, 8))
        self.progress = ttk.Progressbar(body, mode='indeterminate')
        self.progress.pack(fill='x', pady=(0, 12))
        log_frame = tk.Frame(body, bg=PANEL)
        log_frame.pack(fill='both', expand=True)
        self.log = tk.Text(log_frame, height=6, wrap='word', bg=PANEL, fg=MUTED,
                           relief='flat', padx=12, pady=10, font=('Segoe UI', 10), state='disabled')
        self.log.pack(side='left', fill='both', expand=True)
        scrollbar = ttk.Scrollbar(log_frame, command=self.log.yview)
        scrollbar.pack(side='right', fill='y')
        self.log.configure(yscrollcommand=scrollbar.set)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Control-Return>', lambda _: self.start())
        self.after(100, self.poll)

    def label(self, parent, text, size, color=TEXT):
        return tk.Label(parent, text=text, bg=BG, fg=color, font=('Segoe UI', size))

    def button(self, parent, text, command, primary=False):
        return tk.Button(parent, text=text, command=command, bg=GREEN if primary else '#21136D',
                         fg=BG if primary else TEXT, activebackground='#6B3BD0', activeforeground=TEXT,
                         relief='flat', bd=0, padx=17, pady=9, cursor='hand2', font=('Segoe UI', 10, 'bold'))

    def choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.resolve_destination())
        if folder:
            self.destination.set(folder)

    def resolve_destination(self):
        value = os.path.expandvars(self.destination.get().strip())
        path = Path(value).expanduser()
        return (Path.home() / path if not path.is_absolute() else path).resolve()

    def collection_changed(self, *_):
        if not self._setting_collection and self.collection.get() != self._automatic_collection:
            self._automatic_collection = ''

    def url_changed(self, _event=None):
        if not self.urls.edit_modified():
            return
        self.urls.edit_modified(False)
        try:
            suggested = folder_name_from_url(normalize_urls(self.urls.get('1.0', 'end'))[0])
        except ValueError:
            return
        if not self.collection.get() or self.collection.get() == self._automatic_collection:
            self._setting_collection = True
            self.collection.set(suggested)
            self._automatic_collection = suggested
            self._setting_collection = False

    def target_folder(self):
        name = self.collection.get().strip()
        if not name:
            raise ValueError('Inserisci il nome della cartella.')
        if name in ('.', '..') or any(char in name for char in '<>:"/\\|?*') or name.rstrip(' .') != name:
            raise ValueError('“Cartella” deve essere un solo nome, senza /, \\ o caratteri speciali.')
        if name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
            raise ValueError('Questo nome di cartella è riservato da Windows.')
        return self.resolve_destination() / name

    def open_folder(self):
        try:
            folder = self.target_folder() if self.collection.get().strip() else self.resolve_destination()
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(folder)
        except (ValueError, OSError) as exc:
            messagebox.showerror('Cartella non disponibile', str(exc))

    def append(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def set_busy(self, value):
        self.busy = value
        for control in (self.download, self.update_button, self.browse, self.folder_entry, self.collection_entry, self.urls):
            control.configure(state='disabled' if value else 'normal')
        self.stop.configure(state='normal' if value else 'disabled')
        self.progress.start(12) if value else self.progress.stop()

    def start(self):
        if self.busy:
            return
        try:
            urls = normalize_urls(self.urls.get('1.0', 'end'))
            if not self.destination.get().strip():
                raise ValueError('Scegli una cartella di destinazione.')
            destination = self.target_folder()
            destination.mkdir(parents=True, exist_ok=True)
        except (ValueError, OSError) as exc:
            messagebox.showerror('Controlla il link o la cartella', str(exc))
            return
        try:
            STATE_FILE.write_text(json.dumps({'destination': self.destination.get().strip()}), encoding='utf-8')
        except OSError:
            pass
        self.saved = self.existing = 0
        self.cancelled = False
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')
        self.append('Destinazione: ' + str(destination))
        self.set_busy(True)
        self.status.set('Cerco le immagini…')
        self.launch([str(APP_DIR / 'engine.py')], {'urls': urls, 'destination': str(destination)})

    def launch(self, args, payload=None):
        # Spawn before enabling event processing: Cancel cannot race creation.
        try:
            self.process = subprocess.Popen([str(APP_DIR / '.venv/Scripts/python.exe'), '-u', *args],
                cwd=APP_DIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding='utf-8', errors='replace', env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if payload is not None:
                self.process.stdin.write(json.dumps(payload) + '\n')
                self.process.stdin.flush()
            self.process.stdin.close()
        except OSError as exc:
            self.append(str(exc))
            self.status.set('Avvio non riuscito. Esegui INSTALL.bat.')
            self.set_busy(False)
            return
        proc = self.process
        def read():
            for line in proc.stdout:
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise ValueError()
                except ValueError:
                    event = {'type': 'log', 'text': line.rstrip()}
                self.events.put(event)
            self.events.put({'type': 'exit', 'code': proc.wait(), 'update': payload is None})
        threading.Thread(target=read, daemon=True).start()

    def poll(self):
        while not self.events.empty():
            event = self.events.get_nowait()
            kind = event.get('type')
            if kind in ('log', 'status', 'error'):
                self.append(event.get('text', ''))
                if kind != 'log':
                    self.status.set(event.get('text', ''))
            elif kind == 'file':
                self.existing += bool(event.get('existing'))
                self.saved += not event.get('existing', False)
                self.status.set(f'{self.saved} file scaricati · {self.existing} già presenti')
                self.append(('Già presente: ' if event.get('existing') else 'Salvato: ') + event['name'])
            elif kind == 'done':
                self.status.set(event['text'])
                self.append(event['text'])
            elif kind == 'exit':
                self.process = None
                self.set_busy(False)
                if self.cancelled:
                    self.status.set('Annullato. I file già scaricati restano nella cartella.')
                elif event.get('update'):
                    self.status.set('Aggiornamento completato.' if event['code'] == 0 else 'Aggiornamento non riuscito: vedi dettagli.')
                elif event['code'] != 0:
                    self.status.set(f'Download incompleto: {self.saved} file salvati. Vedi dettagli.')
        self.after(100, self.poll)

    def cancel(self):
        if self.process and self.process.poll() is None:
            self.cancelled = True
            self.stop.configure(state='disabled')
            self.status.set('Annullamento…')
            subprocess.Popen(['taskkill', '/PID', str(self.process.pid), '/T', '/F'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def update_engine(self):
        if not self.busy:
            self.cancelled = False
            self.set_busy(True)
            self.status.set('Aggiornamento del motore…')
            self.launch(['-m', 'pip', 'install', '--upgrade', 'gallery-dl', 'yt-dlp'])

    def close(self):
        if self.busy and not messagebox.askyesno('Chiudere?', 'Annullare l’operazione e chiudere?'):
            return
        self.cancel()
        self.destroy()


if __name__ == '__main__':
    if sys.platform == 'win32':
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    App().mainloop()
