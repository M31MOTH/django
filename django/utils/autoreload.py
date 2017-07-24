import sys
import pathlib
import threading
import contextlib
import subprocess
import os
import time

# Globals are bad :'(
_reloader = None

DJANGO_AUTORELOAD_KEY = 'RUN_MAIN'


def watch_directory(directory, glob):
    if _reloader is not None:
        _reloader.watch_directory(directory, glob)


def watch_file(path):
    if _reloader is not None:
        _reloader.watch_file(path)


def iter_all_python_module_files():
    for module in list(sys.modules.values()):
        filename = getattr(module, '__file__', None)

        if not module or not filename:
            continue

        path = pathlib.Path(filename)

        if path.suffix in {'.pyc', '.pyo'}:
            yield path.with_suffix('.py')

        yield path


class BaseReloader:
    def __init__(self):
        self.extra_files = set()
        self.extra_directories = set()

    def watch_directory(self, path_instance, file_glob):
        if not path_instance.is_dir():
            raise RuntimeError('{0} is not a directory'.format(path_instance))

        self.extra_directories.add((path_instance, file_glob))

    def watch_file(self, path_instance):
        self.extra_files.add(path_instance)

    def watched_files(self):
        yield from iter_all_python_module_files()
        yield from iter(self.extra_files)

        for directory, pattern in self.extra_directories:
            yield from directory.glob(pattern)

    def run(self):
        pass

    def get_child_arguments(self):
        """
        Returns the executable. This contains a workaround for windows
        if the executable is incorrectly reported to not have the .exe
        extension which can cause bugs on reloading.
        """
        py_script = sys.argv[0]
        if os.name == 'nt' and not os.path.exists(py_script) and \
            os.path.exists(py_script + '.exe'):
            py_script += '.exe'

        return [py_script] + ['-W%s' % o for o in sys.warnoptions] + sys.argv[1:]

    def restart_with_reloader(self):
        new_environ = os.environ.copy()
        new_environ[DJANGO_AUTORELOAD_KEY] = '1'
        args = self.get_child_arguments()

        while True:
            exit_code = subprocess.call(args, env=new_environ, close_fds=False)

            if exit_code != 3:
                return exit_code

    def trigger_reload(self, filename):
        print('{0} changed, reloading'.format(filename))
        sys.exit(3)


class StatReloader(BaseReloader):
    def run(self):
        modify_times = {}

        while True:
            for file in self.watched_files():
                try:
                    mtime = file.stat().st_mtime
                except OSError:
                    continue

                old_time = modify_times.get(file)
                if old_time is None:
                    modify_times[file] = mtime
                elif mtime > old_time:
                    self.trigger_reload(file)

            time.sleep(1)


def run_with_reloader(main_func, *args, **kwargs):
    import signal
    signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))

    with contextlib.suppress(KeyboardInterrupt):
        if os.environ.get(DJANGO_AUTORELOAD_KEY) == '1':
            _reloader = StatReloader()

            thread = threading.Thread(target=main_func, args=args, kwargs=kwargs)
            thread.setDaemon(True)
            thread.start()
            _reloader.run()
        else:
            exit_code = StatReloader().restart_with_reloader()
            sys.exit(exit_code)
