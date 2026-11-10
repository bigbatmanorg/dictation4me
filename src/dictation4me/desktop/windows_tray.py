"""Windows-only system tray; Tk settings run in a separate process."""
import threading


class WindowsTray:
    def __init__(self, callback):
        import pystray
        from PIL import Image, ImageDraw
        self.pystray = pystray
        self.state = {"state": "idle", "has_last_text": False, "can_retry": False}
        self.callback = callback
        self.images = {}
        for name, fill in [("idle", "#2563eb"), ("recording", "#dc2626"),
                           ("transcribing", "#d97706"), ("inserting", "#059669")]:
            image = Image.new("RGBA", (64, 64))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((22, 8, 42, 40), radius=10, fill=fill)
            draw.arc((14, 20, 50, 49), 0, 180, fill=fill, width=5)
            draw.line((32, 48, 32, 57), fill=fill, width=5)
            draw.line((23, 57, 41, 57), fill=fill, width=5)
            self.images[name] = image
        item = pystray.MenuItem
        self.icon = pystray.Icon("dictation4me", self.images["idle"], "Dictation4me",
            menu=pystray.Menu(
                item(lambda _: "Stop recording" if self.state["state"] == "recording" else "Start recording",
                     lambda *_: callback("toggle"), default=True,
                     enabled=lambda _: self.state["state"] in ("idle", "recording")),
                item("Cancel", lambda *_: callback("cancel"), enabled=lambda _: self.state["state"] != "idle"),
                item("Retry transcription", lambda *_: callback("retry"), enabled=lambda _: self.state.get("can_retry", False)),
                item("Copy last transcript", lambda *_: callback("copy-last"), enabled=lambda _: self.state.get("has_last_text", False)),
                item("Settings…", lambda *_: callback("settings")),
                item("Quit", lambda *_: callback("shutdown")),
            ))
        self.thread = None

    def start(self):
        # pystray explicitly permits a non-main thread on Windows.
        self.thread = threading.Thread(target=self.icon.run, daemon=True, name="dictation-tray")
        self.thread.start()

    def update(self, state):
        old = self.state
        self.state = state
        self.icon.title = ("Dictation4me — " + state["message"])[:127]
        if old["state"] != state["state"]:
            self.icon.icon = self.images.get(state["state"], self.images["idle"])
        self.icon.update_menu()

    def notify(self, text):
        self.icon.notify(text, "Dictation4me")

    def close(self):
        self.icon.stop()
        if self.thread:
            self.thread.join(2)
