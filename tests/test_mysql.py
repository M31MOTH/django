from test_sqlite import *  # NOQA

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'USER': 'root',
        'HOST': '127.0.0.1',
        'PASSWORD': '',
        'NAME': 'django1',
        'OPTIONS': {
            'init_command': 'SET default_storage_engine=INNODB; SET sql_mode=STRICT_ALL_TABLES',
        },
        'TEST': {
            'CHARSET': 'utf8',
            'COLLATION': 'utf8_general_ci',
        },
    },
    'other': {
        'ENGINE': 'django.db.backends.mysql',
        'HOST': '127.0.0.1',
        'USER': 'root',
        'PASSWORD': '',
        'NAME': 'django2',
        'OPTIONS': {
            'init_command': 'SET default_storage_engine=INNODB; SET sql_mode=STRICT_ALL_TABLES',
        },
        'TEST': {
            'CHARSET': 'utf8',
            'COLLATION': 'utf8_general_ci',
        },
    },
}
