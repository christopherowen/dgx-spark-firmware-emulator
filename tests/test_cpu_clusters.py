"""The CPU cluster split, kept as a record with its own consistency rules.

The numbers come from three units (read-only sysfs), so there is no artifact to re-derive them from — but
the record can still be checked for the arithmetic that matters: 20 policies, ten and ten, disjoint id
sets that together cover 0..19, and a faster cluster that is actually faster.
"""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "research" / "cpu-clusters.json"


class CpuClusterTests(unittest.TestCase):
    def setUp(self):
        self.record = json.loads(RECORD.read_text())

    def test_the_two_clusters_cover_every_policy_exactly_once(self):
        clusters = self.record["clusters"]
        self.assertEqual(len(clusters), 2)
        ids = [cpu for cluster in clusters for cpu in cluster["cpus"]]
        self.assertEqual(len(ids), self.record["policies"], "every policy is in a cluster")
        self.assertEqual(sorted(ids), list(range(self.record["policies"])), "no gaps, no duplicates")

    def test_the_counts_match_the_lists(self):
        for cluster in self.record["clusters"]:
            self.assertEqual(cluster["count"], len(cluster["cpus"]), cluster["max_khz"])

    def test_the_split_is_ten_and_ten_with_the_faster_cluster_named(self):
        counts = sorted(cluster["count"] for cluster in self.record["clusters"])
        self.assertEqual(counts, [10, 10])
        fastest = max(cluster["max_khz"] for cluster in self.record["clusters"])
        self.assertEqual(fastest, 3900000, "the faster cluster is 3.9 GHz")

    def test_the_record_says_what_it_does_not_establish(self):
        self.assertIn("not_established", self.record)
        self.assertTrue(self.record["not_established"])
        self.assertIn("placement", self.record["what_it_means"])

    def test_boost_headroom_explains_every_policy_that_did_not_move(self):
        """A policy with no CPPC headroom cannot move, and the record must show that it has none.

        This is the difference between "boost did nothing there" and "the firmware advertises nothing
        to expose": the first is a measurement without an explanation, the second is one with.
        """
        head = self.record["boost_measured"]["why_only_half"]["measured_on_a_unit"]
        moved = self.record["boost_measured"]["effect"]
        for policy, values in head.items():
            self.assertEqual(values["max_perf"] - values["nominal_khz"], values["headroom"],
                             f"{policy}: headroom must be the difference it claims")
        self.assertEqual(head["policy0"]["headroom"], 0)
        self.assertEqual(head["policy5"]["headroom"], 0)
        self.assertGreater(head["policy10"]["headroom"], 0)
        self.assertEqual(head["policy19"]["headroom"],
                         moved["cpu_19"]["boost_khz"] - moved["cpu_19"]["before_khz"])

    def test_boost_is_recorded_as_a_measured_clock_and_an_unmeasured_workload(self):
        """The boost experiment moved clocks; it did not measure throughput.

        The record must keep that distinction: a clock that moved is an observation, and the
        effect on work is a separate question that this experiment did not ask.
        """
        boost = self.record["boost_measured"]
        self.assertIn("restored", boost["reversible"])
        self.assertIn("echo 1", boost["method"])
        effect = boost["effect"]
        self.assertTrue(effect["cpus_0_9_unchanged"], "half the CPUs did not move at all")
        self.assertGreater(effect["cpu_19"]["boost_khz"], effect["cpu_19"]["before_khz"])
        self.assertIn("work was not measured", " ".join(self.record["not_established"]))


if __name__ == "__main__":
    unittest.main()
