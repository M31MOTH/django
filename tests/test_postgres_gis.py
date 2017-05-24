from test_postgres import *

DATABASES['default']['ENGINE'] = 'django.contrib.gis.db.backends.postgis'
DATABASES['other']['ENGINE'] = 'django.contrib.gis.db.backends.postgis'
