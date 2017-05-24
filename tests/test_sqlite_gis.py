from test_sqlite import *  # NOQA

DATABASES['default']['ENGINE'] = 'django.contrib.gis.db.backends.spatialite'
DATABASES['other']['ENGINE'] = 'django.contrib.gis.db.backends.spatialite'

#SPATIALITE_LIBRARY_PATH = 'mod_spatialite'
