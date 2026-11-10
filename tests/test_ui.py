import os
import sys
import pytest


@pytest.mark.desktop
@pytest.mark.skipif(sys.platform == "linux" and not os.environ.get("DISPLAY"), reason="Needs Xvfb or a desktop")
def test_settings_build_collect_and_close():
    tk = pytest.importorskip("tkinter")
    from dictation4me.ui.settings import SettingsWindow
    root = tk.Tk()
    try:
        window = SettingsWindow(root)
        root.update_idletasks()
        # The footer must remain accessible even when the form needs scrolling.
        for tab in range(3):
            window.notebook.select(tab)
            root.geometry("760x600")
            root.update_idletasks()
            bottom = window.footer.winfo_rooty() + window.footer.winfo_height()
            assert bottom <= root.winfo_rooty() + root.winfo_height()
            for button in window.footer.winfo_children():
                assert button.winfo_height() > 15
        window.variables["service.base_url"].set("http://localhost:4000/v1")
        window.variables["service.api_key"].set("SECRET")
        window.variables["output.auto_paste"].set(False)
        cfg = window.collect()
        assert cfg.service.api_key == "SECRET" and cfg.output.auto_paste is False
        window.variables["audio.max_seconds"].set("not-a-number")
        with pytest.raises(ValueError):
            window.collect()
        window.close()
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
