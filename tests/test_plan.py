# tests/test_plan.py

# this is just an example to not have an empty test file

import unittest
from plan_model import Plan


class TestPlan(unittest.TestCase):
    def test_plan_initialization(self):
        p = Plan()
        self.assertIsInstance(p, Plan)
        self.assertEqual(p.scaling, 1.0)
        self.assertEqual(p.n_fields, 0)

    def test_plan_fields_list(self):
        p = Plan()
        self.assertIsInstance(p.fields, list)
        self.assertEqual(len(p.fields), 0)


if __name__ == '__main__':
    unittest.main()
