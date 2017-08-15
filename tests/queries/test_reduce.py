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

    def test_reduce_with_no_dependents(self):
        with CaptureQueriesContext(connection) as captured_queries:
            Classroom.objects.count()

            Classroom.objects \
                .annotate(Max('students')) \
                .count()

        self.assertEqual(captured_queries[0]['sql'], captured_queries[1]['sql'])

    def test_reduce_with_dependents(self):
        with CaptureQueriesContext(connection) as captured_queries:
            Classroom.objects.count()

            Classroom.objects \
                .annotate(abc=Max('students'), xyz=Max('students')) \
                .filter(xyz__gt=1)\
                .count()

        self.assertEqual(captured_queries[0]['sql'], captured_queries[1]['sql'])
