"""Regressions for shortest depths and explicit input profiles."""

import unittest

import pandas as pd

from hackalem.contracts import InputProfile
from hackalem.input_contracts import validate_depths, validate_profile
from hackalem.validation import ValidationError


def graph_tables(depths, seeds, pairs):
    nodes = pd.DataFrame(
        {
            "gid": list(depths),
            "depth": list(depths.values()),
            "is_seed": [gid in seeds for gid in depths],
        }
    )
    edges = pd.DataFrame(pairs, columns=["src", "dst"], dtype="int64")
    return edges, nodes


def transactions(dates, amounts):
    return pd.DataFrame({"date": pd.to_datetime(dates), "sum_kzt": amounts})


class DepthAuditTests(unittest.TestCase):
    def test_true_depth_two_cannot_be_changed_to_four(self):
        edges, nodes = graph_tables({1: 0, 2: 1, 3: 2}, {1}, [(1, 2), (2, 3)])
        validate_depths(edges, nodes)
        nodes.loc[nodes.gid.eq(3), "depth"] = 4
        with self.assertRaisesRegex(ValidationError, r"'gid': 3, 'declared': 4, 'actual': 2"):
            validate_depths(edges, nodes)

    def test_shortest_path_from_all_seeds_is_used(self):
        edges, nodes = graph_tables({1: 0, 2: 1, 3: 2, 4: 0}, {1, 4}, [(1, 2), (2, 3), (4, 3)])
        with self.assertRaisesRegex(ValidationError, r"'gid': 3, 'declared': 2, 'actual': 1"):
            validate_depths(edges, nodes)
        nodes.loc[nodes.gid.eq(3), "depth"] = 1
        validate_depths(edges.sample(frac=1, random_state=9), nodes.sample(frac=1, random_state=8))

    def test_unreachable_nonseed_is_rejected_including_reverse_only_path(self):
        for pairs in ([], [(2, 1)]):
            edges, nodes = graph_tables({1: 0, 2: 1}, {1}, pairs)
            with self.subTest(pairs=pairs), self.assertRaisesRegex(ValidationError, "unreachable"):
                validate_depths(edges, nodes)

    def test_isolated_seed_is_valid(self):
        edges, nodes = graph_tables({1: 0, 2: 1, 3: 0}, {1, 3}, [(1, 2)])
        validate_depths(edges, nodes)

    def test_configured_depth_limit_is_enforced(self):
        edges, nodes = graph_tables({1: 0, 2: 1, 3: 2}, {1}, [(1, 2), (2, 3)])
        validate_depths(edges, nodes, max_depth=2)
        with self.assertRaisesRegex(ValidationError, "maximum 1"):
            validate_depths(edges, nodes, max_depth=1)

    def test_cycles_and_self_transfers_do_not_change_shortest_depths(self):
        edges, nodes = graph_tables(
            {1: 0, 2: 1, 3: 2}, {1}, [(1, 1), (1, 2), (2, 3), (3, 2), (3, 1)]
        )
        validate_depths(edges, nodes)

    def test_edge_acquisition_depth_is_not_reinterpreted(self):
        edges, nodes = graph_tables({1: 0, 2: 1, 3: 2}, {1}, [(1, 2), (2, 3)])
        edges["depth"] = [4, 1]
        validate_depths(edges, nodes)


class ProfileAuditTests(unittest.TestCase):
    def setUp(self):
        self.case = InputProfile(
            name="july-2026",
            start_date="2026-07-01",
            end_date="2026-07-31",
            min_tx_tiyn=500_000,
            max_depth=4,
        )

    def test_generic_profile_accepts_synthetic_dates_and_one_tiyn(self):
        validate_profile(
            transactions(["2000-01-01", "2030-08-02"], [0.01, 10.0]), InputProfile(name="generic")
        )

    def test_case_includes_entire_first_and_last_calendar_days(self):
        tx = transactions(["2026-07-01 00:00:00", "2026-07-31 23:59:59"], [5000.0, 5000.01])
        validate_profile(tx, self.case)
        tx["date"] = tx.date.dt.tz_localize("Asia/Almaty")
        validate_profile(tx, self.case)

    def test_outside_case_dates_are_rejected(self):
        for day in ["2026-06-30", "2026-08-01"]:
            with (
                self.subTest(day=day),
                self.assertRaisesRegex(ValidationError, "transactions.date"),
            ):
                validate_profile(transactions([day], [5000.0]), self.case)

    def test_minimum_amount_uses_exact_tiyn(self):
        validate_profile(transactions(["2026-07-01"], [5000.0]), self.case)
        with self.assertRaisesRegex(ValidationError, "500000 tiyn"):
            validate_profile(transactions(["2026-07-01"], [4999.99]), self.case)

    def test_one_sided_date_bound(self):
        validate_profile(
            transactions(["2030-01-01"], [1.0]), InputProfile(name="since", start_date="2026-07-01")
        )
        validate_profile(
            transactions(["2000-01-01"], [1.0]), InputProfile(name="until", end_date="2026-07-31")
        )

    def test_invalid_transaction_date_is_rejected(self):
        tx = pd.DataFrame({"date": ["not-a-date"], "sum_kzt": [5000.0]})
        with self.assertRaisesRegex(ValidationError, "invalid dates"):
            validate_profile(tx, self.case)


if __name__ == "__main__":
    unittest.main()
