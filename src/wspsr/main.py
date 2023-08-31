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

    def on_mount(self) -> None:
        self.push_screen(SelectionScreen("/media"))
        # self.push_screen(SetEncryptionKeyScreen())


if __name__ == "__main__":
    app = WSPSRApp()
    app.run()
