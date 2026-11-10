"""TTK configuration and diagnostic window; never created during normal dictation."""
from __future__ import annotations

from dataclasses import asdict
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

from ..config import from_dict, load, save
from ..onboarding import install_integration


class ScrollablePage(ttk.Frame):
    """Keep long settings forms usable on smaller and high-DPI desktops."""

    def __init__(self, parent):
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        background = ttk.Style().lookup("TFrame", "background") or "#eeeeee"
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0,
                                background=background)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.body = ttk.Frame(self.canvas, padding=16)
        self.item = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda event: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(
            self.item, width=event.width))
        self.canvas.bind("<Button-4>", lambda event: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind("<Button-5>", lambda event: self.canvas.yview_scroll(1, "units"))
        self.canvas.bind("<MouseWheel>", lambda event: self.canvas.yview_scroll(
            -1 if event.delta > 0 else 1, "units"))


class SettingsWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Dictation4me · Settings")
        self.root.minsize(680, 540)
        height = max(540, min(790, root.winfo_screenheight() - 90))
        self.root.geometry(f"760x{height}")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.config = load(missing_ok=True)
        self.variables = {}
        self.events = queue.Queue()
        self.stopping = threading.Event()
        self.busy = False
        self.test_wav = None
        self.status_var = tk.StringVar(value="Ready")
        self.service_var = tk.StringVar(value="Service: checking…")
        self.buttons = []
        self.devices = []
        style = ttk.Style()
        style.configure("Title.TLabel", font=("TkDefaultFont", 19, "bold"))
        style.configure("Muted.TLabel", foreground="#60646c")
        outer = ttk.Frame(root, padding=20)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)
        ttk.Label(outer, text="Dictation4me", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(outer, text="Press your shortcut. Speak. Press again to transcribe.",
                  style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 14))
        book = self.notebook = ttk.Notebook(outer)
        book.grid(row=2, column=0, sticky="nsew")
        connection_scroll = ScrollablePage(book)
        desktop_scroll = ScrollablePage(book)
        connection = connection_scroll.body
        desktop = desktop_scroll.body
        tools = ttk.Frame(book, padding=16)
        for page, name in ((connection_scroll, "Speech & audio"), (desktop_scroll, "Desktop"), (tools, "Tests & diagnostics")):
            book.add(page, text=name)
        for page in (connection, desktop, tools):
            page.columnconfigure(1, weight=1)
        self._entry(connection, 0, "Speech server URL", "service.base_url")
        self._entry(connection, 1, "Model alias", "service.model")
        self._entry(connection, 2, "API key", "service.api_key", secret=True)
        self._entry(connection, 3, "Key environment variable", "service.api_key_env")
        ttk.Label(connection, text="An existing environment variable overrides the stored API key.",
                  style="Muted.TLabel", wraplength=600).grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 12))
        self._entry(connection, 5, "Language", "service.language", values=["auto", "en", "cs", "sk", "de"])
        self.device_box = self._entry(connection, 6, "Microphone", "audio.device", values=["default"])
        self._button(connection, "Refresh microphones", self.refresh_devices).grid(row=7, column=1, sticky="w", pady=5)
        self._entry(connection, 8, "Recording limit (seconds)", "audio.max_seconds")
        self._entry(connection, 9, "Request timeout (seconds)", "service.timeout_seconds")
        ttk.Label(connection, text="Audio is held in memory. Only your configured server receives it.\n"
                                  "Failed uploads can be retried for five minutes. Nothing is recorded until you start.",
                  wraplength=610, style="Muted.TLabel").grid(row=10, column=0, columnspan=2, sticky="w", pady=14)
        self._entry(desktop, 0, "Global shortcut", "desktop.hotkey")
        self._entry(desktop, 1, "Shortcut backend", "desktop.hotkey_backend", values=["auto", "portal", "x11", "external", "windows"], readonly=True)
        ttk.Label(desktop, text="Wayland: the desktop portal owns the final binding. If unavailable, assign\n"
                               "the command ‘dictation4me toggle’ in your desktop's shortcut settings.",
                  wraplength=600, style="Muted.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 14))
        self._check(desktop, 3, "Automatically paste the completed transcript", "output.auto_paste")
        self._entry(desktop, 4, "Paste shortcut", "output.paste_shortcut", values=["ctrl+v", "ctrl+shift+v", "shift+insert"], readonly=True)
        self._entry(desktop, 5, "Paste backend", "output.backend", values=["auto", "clipboard", "ydotool", "xdotool"], readonly=True)
        self._entry(desktop, 6, "Clipboard settle delay (ms)", "output.paste_delay_ms")
        self._entry(desktop, 7, "ydotool socket (optional)", "output.ydotool_socket")
        self._check(desktop, 8, "Start Dictation4me when I sign in", "desktop.autostart")
        ttk.Label(desktop, text="The focused field at paste time receives the text. There is no focus restoration.\n"
                               "Clipboard-only mode is safer when you switch applications while waiting.\n"
                               "Terminal applications may require Ctrl+Shift+V. No Enter key is ever sent.",
                  wraplength=610, style="Muted.TLabel").grid(row=9, column=0, columnspan=2, sticky="w", pady=16)
        controls = ttk.Frame(tools)
        controls.grid(row=0, column=0, columnspan=2, sticky="ew")
        for label, command in [("Record 3-second test", self.test_microphone),
                               ("Transcribe test audio", self.test_transcription),
                               ("Test paste (3s delay)", self.test_output),
                               ("Run diagnostics", self.doctor)]:
            self._button(controls, label, command).pack(anchor="w", fill="x", pady=3)
        self.result = tk.Text(tools, height=13, wrap="word", font=("TkFixedFont", 10))
        self.result.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=10)
        tools.rowconfigure(1, weight=1)
        self.result.insert("1.0", "Tests run only when requested. Transcribe sends the test recording to your server.\n"
                                    "Test paste puts harmless sample text on your clipboard and sends your selected paste shortcut.")
        self.result.configure(state="disabled")
        ttk.Label(outer, textvariable=self.service_var, style="Muted.TLabel", wraplength=700).grid(row=3, column=0, sticky="w", pady=(12, 2))
        ttk.Label(outer, textvariable=self.status_var, wraplength=700).grid(row=4, column=0, sticky="w", pady=(2, 10))
        footer = self.footer = ttk.Frame(outer)
        footer.grid(row=5, column=0, sticky="ew")
        self._button(footer, "Save", self.save_only).pack(side="left")
        self._button(footer, "Save & start", self.save_start).pack(side="left", padx=8)
        ttk.Button(footer, text="Close", command=self.close).pack(side="right")
        self.root.after(80, self.poll_events)
        self.root.after(300, self.poll_service)

    def _entry(self, parent, row, label, dotted, *, secret=False, values=None, readonly=False):
        section, name = dotted.split(".")
        var = tk.StringVar(value=str(getattr(getattr(self.config, section), name)))
        self.variables[dotted] = var
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 14), pady=7)
        if values is not None:
            field = ttk.Combobox(parent, textvariable=var, values=values, state="readonly" if readonly else "normal")
        else:
            field = ttk.Entry(parent, textvariable=var, show="•" if secret else "")
        field.grid(row=row, column=1, sticky="ew", pady=7)
        return field

    def _check(self, parent, row, label, dotted):
        section, name = dotted.split(".")
        var = tk.BooleanVar(value=getattr(getattr(self.config, section), name))
        self.variables[dotted] = var
        ttk.Checkbutton(parent, text=label, variable=var).grid(row=row, column=0, columnspan=2, sticky="w", pady=8)

    def _button(self, parent, text, command):
        btn = ttk.Button(parent, text=text, command=command)
        self.buttons.append(btn)
        return btn

    def collect(self):
        data = asdict(self.config)
        for dotted, var in self.variables.items():
            section, name = dotted.split(".")
            value = var.get()
            if type(data[section][name]) is int:
                try:
                    value = int(value)
                except ValueError as exc:
                    raise ValueError(f"{name.replace('_', ' ')} must be a whole number") from exc
            data[section][name] = value
        return from_dict(data)

    def background(self, function, completed=lambda result: None):
        if self.busy:
            return
        self.busy = True
        for button in self.buttons:
            button.state(["disabled"])

        def worker():
            try:
                value = function()
                self.events.put(("done", (completed, value)))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True, name="dictation-settings-worker").start()

    def poll_events(self):
        if self.stopping.is_set():
            return
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind in ("done", "error"):
                self.busy = False
                for button in self.buttons:
                    button.state(["!disabled"])
            if kind == "done":
                callback, result = value
                callback(result)
            elif kind == "error":
                self.status_var.set(value)
                self.show_result(value)
            elif kind == "status":
                self.status_var.set(value)
        self.root.after(80, self.poll_events)

    def show_result(self, text):
        self.result.configure(state="normal")
        self.result.delete("1.0", "end")
        self.result.insert("1.0", text)
        self.result.configure(state="disabled")

    def poll_service(self):
        if self.stopping.is_set():
            return
        from ..ipc import request
        try:
            status = request("status", timeout=0.15)
            self.service_var.set(f"Service: {status['state']} — {status['message']}")
        except ConnectionError:
            self.service_var.set("Service: not running")
        self.root.after(1500, self.poll_service)

    def _validated(self):
        try:
            return self.collect()
        except ValueError as exc:
            messagebox.showerror("Invalid setting", str(exc), parent=self.root)
            return None

    def _save(self, cfg, start=False):
        from ..ipc import request
        from ..app import launch
        try:
            status = request("status", timeout=0.5)
            if status["state"] != "idle":
                raise RuntimeError("Finish or cancel the active dictation before changing settings")
        except ConnectionError:
            status = None
        save(cfg)
        install_integration(cfg)
        result = {"ok": True}
        if status:
            result = request("reload")
            if not result.get("ok"):
                raise RuntimeError(result.get("error", "Could not reload settings"))
        if start:
            if result.get("restart_required"):
                request("shutdown")
                for _ in range(60):
                    time.sleep(0.1)
                    try:
                        request("status", timeout=0.1)
                    except ConnectionError:
                        break
                else:
                    raise RuntimeError("Service has not stopped yet; retry Save & start")
            launch()
        return result

    def save_only(self):
        cfg = self._validated()
        if cfg:
            self.background(lambda: self._save(cfg),
                            lambda result: self._saved(cfg, result, False))

    def save_start(self):
        cfg = self._validated()
        if cfg:
            self.background(lambda: self._save(cfg, True),
                            lambda result: self._saved(cfg, result, True))

    def _saved(self, cfg, result, started):
        self.config = cfg
        self.status_var.set("Service running. Approve the shortcut dialog if shown. You can close this window."
                            if started else "Saved. " + ("Restart the service for the new shortcut." if result.get("restart_required") else ""))

    def refresh_devices(self):
        from ..audio import list_devices

        def completed(devices):
            self.devices = devices
            # Store stable names when unique; explicit indices only for duplicate names.
            names = [d["name"] for d in devices]
            values = ["default"] + [d["name"] if names.count(d["name"]) == 1 else f"index:{d['index']}" for d in devices]
            self.device_box.configure(values=values)
            self.status_var.set(f"Found {len(devices)} microphone(s)")
            self.show_result("\n".join(f"index:{d['index']} — {d['name']} ({d['host_api']})" for d in devices))
        self.background(list_devices, completed)

    def test_microphone(self):
        cfg = self._validated()
        if not cfg:
            return
        self.status_var.set("Recording a three-second test. Speak now…")

        def record():
            from ..audio import Recorder
            from ..ipc import request
            try:
                if request("status", timeout=0.3)["state"] != "idle":
                    raise RuntimeError("Finish the current dictation before testing the microphone")
            except ConnectionError:
                pass
            recorder = Recorder()
            try:
                recorder.start(cfg.audio)
                for _ in range(30):
                    if self.stopping.wait(0.1):
                        raise RuntimeError("Test cancelled")
                return recorder.stop().wav()
            finally:
                recorder.abort()

        def complete(wav):
            self.test_wav = wav
            self.status_var.set("Test recorded in memory. Click Transcribe test audio to contact your server.")
            self.show_result(f"WAV captured: {len(wav):,} bytes, mono PCM16 / 16 kHz.")
        self.background(record, complete)

    def test_transcription(self):
        cfg = self._validated()
        if not cfg:
            return
        if self.test_wav is None:
            self.status_var.set("Record the three-second microphone test first")
            return
        self.status_var.set("Sending test audio to your configured speech server…")
        wav = self.test_wav

        def work():
            import asyncio
            from ..stt import transcribe
            return asyncio.run(transcribe(wav, cfg.service))

        def complete(text):
            self.show_result(text)
            self.status_var.set("Transcription test succeeded; nothing was pasted")
        self.background(work, complete)

    def test_output(self):
        cfg = self._validated()
        if not cfg:
            return
        from ..cli import output_test
        self.status_var.set("Focus the target field now; sample paste in three seconds")
        self.root.iconify()
        self.background(lambda: output_test(cfg, delay=3), lambda text: self.status_var.set(text))

    def doctor(self):
        from ..diagnostics import diagnose
        self.background(diagnose, lambda rows: self.show_result("\n".join(
            f"[{r['level'].upper()}] {r['check']}: {r['detail']}" for r in rows)))

    def close(self):
        self.stopping.set()
        self.test_wav = None
        self.root.destroy()


def show_settings():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise RuntimeError("Cannot open the settings window. Run from an interactive desktop, or edit config.toml") from exc
    SettingsWindow(root)
    root.mainloop()
