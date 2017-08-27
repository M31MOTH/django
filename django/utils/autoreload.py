import contextlib
import os
import pathlib
import queue
import signal
import sys
import threading
import time
from collections import namedtuple
from multiprocessing import Event, Process, Queue, set_start_method
from pathlib import Path

from django.apps import apps
from django.dispatch import Signal

try:
    import termios
except ImportError:
    termios = None

autoreload_started = Signal()
file_changed = Signal(providing_args=['path'])

ResetWatchedFiles = namedtuple('ResetWatchedFiles', 'paths')

DJANGO_AUTORELOAD_ENV = 'RUN_MAIN'


def ensure_echo_on():
    if termios:
        fd = sys.stdin
        if fd.isatty():
            attr_list = termios.tcgetattr(fd)
            if not attr_list[3] & termios.ECHO:
                attr_list[3] |= termios.ECHO
                if hasattr(signal, 'SIGTTOU'):
                    old_handler = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
                else:
                    old_handler = None
                termios.tcsetattr(fd, termios.TCSANOW, attr_list)
                if old_handler is not None:
                    signal.signal(signal.SIGTTOU, old_handler)


def trigger_reloader_started(watch_queue):
    def watch_function(path, glob=None):
        watch_queue.put_nowait([(path, glob)])

    wait_for_app_ready()

    all_module_files = set(iter_all_python_module_files())

    watch_queue.put_nowait(ResetWatchedFiles(all_module_files))

    autoreload_started.send(watch_function)

    while True:
        time.sleep(0.5)
        new_modules = set(iter_all_python_module_files())
        diff = new_modules - all_module_files
        if diff:
            watch_queue.put_nowait([(p, None) for p in diff])
            all_module_files = new_modules


def read_change_queue(change_queue, manage_py_thread):
    apps_failed = wait_for_app_ready(manage_py_thread)
    if apps_failed:
        sys.exit(1)

    while True:
        change = change_queue.get()
        # Not sure if this sender argument is correct...
        results = file_changed.send(sender=Reloader, file_path=change)
        if not any(res[1] for res in results):
            sys.exit(3)


def run_manage_py(argv):
    os.environ[DJANGO_AUTORELOAD_ENV] = '1'
    sys.argv = argv

    with open(sys.argv[0], 'r') as fd:
        code_block = compile(fd.read(), sys.argv[0], 'exec')
        exec(code_block, {'__name__': '__main__'})


def execute_child(argv, watch_queue, change_queue):
    signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))
    ensure_echo_on()

    reload_started_thread = threading.Thread(target=trigger_reloader_started, args=(watch_queue,), daemon=True)
    reload_started_thread.start()

    manage_py_thread = threading.Thread(target=run_manage_py, args=(argv,), daemon=True)
    manage_py_thread.start()

    with contextlib.suppress(KeyboardInterrupt):
        read_change_queue(change_queue, manage_py_thread)


def iter_all_python_module_files():
    for module in list(sys.modules.values()):
        filename = getattr(module, '__file__', None)

        if not module or not filename:
            continue

        yield pathlib.Path(filename).absolute()


def wait_for_app_ready(conditional_thread=None):
    # This could be improved if there was some kind of `app_ready` signal
    while not apps.ready:
        time.sleep(0.1)
        if conditional_thread and not conditional_thread.is_alive():
            return True


class Reloader:
    def __init__(self):
        self.watch_queue = Queue()
        self.change_queue = Queue()
        self.started_event = Event()

        self._watched_files = set()
        self.child_process = None

    @property
    def child_exited_due_to_reloader(self):
        return self.child_process.exitcode == 3

    @property
    def child_process_exited(self):
        return self.child_process.exitcode is not None

    def start_child_process(self):
        if self.child_process and self.child_process.is_alive():
            raise RuntimeError('Reloader already has an active child process')

        threading.Thread(target=self._child_process_loop, daemon=True).start()

    def _child_process_loop(self):
        kwargs = {'watch_queue': self.watch_queue,
                  'change_queue': self.change_queue}

        while True:
            self.flush_change_queue()

            self.child_process = Process(target=execute_child, args=(sys.argv,), kwargs=kwargs)
            self.child_process.start()
            self.child_process.join()

            if not self.child_exited_due_to_reloader:
                return

    def watched_files(self):
        for path, glob in self._watched_files:
            if glob:
                yield from path.glob(glob)
            else:
                yield path

    def watch(self, path, glob=None):
        path = Path(path)
        self._watched_files.add((path, glob))

    def read_watch_queue(self):
        while True:
            item = self.watch_queue.get()
            if isinstance(item, ResetWatchedFiles):
                self._watched_files = set()
                for path in item.paths:
                    self.watch(path)
            elif isinstance(item, list):
                for path, glob in item:
                    self.watch(path, glob)
            else:
                raise RuntimeError('Unknown watch_queue value: {0} {1}'.format(type(item), item))

    def flush_change_queue(self):
        while True:
            try:
                self.change_queue.get_nowait()
            except queue.Empty:
                return

    def watch_for_changes(self):
        for change in self.yield_changes():
            print(change)
            if self.child_process:
                if self.child_process_exited and not self.child_exited_due_to_reloader:
                    self.start_child_process()
                else:
                    self.change_queue.put_nowait(change)

    def run(self):
        self.start_child_process()

        watch_queue_thread = threading.Thread(target=self.read_watch_queue, daemon=True)
        watch_queue_thread.start()

        for module in iter_all_python_module_files():
            self.watch(module)

        while True:
            self.watch_for_changes()

    def yield_changes(self):
        raise NotImplementedError()


class StatReloader(Reloader):
    SLEEP_DURATION = 1

    def yield_changes(self):
        file_times = {}

        while True:
            for path, mtime in self.snapshot():
                previous_time = file_times.get(path)
                changed = previous_time != mtime

                if changed:
                    if previous_time is not None:
                        yield path

                    file_times[path] = mtime

            time.sleep(self.SLEEP_DURATION)

    def snapshot(self):
        for file in self.watched_files():
            try:
                mtime = file.stat().st_mtime
            except OSError:
                continue

            yield file, mtime


def run_with_reloader(main_func, *args, **kwargs):
    with contextlib.suppress(KeyboardInterrupt):
        if os.environ.get(DJANGO_AUTORELOAD_ENV) == '1':
            main_func(*args, **kwargs)
        else:
            set_start_method('spawn')
            StatReloader().run()
