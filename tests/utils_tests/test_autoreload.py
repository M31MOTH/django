import gettext
import os
import shutil
import tempfile
from importlib import import_module
from unittest import mock

import _thread

from django import conf
from django.contrib import admin
from django.test import SimpleTestCase, override_settings
from django.test.utils import extend_sys_path
from django.utils import autoreload
from django.utils.translation import trans_real

LOCALE_PATH = os.path.join(os.path.dirname(__file__), 'locale')


class TestFilenameGenerator(SimpleTestCase):
    def test_paths_are_native_strings(self):
        for filename in autoreload.gen_filenames():
            self.assertIsInstance(filename, str)

    def test_only_new_files(self):
        """
        When calling a second time gen_filenames with only_new = True, only
        files from newly loaded modules should be given.
        """
        dirname = tempfile.mkdtemp()
        filename = os.path.join(dirname, 'test_only_new_module.py')
        self.addCleanup(shutil.rmtree, dirname)
        with open(filename, 'w'):
            pass

        # Test uncached access
        self.clear_autoreload_caches()
        filenames = set(autoreload.gen_filenames(only_new=True))
        filenames_reference = set(autoreload.gen_filenames())
        self.assertEqual(filenames, filenames_reference)

        # Test cached access: no changes
        filenames = set(autoreload.gen_filenames(only_new=True))
        self.assertEqual(filenames, set())

        # Test cached access: add a module
        with extend_sys_path(dirname):
            import_module('test_only_new_module')
        filenames = set(autoreload.gen_filenames(only_new=True))
        self.assertEqual(filenames, {filename})

    def test_deleted_removed(self):
        """
        When a file is deleted, gen_filenames() no longer returns it.
        """
        dirname = tempfile.mkdtemp()
        filename = os.path.join(dirname, 'test_deleted_removed_module.py')
        self.addCleanup(shutil.rmtree, dirname)
        with open(filename, 'w'):
            pass

        with extend_sys_path(dirname):
            import_module('test_deleted_removed_module')
        self.assertFileFound(filename)

        os.unlink(filename)
        self.assertFileNotFound(filename)

    def test_check_errors(self):
        """
        When a file containing an error is imported in a function wrapped by
        check_errors(), gen_filenames() returns it.
        """
        dirname = tempfile.mkdtemp()
        filename = os.path.join(dirname, 'test_syntax_error.py')
        self.addCleanup(shutil.rmtree, dirname)
        with open(filename, 'w') as f:
            f.write("Ceci n'est pas du Python.")

        with extend_sys_path(dirname):
            with self.assertRaises(SyntaxError):
                autoreload.check_errors(import_module)('test_syntax_error')
        self.assertFileFound(filename)

    def test_check_errors_only_new(self):
        """
        When a file containing an error is imported in a function wrapped by
        check_errors(), gen_filenames(only_new=True) returns it.
        """
        dirname = tempfile.mkdtemp()
        filename = os.path.join(dirname, 'test_syntax_error.py')
        self.addCleanup(shutil.rmtree, dirname)
        with open(filename, 'w') as f:
            f.write("Ceci n'est pas du Python.")

        with extend_sys_path(dirname):
            with self.assertRaises(SyntaxError):
                autoreload.check_errors(import_module)('test_syntax_error')
        self.assertFileFoundOnlyNew(filename)

    def test_check_errors_catches_all_exceptions(self):
        """
        Since Python may raise arbitrary exceptions when importing code,
        check_errors() must catch Exception, not just some subclasses.
        """
        dirname = tempfile.mkdtemp()
        filename = os.path.join(dirname, 'test_exception.py')
        self.addCleanup(shutil.rmtree, dirname)
        with open(filename, 'w') as f:
            f.write("raise Exception")

        with extend_sys_path(dirname):
            with self.assertRaises(Exception):
                autoreload.check_errors(import_module)('test_exception')
        self.assertFileFound(filename)


class CleanFilesTests(SimpleTestCase):
    TEST_MAP = {
        # description: (input_file_list, expected_returned_file_list)
        'falsies': ([None, False], []),
        'pycs': (['myfile.pyc'], ['myfile.py']),
        'pyos': (['myfile.pyo'], ['myfile.py']),
        '$py.class': (['myclass$py.class'], ['myclass.py']),
        'combined': (
            [None, 'file1.pyo', 'file2.pyc', 'myclass$py.class'],
            ['file1.py', 'file2.py', 'myclass.py'],
        )
    }

    def _run_tests(self, mock_files_exist=True):
        with mock.patch('django.utils.autoreload.os.path.exists', return_value=mock_files_exist):
            for description, values in self.TEST_MAP.items():
                filenames, expected_returned_filenames = values
                self.assertEqual(
                    autoreload.clean_files(filenames),
                    expected_returned_filenames if mock_files_exist else [],
                    msg='{} failed for input file list: {}; returned file list: {}'.format(
                        description, filenames, expected_returned_filenames
                    ),
                )

    def test_files_exist(self):
        """
        If the file exists, any compiled files (pyc, pyo, $py.class) are
        transformed as their source files.
        """
        self._run_tests()

    def test_files_do_not_exist(self):
        """
        If the files don't exist, they aren't in the returned file list.
        """
        self._run_tests(mock_files_exist=False)
