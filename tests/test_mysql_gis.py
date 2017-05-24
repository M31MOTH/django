from test_mysql import *


DATABASES['default']['ENGINE'] = 'django.contrib.gis.db.backends.mysql'
DATABASES['other']['ENGINE'] = 'django.contrib.gis.db.backends.mysql'
