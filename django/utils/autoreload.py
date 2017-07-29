import contextlib
import os
import pathlib
import subprocess
import sys
import threading
import time
from pathlib import Path

from django.apps import apps
from django.dispatch import Signal

autoreload_started = Signal()
file_changed = Signal(providing_args=['path'])

DJANGO_AUTORELOAD_ENV = 'RUN_MAIN'


def iter_all_python_module_files():
    for module in list(sys.modules.values()):
        filename = getattr(module, '__file__', None)

        if not module or not filename:
            continue

        yield pathlib.Path(filename).absolute()


class BaseReloader:
    def __init__(self):
        self.extra_files = set()
        self.extra_directories = set()

    def watch(self, path, glob=None):
        path = Path(path)

        if glob:
            self.extra_directories.add((path, glob))
        else:
            self.extra_files.add(path.absolute())

    def wait_for_app_ready(self):
        while not apps.ready:
            time.sleep(0.1)

        autoreload_started.send(sender=self)

    def run(self):
        for path in self.yield_changes():
            results = file_changed.send(sender=self, file_path=path)
            if not any(res[1] for res in results):
                self.trigger_reload(path)

    def yield_changes(self):
        yield from []

    def get_child_arguments(self, argv, warnings=None):
        """
        Returns the executable. This contains a workaround for windows
        if the executable is incorrectly reported to not have the .exe
        extension which can cause bugs on reloading.
        """
        py_script = Path(argv[0]).absolute()
        py_script_exe_suffix = py_script.with_suffix('.exe')
        if os.name == 'nt' and not py_script.exists() and py_script_exe_suffix.exists():
            py_script = py_script_exe_suffix

        return [str(py_script)] + ['-W%s' % o for o in warnings or []] + argv[1:]

    def restart_with_reloader(self):
        new_environ = os.environ.copy()
        new_environ[DJANGO_AUTORELOAD_ENV] = '1'
        args = self.get_child_arguments(sys.argv, sys.warnoptions)

        while True:
            exit_code = subprocess.call(args, env=new_environ, close_fds=False)

            if exit_code != 3:
                return exit_code

    def trigger_reload(self, filename):
        print('{0} changed, reloading'.format(filename))
        sys.exit(3)


class StatReloader(BaseReloader):
    SLEEP_DURATION = 1

    def watched_files(self):
        yield from iter_all_python_module_files()
        yield from self.extra_files

        for directory, pattern in self.extra_directories:
            yield from directory.glob(pattern)

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
    import signal
    signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))

    with contextlib.suppress(KeyboardInterrupt):
        if os.environ.get(DJANGO_AUTORELOAD_ENV) == '1':
            thread = threading.Thread(target=main_func, args=args, kwargs=kwargs)
            thread.setDaemon(True)
            thread.start()

            reloader = StatReloader()
            reloader.wait_for_app_ready()
            reloader.run()
        else:
            exit_code = StatReloader().restart_with_reloader()
            sys.exit(exit_code)
