import gettext
import os
import threading
from pathlib import Path

from django import conf
from django.contrib import admin
from django.test import SimpleTestCase, override_settings
from django.utils.autoreload import (
    StatReloader, autoreload_started, file_changed,
)
from django.utils.translation import trans_real

module_path = Path(__file__).parent
test_locale_dir = module_path / 'other'
admin_locale_dir = Path(admin.__file__).parent / 'locale'
django_locale_dir = Path(conf.__file__).parent / 'locale'

test_locale_file = test_locale_dir / 'locale' / 'de' / 'LC_MESSAGES' / 'django.mo'
django_locale_file = django_locale_dir / 'nl' / 'LC_MESSAGES' / 'django.mo'


class TestAutoReloadRegister(SimpleTestCase):
    def send_and_get_files(self):
        reloader = StatReloader()
        autoreload_started.send(reloader)
        return list(reloader.watched_files())

    @override_settings(USE_I18N=False)
    def test_i8n_disabled(self):
        files = self.send_and_get_files()
        self.assertNotIn('.mo', list(f.suffix for f in files))

    def test_i8n_enabled(self):
        files = self.send_and_get_files()
        self.assertIn('.mo', list(f.suffix for f in files))

    def test_django_locales(self):
        files = self.send_and_get_files()
        self.assertIn(django_locale_file, files)

    @override_settings(LOCALE_PATHS=[str(test_locale_dir)])
    def test_locale_paths_setting(self):
        files = self.send_and_get_files()
        self.assertIn(test_locale_file, files)

    @override_settings(INSTALLED_APPS=[])
    def test_project_root_locale(self):
        old_cwd = os.getcwd()
        os.chdir(str(test_locale_dir))
        files = self.send_and_get_files()
        try:
            self.assertIn(test_locale_file, files)
        finally:
            os.chdir(old_cwd)

    @override_settings(INSTALLED_APPS=['django.contrib.admin'])
    def test_app_locales(self):
        files = self.send_and_get_files()
        self.assertIn(admin_locale_dir / 'nl' / 'LC_MESSAGES' / 'django.mo', files)


class TestAutoReloadFileChanged(SimpleTestCase):

    def setUp(self):
        self.gettext_translations = gettext._translations.copy()
        self.trans_real_translations = trans_real._translations.copy()

    def tearDown(self):
        gettext._translations = self.gettext_translations
        trans_real._translations = self.trans_real_translations

    @staticmethod
    def trigger_change(path):
        results = file_changed.send(StatReloader(), file_path=path)
        return [r[1] for r in results]

    def test_py_file_returns_none(self):
        results = self.trigger_change(Path(__file__))
        self.assertSequenceEqual(results, [None])

    def test_mo_file_returns_true(self):
        results = self.trigger_change(django_locale_file)
        self.assertSequenceEqual(results, [True])

    def test_mo_file_resets_gettext(self):
        gettext._translations = {'foo': 'bar'}
        self.trigger_change(django_locale_file)
        self.assertEqual(gettext._translations, {})

    def test_mo_file_resets_trans_real(self):
        trans_real._translations = {'foo': 'bar'}
        trans_real._default = 1
        trans_real._active = False
        self.trigger_change(django_locale_file)
        self.assertEqual(trans_real._translations, {})
        self.assertIsNone(trans_real._default)
        self.assertIsInstance(trans_real._active, threading.local)
