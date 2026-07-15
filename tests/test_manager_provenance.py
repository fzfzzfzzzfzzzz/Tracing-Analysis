import unittest

from tracegraph.manager_provenance import manager_provenance


class ManagerProvenanceTests(unittest.TestCase):
    def test_returns_defensive_copy(self):
        first = manager_provenance(["acon_style"])
        first["acon_style"]["main_result_eligible"] = True
        second = manager_provenance(["acon_style"])
        self.assertFalse(second["acon_style"]["main_result_eligible"])

    def test_unknown_manager_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "missing scientific provenance"):
            manager_provenance(["unregistered"])

    def test_official_acon_requires_runtime_eligibility(self):
        provenance = manager_provenance(["acon_official"])["acon_official"]
        self.assertEqual(provenance["implementation_kind"], "external_official_adapter")
        self.assertFalse(provenance["main_result_eligible"])
        self.assertTrue(provenance["runtime_eligibility_required"])


if __name__ == "__main__":
    unittest.main()
