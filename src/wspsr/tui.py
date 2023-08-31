from textual.app import App
from textual.logging import TextualHandler

from wspsr.screens.selection import SelectionScreen

import logging

logging.basicConfig(
    level="NOTSET",
    handlers=[TextualHandler()],
)


class WSPSRApp(App):
    TITLE = "Live WSPSR"
    CSS_PATH = "pretty.css"
    audio_tracks: dict[str, dict] = {}
    tasks: dict[str, dict] = {}
    defaults = {'models': ['large-v2', 'diarize']}

    def __init__(self, driver_class=None, css_path=None, watch_css=None, monitor_path=None):
        super().__init__(driver_class, css_path, watch_css)
        self.monitor_path = monitor_path

    def on_mount(self) -> None:
        self.push_screen(SelectionScreen(self.monitor_path))
        # self.push_screen(SetEncryptionKeyScreen())


def main():
    from sys import argv
    app = WSPSRApp(monitor_path="/media" if len(argv) <= 1 else argv[1])
    app.run()


if __name__ == "__main__":
    main()
