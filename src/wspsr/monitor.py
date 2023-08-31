import magic
import libarchive

from watchdog.utils.dirsnapshot import *
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from pymediainfo import MediaInfo

from os import lstat
from multiprocessing import Process, Queue
from queue import Empty
from mimetypes import guess_type


def peek_worker(input_queue, output_queue):
    for filename in iter(input_queue.get, None):
        mime = magic.from_file(filename, mime=True)
        statinfo = lstat(filename)
        observation = {
            'path': filename,
            'mime': mime,
            'format': magic.from_file(filename),
            'size': statinfo.st_size,
            'ctime': statinfo.st_ctime,
            'mtime': statinfo.st_mtime,
        }

        if mime.startswith('audio/') or mime.startswith('video/'):
            media_info = MediaInfo.parse(filename)
            audio_tracks = [track.to_data() for track in media_info.tracks if track.track_type == 'Audio']
            observation['audio_tracks'] = audio_tracks
            output_queue.put(observation)
            continue

        try:
            with libarchive.file_reader(filename) as archive:
                for entry in archive:
                    if entry.isreg:
                        entry_mime = guess_type(entry.pathname, strict=False)[0] or ''

                        if entry_mime.startswith('audio/') or entry_mime.startswith('video/'):
                            observation = observation.copy()
                            try:
                                for block in entry.get_blocks():
                                    break
                            except libarchive.exception.ArchiveError as e:
                                if 'encrypted' in e.msg.lower() or 'passphrase' in e.msg.lower():
                                    observation['encrypted'] = True
                                else:
                                    print("Entry error", e)
                                    raise e

                            observation.update({
                                'archive_path': entry.pathname,
                                'archive_size': entry.size,
                                'archive_ctime': entry.ctime,
                                'archive_mtime': entry.mtime,
                            })
                            output_queue.put(observation)

        except libarchive.exception.ArchiveError as e:
            continue


class MountsDirectoryHandler(FileSystemEventHandler):
    def __init__(self, path, file_queue):
        self.path = path
        self.file_queue = file_queue
        self.previous_snapshot = EmptyDirectorySnapshot()
        self.queue_new_files()

    def on_any_event(self, event):
        print(event)
        self.queue_new_files()

    def queue_new_files(self):
        snapshot = DirectorySnapshot(self.path)
        changes = DirectorySnapshotDiff(self.previous_snapshot, snapshot)
        self.previous_snapshot = snapshot

        for file in changes.files_created:
            self.file_queue.put(file)


def monitor_directory(path, worker=None):
    peek_queue, tui_queue = Queue(), Queue()
    peek_process = Process(target=peek_worker, args=(peek_queue, tui_queue))

    mounts_handler = MountsDirectoryHandler(path, peek_queue)
    mount_observer = Observer()
    mount_observer.schedule(mounts_handler, path, recursive=False)

    peek_process.start()
    mount_observer.start()

    try:
        while mount_observer.is_alive() and \
                peek_process.is_alive() and \
                (not worker or not worker.is_cancelled):
            try:
                yield tui_queue.get(timeout=1)
            except Empty:
                continue
            except GeneratorExit:
                break
    finally:
        peek_queue.put(None)
        mount_observer.stop()
        peek_process.terminate()
        peek_process.join(4)
        if peek_process.exitcode is None:
            peek_process.kill()
        mount_observer.join()


if __name__ == "__main__":
    for row in monitor_directory("../media"):
        print(row)
