"""Stable, non-secret errors crossing the service IPC boundary.

Every error carries a short *fix hint*: what the user can do about it. Hints are
static text (never URLs, keys or note content) so they are safe to show in the
UI, CLI, notifications and logs.
"""
import sys

_LINUX = sys.platform.startswith("linux")
_STATE_DIR = "~/.local/state/dictation4me" if _LINUX else r"%LOCALAPPDATA%\Dictation4me\state"

HINTS = {
    # Audio
    "AUDIO_UNAVAILABLE": ("Install PortAudio: sudo apt install libportaudio2 (Fedora: sudo dnf install portaudio), "
                          "then restart: dictation4me service restart" if _LINUX else
                          "Reinstall dictation4me so the sounddevice package brings PortAudio"),
    "AUDIO_OPEN_FAILED": ("Check the microphone in System Settings › Sound, pick another input in Settings › "
                          "Dictation, and install PipeWire's ALSA bridge: sudo apt install pipewire-alsa"
                          if _LINUX else "Check Windows Settings › Privacy › Microphone and the input device"),
    "AUDIO_EMPTY": "No audio arrived. Check that the microphone is not muted and the right input is selected",
    "NO_SPEECH": "Nothing was heard. Check the microphone level in your sound settings and speak a bit closer",
    "AUDIO_TOO_LARGE": "The recording exceeded the size limit. Dictate in shorter parts",
    "AUDIO_PLAYBACK": "Check the output device in your sound settings",
    "AUDIO_MISSING": "The audio file is gone. Discard this item in Recovery",
    # Providers
    "MODEL_NOT_CONFIGURED": "Enter the model name in Settings › Connections",
    "PROVIDER_UNAVAILABLE": ("Check that the server URL in Settings › Connections is reachable from this computer "
                             "(VPN, firewall, server running) and that it speaks the OpenAI API"),
    "PROVIDER_AUTH": "The server rejected the API key. Check the key and its source in Settings › Connections",
    "PROVIDER_NOT_FOUND": "Check the model name and the advanced endpoint path in Settings › Connections",
    "PROVIDER_RATE_LIMIT": "The server is rate-limiting. Wait a moment and retry from Recovery",
    "PROVIDER_HTTP": "The server reported an error. Check its logs, then retry from Recovery",
    "PROVIDER_OUTPUT": "The server's reply was not in the expected format. Check the model name and request format",
    "PROVIDER_TRUNCATED": "Raise the model's output limit on the server or rewrite shorter text",
    "PROVIDER_REDIRECT": "Use the final server URL in Settings › Connections (redirects are not followed)",
    "REALTIME_UNAVAILABLE": ("Check the realtime model name and that the server supports /v1/realtime; "
                             "the recording is transcribed with the normal model instead"),
    "CONSENT": "Tick 'Allow sending audio and text to remote servers' in Settings › Connections",
    "SECRET": ("Paste the key in Settings › Connections (stored in the keyring), or use 'Move key to keyring' "
               "— variables from ~/.bashrc are not visible to a service started at login"),
    "KEYRING": "Unlock or install a keyring (KDE Wallet / GNOME Keyring), or use an environment variable",
    "CONFIG": "Correct the highlighted setting; nothing was saved",
    # Desktop integration
    "DESKTOP_DEPENDENCY": ("Install the clipboard tools: sudo apt install wl-clipboard (Wayland) or xclip (X11)"
                           if _LINUX else "Reinstall dictation4me"),
    "DESKTOP": "The desktop clipboard did not respond. Try again; if it persists, log out and back in",
    "CLIPBOARD_CHANGED": "Another app changed the clipboard first. Press 'Copy again' in Quick dictation",
    "HOTKEY": ("Run 'dictation4me init' once (installs the desktop entry the shortcut portal needs), then "
               "'dictation4me service restart'; or bind 'dictation4me toggle' as a custom KDE shortcut"),
    "NO_DESKTOP": "Start dictation4me from inside your desktop session",
    "PASTE_UNAVAILABLE": "Run 'dictation4me init', restart the service, then Settings › Dictation › Allow automatic pasting",
    "PASTE_DENIED": "Settings › Dictation › Allow automatic pasting, then press Approve in the KDE dialog",
    "PASTE_TIMEOUT": "Answer the KDE 'Remote Control' dialog (it may be behind other windows)",
    "TRAY_UNAVAILABLE": "Your panel has no system tray; dictation still works through the shortcut",
    # Service, GUI and data
    "SERVICE_UNAVAILABLE": f"Start it with 'dictation4me service start'; details in {_STATE_DIR}/service.log",
    "STARTUP": f"Run 'dictation4me service run' in a terminal to see the error, or read {_STATE_DIR}/service.log",
    "ALREADY_RUNNING": "Use 'dictation4me service restart' to replace the running service",
    "GUI_UNAVAILABLE": "Reinstall dictation4me (uv tool install --reinstall .) to get the NativeHTMLKit package",
    "BUSY": "Wait until the current recording or transcription finishes",
    "RECOVERY_PENDING": "Open Recovery to retry or discard the failed recording",
    "DELIVERED": "Use 'Copy again' (or 'dictation4me paste-last') to deliver it once more",
    "NO_RESULT": "Dictate something first",
    "CONFLICT": "The note changed elsewhere; your text is kept — reload the note and apply again",
    "INVALID_BACKUP": "Use a backup created by 'dictation4me backup'; the file may be damaged",
    "INTERNAL": f"Unexpected error. Details are in {_STATE_DIR}/service.log",
}


class AppError(RuntimeError):
    def __init__(self, code: str, message: str, hint: str | None = None):
        self.code = code
        self.explicit_hint = hint
        super().__init__(message)

    @property
    def hint(self) -> str:
        return self.explicit_hint or HINTS.get(self.code, "")

    def describe(self) -> str:
        """Message plus how to fix it, for people."""
        hint = self.hint
        return f"{self}. Fix: {hint}" if hint else str(self)


class Conflict(AppError):
    def __init__(self, message: str = "The note changed. Reload before applying this change."):
        super().__init__("CONFLICT", message)


class Busy(AppError):
    def __init__(self, message: str = "A recording or delivery is already active."):
        super().__init__("BUSY", message)
