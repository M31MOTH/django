from django.db import connection
from django.db.models import Count
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from .models import Classroom, School, Student

QUERIES = [
    (
        Classroom.objects.annotate(one=Count('students')),
        Classroom.objects.all()
    ),
]


class TestSimplify(TestCase):
    maxDiff = None

    @classmethod
    def setUpTestData(cls):
        cls.school = School.objects.create()
        cls.classroom = Classroom.objects.create(school=cls.school, name='test')
        cls.student1 = Student.objects.create(school=cls.school)
        cls.student2 = Student.objects.create(school=cls.school)
        cls.classroom.students.add(cls.student1, cls.student2)

    def test_reduce_query_count(self):
        for one, two in QUERIES:
            with self.subTest(one=one, two=two):
                with CaptureQueriesContext(connection) as captured_queries:
                    self.assertEqual(one.count(), two.count())

                self.assertEqual(captured_queries[0]['sql'], captured_queries[1]['sql'])
