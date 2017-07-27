import contextlib
import os
import shutil
import sys
import tempfile
from importlib import import_module
from pathlib import Path
from types import ModuleType
from unittest import skipUnless

from django.test import SimpleTestCase
from django.test.utils import extend_sys_path
from django.utils import autoreload


@contextlib.contextmanager
def change_dir(path):
    old_cwd = Path.cwd()
    os.chdir(str(path))
    try:
        yield path
    finally:
        os.chdir(str(old_cwd))


@contextlib.contextmanager
def add_module(module):
    sys.modules[module.__name__] = module
    try:
        yield module
    finally:
        del sys.modules[module.__name__]


class TestStatReloader(SimpleTestCase):
    def setUp(self):
        temp_dir_path = tempfile.mkdtemp()
        self.temp_dir = Path(temp_dir_path)
        self.reloader = autoreload.StatReloader()
        self.addCleanup(shutil.rmtree, temp_dir_path)

    def test_snapshot_stats_file(self):
        new_file = self.temp_dir / 'temp.txt'
        new_file.touch()
        self.reloader.watch(new_file)
        mtime = new_file.stat().st_mtime

        snapshot = dict(self.reloader.snapshot())
        self.assertEqual(snapshot[new_file], mtime)


class TestIterModules(SimpleTestCase):
    def setUp(self):
        temp_dir_path = tempfile.mkdtemp()
        self.temp_dir = Path(temp_dir_path)
        self.addCleanup(shutil.rmtree, temp_dir_path)

    def test_contains_imported_modules(self):
        with extend_sys_path(str(self.temp_dir)):
            py_file = self.temp_dir / 'test_new_module.py'
            py_file.touch()
            import_module('test_new_module')

        module_files = list(autoreload.iter_all_python_module_files())
        self.assertIn(py_file, module_files)

    def test_does_not_rename_pyc(self):
        module = ModuleType('test-module')
        module.__file__ = str(self.temp_dir / 'test.pyc')

        with add_module(module):
            module_files = list(autoreload.iter_all_python_module_files())
            self.assertIn(self.temp_dir / 'test.pyc', module_files)

    def test_does_not_rename_pyo(self):
        module = ModuleType('test-module')
        module.__file__ = str(self.temp_dir / 'test.pyo')

        with add_module(module):
            module_files = list(autoreload.iter_all_python_module_files())
            self.assertIn(self.temp_dir / 'test.pyo', module_files)


class TestGetChildArguments(SimpleTestCase):
    def setUp(self):
        self.reloader = autoreload.BaseReloader()

    @skipUnless(os.name == 'nt', 'Only relevant on Windows')
    def test_child_arguments_nt_exe(self):
        temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(temp))

        script_path = temp / 'my_script'
        script_exe_path = temp / 'my_script.exe'
        script_exe_path.touch()

        args = self.reloader.get_child_arguments([str(script_path)])
        self.assertSequenceEqual(args, [str(script_exe_path)])

    def test_child_arguments_absolute(self):
        script = Path('some_script.py')
        args = self.reloader.get_child_arguments([str(script)])
        self.assertSequenceEqual(args, [str(script.absolute())])

    def test_child_arguments_warnings(self):
        script = Path('some_script.py')
        args = self.reloader.get_child_arguments([str(script)], warnings=['abc', 'def'])
        expected = [str(script.absolute()), '-Wabc', '-Wdef']
        self.assertSequenceEqual(args, expected)

    def test_child_arguments_appends_args(self):
        script = Path('some_script.py')
        args = self.reloader.get_child_arguments([str(script), 'abc', 'def'])
        expected = [str(script.absolute()), 'abc', 'def']
        self.assertSequenceEqual(args, expected)
