from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pystray
from PIL import Image, ImageDraw


def _image(connected: bool) -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    color = "#32c878" if connected else "#e87562"
    draw.rounded_rectangle((5, 5, 59, 59), radius=14, fill="#172333", outline=color, width=5)
    draw.ellipse((22, 22, 42, 42), fill=color)
    return image


def create_tray(
    on_open: Callable[[], None],
    connected: Callable[[], bool],
    on_quit: Callable[[], None],
    on_install_audio: Callable[[], None],
):
    icon = pystray.Icon(
        "TailDesk",
        _image(False),
        "TailDesk — disconnected",
        pystray.Menu(
            pystray.MenuItem("Open TailDesk settings", lambda icon, item: on_open(), default=True),
            pystray.MenuItem("Install virtual audio device", lambda icon, item: on_install_audio()),
            pystray.MenuItem("Quit TailDesk", lambda icon, item: (on_quit(), icon.stop())),
        ),
    )

    def refresh() -> None:
        previous = None
        while not icon.visible:
            time.sleep(0.2)
        while icon.visible:
            active = connected()
            if active != previous:
                icon.icon = _image(active)
                icon.title = "TailDesk — connected" if active else "TailDesk — disconnected"
                previous = active
            time.sleep(1)

    threading.Thread(target=refresh, name="taildesk-tray-status", daemon=True).start()
    return icon
