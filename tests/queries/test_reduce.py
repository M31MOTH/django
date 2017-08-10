from django.db.models import Count, Max
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from .models import School, Classroom, Student
from django.db import connection


class TestSimplify(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.school = School.objects.create()
        cls.classroom = Classroom.objects.create(school=cls.school, name='test')
        cls.student1 = Student.objects.create(school=cls.school)
        cls.student2 = Student.objects.create(school=cls.school)
        cls.classroom.students.add(cls.student1, cls.student2)

    def test_reduce(self):
        with CaptureQueriesContext(connection) as captured_queries:
            Classroom.objects\
                .annotate(Max('students'), a=Max('students'))\
                .filter(a__gte=1)\
                .count()

        self.assertNotIn('students__max', captured_queries[0]['sql'])
