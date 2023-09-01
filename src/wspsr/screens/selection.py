from textual import work, on, events
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll, Grid
from textual.screen import Screen, ModalScreen
from textual.validation import Integer
from textual.widgets import (
    Header,
    Footer,
    DataTable,
    RichLog,
    Pretty,
    SelectionList,
    Label,
    Button,
    Input,
)
from textual.widgets.selection_list import Selection
from textual.worker import Worker, get_current_worker

from rich.text import Text

from datetime import timedelta
from enum import Enum
from tempfile import TemporaryDirectory
from os import chdir, path, makedirs
from shutil import ignore_patterns
from shlex import quote

import asyncio
from aiostream.stream import merge
from aioshutil import copytree

from wspsr.monitor import monitor_directory


async def decorate_with(prefix, awaitable):
    async for item in awaitable:
        yield prefix, item


def sizeof_fmt(num, suffix="B"):
    for unit in ("", "K", "M", "G", "T"):
        if abs(num) < 1024.0:
            return f"{num:3.2f} {unit}{suffix}"
        num /= 1024.0
    return f"{num:.2f} Pi{suffix}"


class TranscriptionStatus(Enum):
    SKIPPED = 0
    WAITING = 1
    UNPACKING = 2
    LOADING = 3
    TRANSCRIBING = 4
    DIARIZING = 5
    ENCRYPTING = 6
    RETURNING = 7
    FAILED = 8
    COMPLETED = 9


class OptionsScreen(ModalScreen[dict]):
    def __init__(
        self,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ):
        super().__init__(name, id, classes)
        self.options = {}

    def compose(self) -> ComposeResult:
        yield Vertical(
            VerticalScroll(
                Grid(
                    Label("Audio track", classes="query"),
                    Label(self.name),
                    Label("Models used"),
                    SelectionList[str](
                        Selection("OpenAI large-v2", "large-v2", True),
                        Selection("NB AI-Lab large-beta", "nb-large"),
                        Selection("Diarization", "diarize", True),
                        id="models",
                    ),
                    Label("Minimum speakers", classes="diarize"),
                    Input(
                        placeholder="1",
                        id="min_speakers",
                        classes="diarize",
                        validators=[Integer(minimum=1)],
                    ),
                    Label("Maximum speakers", classes="diarize"),
                    Input(
                        placeholder="Infinite",
                        id="max_speakers",
                        classes="diarize",
                        validators=[Integer(minimum=1)],
                    ),
                    Label("Prompt"),
                    Input(id="prompt"),
                    id="inputs",
                ),
            ),
            Button("Apply", variant="primary", id="apply"),
            Button("Reset", id="reset"),
            Button("Set as defaults", id="set_defaults"),
            id="dialog",
        )

    @on(Input.Submitted)
    def submit_input_field_value(self, event: Input.Submitted) -> None:
        """Store input field value if it can be validated after enter is pressed,
        otherwise clear it.
        """
        if event.validation_result and not event.validation_result.is_valid:
            self.options.pop(event.input.id, None)
            event.input.value = ""
            return

        self.options[event.input.id] = event.input.value

    @on(SelectionList.SelectedChanged)
    def submit_models_selection(self, event: SelectionList.SelectedChanged) -> None:
        self.options[event.selection_list.id] = event.selection_list.selected

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Return options if any of the 3 apply/reset buttons are pressed."""
        for field in self.query(Input):
            await field.action_submit()

        self.dismiss(
            (
                {} if event.button.id == "reset" else self.options,  # Options
                None if event.button.id == "set_defaults" else self.name,  # Target
            )
        )

    def key_escape(self) -> None:
        """Return without saving if ESC key is pressed."""
        self.app.pop_screen()

    @on(events.Click)
    def mouse_escape(self, event: events.Click):
        """Return without saving if clicking outside OptionsScreens area."""
        if event.y == event.screen_y and event.x == event.screen_x:
            self.app.pop_screen()

    @on(SelectionList.SelectedChanged)
    def update_selected_view(self) -> None:
        """Update options depending on selected models."""
        diarize = "diarize" in self.query_one(SelectionList).selected
        for widget in self.query(".diarize"):
            widget.visible = diarize
            # widget.display = diarize


async def run_proc(program, *args, rlog=None):
    proc = await asyncio.create_subprocess_exec(
        program,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    if rlog:
        rlog.write(
            Text.assemble(
                (">> ", "bright_white"),
                (program + " ", "blue italic"),
                (" ".join(args), "magenta"),
            )
        )

    async for is_stdout, f in merge(
        decorate_with(True, proc.stdout), decorate_with(False, proc.stderr)
    ):
        if not rlog:
            continue

        log_line = Text(
            f.decode().strip(), style="steel_blue1" if is_stdout else "bright_red"
        )
        rlog.write(log_line)

    return await proc.wait()


class SelectionScreen(Screen):
    BINDINGS = [
        ("d", "decrypt", "Add decryption key"),
        ("l", "log", "Toggle log"),
        ("x", "reboot", "Restart computer"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="filelist", fixed_columns=1)
        with VerticalScroll(classes="details"):
            yield Pretty("Waiting for media files to make an appearance...")
        yield Button("Start", id="start", variant="success")
        yield RichLog(id="log", wrap=True, classes="tiny")
        yield Footer()

        self.populate_filelist(self.name)

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("Status", width=10, key="status")
        table.add_column("Filename", width=60, key="filename")
        table.add_column("Type", width=11, key="type")
        table.add_column("Encrypted", key="encrypted")
        table.add_column("Size", key="size")
        table.add_column("Length", width=11, key="length")
        table.cursor_type = "row"

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        info = self.query_one(Pretty)
        track = self.app.audio_tracks[event.row_key.value]
        task = self.app.tasks.get(event.row_key.value, {})
        info.update((task, track))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        def check_options(options_results):
            options, target = options_results

            # New defaults?
            if not target:
                self.app.defaults = options
                self.update_rows(data_table=event.data_table)
                return

            self.app.tasks[event.row_key.value] = options
            self.update_rows(event.row_key, event.data_table)

        self.app.push_screen(OptionsScreen(event.row_key.value), check_options)

    def update_rows(self, row_keys=None, data_table=None):
        if not data_table:
            data_table = self.query_one(DataTable)

        if not row_keys:
            row_keys = data_table.rows.keys()
        elif not hasattr(row_keys, '__iter__'):
            row_keys = (row_keys, )

        for row_key in row_keys:
            task = self.get_row_task(row_key.value)
            status = self.get_row_status(row_key.value)

            if (
                "models" in task
                and not task["models"]
                and status == TranscriptionStatus.WAITING
            ):
                status = TranscriptionStatus.SKIPPED

            data_table.update_cell(row_key, "status", str(status.name).capitalize())

    def on_track_added(self, key, entry) -> None:
        self.app.audio_tracks[key] = entry
        datatable = self.query_one("#filelist", DataTable)

        track = entry["audio_track"]

        if "samples_count" in track and "sampling_rate" in track:
            duration = str(
                timedelta(seconds=int(track["samples_count"]) / track["sampling_rate"])
            )
        elif "duration" in track:
            duration = str(timedelta(milliseconds=int(track["duration"])))
        else:
            duration = ""

        datatable.add_row(
            "Waiting",
            Text(
                "..." + key[-57:] if len(key) > 59 else key,
                style="bold",
                justify="right",
            ),
            entry["format"],
            "X" if "encrypted" in entry else "",
            Text(sizeof_fmt(entry["size"]), justify="right"),
            duration,
            key=key,
        )

    @work(exclusive=True, thread=True)
    def populate_filelist(self, directory: str) -> None:
        worker = get_current_worker()

        monitor = monitor_directory(directory, worker)
        for file_entry in monitor:
            if worker.is_cancelled:
                monitor.close()
                break

            key = file_entry["path"]

            if "archive_path" in file_entry:
                key += "/" + file_entry["archive_path"]

            audio_tracks = file_entry.get(
                "audio_tracks",
                [
                    {},
                ],
            )
            file_entry.pop("audio_tracks", None)

            for i, track in enumerate(audio_tracks):
                if "track_id" in track:
                    i = int(track["track_id"])

                audio_entry = file_entry.copy()
                audio_entry["audio_track"] = track
                self.app.call_from_thread(
                    self.on_track_added, "{}/{}".format(key, i), audio_entry
                )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        pass

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start":
            self.process_queue()
            event.button.display = False

    def get_row_task(self, row_key: object | str) -> dict[str, str | list]:
        if not isinstance(row_key, str):
            row_key = row_key.value

        task = self.app.defaults.copy()
        task.update(self.app.tasks.get(row_key, {}))
        return task

    def set_row_status(self, row_key: object | str, status: TranscriptionStatus = TranscriptionStatus.WAITING) -> None:
        if not isinstance(row_key, str):
            row_key = row_key.value
        
        task = self.app.tasks.setdefault(row_key, {})
        task.update({"status": status})
        self.update_rows(row_key)

    def get_row_status(self, row_key: object | str) -> TranscriptionStatus:
        if not isinstance(row_key, str):
            row_key = row_key.value

        task = self.get_row_task(row_key)
        return task.get("status", TranscriptionStatus.WAITING)

    @work(exclusive=True)
    async def process_queue(self) -> None:
        worker = get_current_worker()
        rlog = self.query_one("#log", RichLog)
        data_table = self.query_one(DataTable)

        for row in data_table.ordered_rows:
            status = self.get_row_status(row.key)

            if status != TranscriptionStatus.WAITING:
                continue

            track = self.app.audio_tracks[row.key.value]
            task = self.get_row_task(row.key.value)

            if track.get("encrypted", False):
                self.set_row_status(row.key, TranscriptionStatus.FAILED)
                continue

            with TemporaryDirectory(
                ignore_cleanup_errors=True, dir="/tmp/"
            ) as temp_dir:
                chdir(temp_dir)

                rlog.write(
                    Text(
                        "\nProcessing {} in {}...".format(row.key.value, temp_dir),
                        style="bright_magenta italic",
                    )
                )

                if "archive_path" in track:
                    self.set_row_status(row.key, TranscriptionStatus.UNPACKING)
                    ret = await run_proc(
                        "/usr/bin/bsdtar",
                        "-x",
                        "-k",
                        "-f",
                        track["path"],
                        track["archive_path"],
                        rlog=rlog,
                    )
                    if not ret == 0:
                        self.set_row_status(row.key, TranscriptionStatus.FAILED)
                        continue

                    track["extracted_path"] = path.join(
                        track["path"], track["archive_path"]
                    )

                self.set_row_status(row.key, TranscriptionStatus.LOADING)

                source_path = track.get("extracted_path", None) or track["path"]
                audio_file = path.basename(source_path) + ".oga"
                ffmpeg_arg = (
                    "ffmpeg -hide_banner -loglevel warning "
                    "-i {} -ac 1 -ar 16000 -c:a libvorbis -q:a 10 {}".format(
                        quote(source_path), quote(audio_file)
                    )
                )

                ret = await run_proc("/bin/bash", "-c", ffmpeg_arg, rlog=rlog)

                if not ret == 0:
                    self.set_row_status(row.key, TranscriptionStatus.FAILED)
                    continue

                self.set_row_status(row.key, TranscriptionStatus.TRANSCRIBING)

                whisper_args = [
                    "whisperx --language no --model large-v2 --compute_type float32"
                ]

                if "diarize" in task["models"]:
                    whisper_args.append(
                        "--align_model NbAiLab/wav2vec2-xlsr-300m-norwegian --diarize"
                    )

                    if "min_speakers" in task:
                        whisper_args.append(
                            "--min_speakers {}".format(task["min_speakers"])
                        )

                    if "max_speakers" in task:
                        whisper_args.append(
                            "--max_speakers {}".format(task["max_speakers"])
                        )

                if "prompt" in task:
                    whisper_args.append("--initial_prompt {}".format(quote(task["prompt"])))

                whisper_args.append(quote(audio_file))

                ret = await run_proc(
                    "/bin/bash", "-c", " ".join(whisper_args), rlog=rlog
                )

                if not ret == 0 and False:
                    self.set_row_status(row.key, TranscriptionStatus.FAILED)
                    continue

                self.set_row_status(row.key, TranscriptionStatus.RETURNING)
                output_path = track["path"]

                while output_path.endswith("/") or output_path.endswith("\\"):
                    output_path = output_path[:-1]

                output_path += ".output"
                makedirs(output_path, exist_ok=True)
                await copytree(
                    ".",
                    output_path,
                    ignore=ignore_patterns("*.oga"),
                    symlinks=False,
                    ignore_dangling_symlinks=True,
                )

                self.set_row_status(row.key, TranscriptionStatus.COMPLETED)

    def action_reboot(self) -> None:
        self.app.exit()

    def action_log(self) -> None:
        rlog = self.query_one("#log", RichLog)
        if "tiny" in rlog.classes:
            rlog.remove_class("tiny")
            rlog.add_class("sidebar")
        else:
            rlog.add_class("tiny")
            rlog.remove_class("sidebar")

    def action_decrypt(self) -> None:
        rlog = self.query_one("#log", RichLog)
        rlog.write("Decryption is not yet implemented.")
