import contextlib
import os
import pathlib
import queue
import signal
import sys
import threading
import time
import queue
import multiprocessing
from collections import namedtuple
from functools import lru_cache
from pathlib import Path

from django.apps import apps
from django.dispatch import Signal

try:
    import termios
except ImportError:
    termios = None

try:
    import pywatchman
except ImportError:
    pywatchman = None

try:
    import watchdog
except ImportError:
    watchdog = None

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


@lru_cache(maxsize=10)
def find_common_roots(paths):
    """Out of some paths it finds the common roots that need monitoring."""
    paths = [x.parts for x in paths]
    root = {}
    for chunks in sorted(paths, key=len, reverse=True):
        node = root
        for chunk in chunks:
            node = node.setdefault(chunk, {})
        node.clear()

    rv = set()

    def _walk(node, path):
        for prefix, child in node.items():
            _walk(child, path + (prefix,))
        if not node:
            subpath = '/'.join(path[1:])
            rv.add(path[0] + subpath)

    _walk(root, ())
    return rv


def wait_for_app_ready(conditional_thread=None):
    # This could be improved if there was some kind of `app_ready` signal
    while not apps.ready:
        time.sleep(0.1)
        if conditional_thread and not conditional_thread.is_alive():
            return True


class Reloader:
    def __init__(self):
        self.watch_queue = multiprocessing.Queue()
        self.change_queue = multiprocessing.Queue()
        self.started_event = multiprocessing.Event()
        self.child_process = None
        self.watched_files = set()
        self.watched_directories = {}

    def watch(self, path, glob=None):
        if isinstance(path, list):
            for path in path:
                self.watched_files.add(path)
        else:
            if glob:
                self.watched_directories[path] = glob
            else:
                self.watched_files.add(path)

    def reset_watches(self, paths=None):
        self.watched_files.clear()
        self.watched_directories.clear()
        if paths:
            self.watch(paths)

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

            self.child_process = multiprocessing.Process(target=execute_child, args=(sys.argv,), kwargs=kwargs)
            self.child_process.start()
            self.child_process.join()

            if not self.child_exited_due_to_reloader:
                return

    def read_watch_queue(self):
        while True:
            item = self.watch_queue.get()
            if isinstance(item, ResetWatchedFiles):
                self.reset_watches([Path(p) for p in item.paths])
            elif isinstance(item, list):
                for path, glob in item:
                    self.watch(Path(path), glob)
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
            if self.child_process:
                if self.child_process_exited and not self.child_exited_due_to_reloader:
                    self.start_child_process()
                else:
                    self.change_queue.put_nowait(change)

    def run(self):
        self.start_child_process()

        watch_queue_thread = threading.Thread(target=self.read_watch_queue, daemon=True)
        watch_queue_thread.start()

        self.watch(list(iter_all_python_module_files()))

        while True:
            self.watch_for_changes()

    def stop(self):
        pass

    def yield_changes(self):
        raise NotImplementedError()

    @classmethod
    def is_available(cls):
        raise NotImplementedError()

    @property
    def name(self):
        raise NotImplementedError()


class StatReloader(Reloader):
    SLEEP_DURATION = 1

    def __init__(self):
        super().__init__()
        self.name = 'stat'

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

    def all_watched_files(self):
        yield from self.watched_files
        for path, glob in list(self.watched_directories.items()):
            yield from path.glob(glob)

    def snapshot(self):
        for file in self.all_watched_files():
            try:
                mtime = file.stat().st_mtime
            except OSError:
                continue

            yield file, mtime

    @classmethod
    def is_available(cls):
        return True


class DirectoryReloader(Reloader):
    def __init__(self):
        super().__init__()
        self.watched_roots = set()

    def watch(self, path, glob=None):
        super().watch(path, glob)
        all_directories = list(self.watched_directories.keys()) + [f.parent for f in self.watched_files]
        roots = find_common_roots(tuple(all_directories))

        if roots != self.watched_roots:
            new_roots = roots - self.watched_roots
            removed_roots = self.watched_roots - roots
            self.unwatch_roots(removed_roots)
            self.watch_roots(new_roots)
            self.watched_roots = roots

    def watch_roots(self, roots):
        raise NotImplementedError()

    def unwatch_roots(self, roots):
        raise NotImplementedError()


class WatchmanReloader(DirectoryReloader):
    def __init__(self):
        super().__init__()
        self.client = pywatchman.client()
        self.client.setTimeout(10)
        self.name = 'Watchman'

    @property
    def subscription_name(self):
        return 'django:{0}'.format(os.getpid())

    def watch_roots(self, roots):
        for root in roots:
            self.client.query('subscribe', root, self.subscription_name, {'fields': ['name'],
                                                                          'expression': ['allof',
                                                                                         ['type', 'f'],
                                                                                         ['not', ['suffix', 'pyc']]],
                                                                          'dedup_results': True})

    def unwatch_roots(self, roots):
        for root in roots:
            self.client.query('unsubscribe', root, self.subscription_name)

    def stop(self):
        super().stop()
        self.unwatch_roots(self.watched_roots)

    def yield_changes(self):
        while True:
            try:
                result = self.client.receive()
            except pywatchman.SocketTimeout as e:
                continue

            if not result['is_fresh_instance']:
                root = Path(result['root'])
                for path in result['files']:
                    yield root / path

    @classmethod
    def is_available(cls):
        if pywatchman is None:
            return False

        with pywatchman.client() as c:
            c.setTimeout(1)
            try:
                return 'version' in c.capabilityCheck()
            except Exception:
                return False


class WatchdogReloader(DirectoryReloader):
    def __init__(self):
        super().__init__()
        self._event_queue = queue.Queue()
        self._root_watches = {}

        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler

        def _check_modification(path):
            if path in self.watched_files:
                self._event_queue.put(path)

            for directory in self.watched_directories:
                if directory in path.parents:
                    self._event_queue.put(path)
                    break

        class _CustomHandler(FileSystemEventHandler):

            def on_created(self, event):
                _check_modification(Path(event.src_path))

            def on_modified(self, event):
                _check_modification(Path(event.src_path))

            def on_moved(self, event):
                _check_modification(Path(event.src_path))
                _check_modification(Path(event.dest_path))

            def on_deleted(self, event):
                _check_modification(Path(event.src_path))

        self.observer = Observer()
        self.handler = _CustomHandler()
        self.observer.start()

    @property
    def name(self):
        reloader_name = self.observer.__class__.__name__.lower()
        if reloader_name.endswith('observer'):
            reloader_name = reloader_name[:-8]
        return reloader_name

    def watch_roots(self, roots):
        for root in roots:
            watch = self.observer.schedule(self.handler, str(root), recursive=True)
            self._root_watches[root] = watch

    def unwatch_roots(self, roots):
        for root in roots:
            watch = self._root_watches.pop(root)
            self.observer.unschedule(watch)

    def yield_changes(self):
        while True:
            yield self._event_queue.get()

    @classmethod
    def is_available(cls):
        return watchdog is not None


def run_with_reloader(main_func, *args, **kwargs):
    with contextlib.suppress(KeyboardInterrupt):
        if os.environ.get(DJANGO_AUTORELOAD_ENV) == '1':
            main_func(*args, **kwargs)
        else:
            WATCHERS = (
                WatchmanReloader,
                WatchdogReloader,
                StatReloader,
            )

            available_watchers = [
                cls
                for cls in WATCHERS
                if cls.is_available()
            ]

            reloader = available_watchers[0]

            multiprocessing.set_start_method('spawn')
            reloader = reloader()
            print('Reloading using: {0}'.format(reloader.name))
            try:
                reloader.run()
            finally:
                reloader.stop()
