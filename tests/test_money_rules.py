"""Behavior at exact currency boundaries and self-transfer policy."""

import json
import unittest
from dataclasses import FrozenInstanceError
from fractions import Fraction

import networkx as nx
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from hackalem.contracts import AnalysisConfig, InputProfile
from hackalem.features import positive_percentile
from hackalem.pipeline import prepare_dataset, run_analysis
from hackalem.rules import ratio_ge, ratio_le

BASE = 100000003684369100


def dataset(rows):
    gids = [BASE + i for i in range(24)]
    tx = pd.DataFrame(rows, columns=["src", "dst", "day", "tiyn"])
    tx["src"] = tx.src.map(lambda value: gids[value]).astype("int64")
    tx["dst"] = tx.dst.map(lambda value: gids[value]).astype("int64")
    tx["date"] = pd.to_datetime(tx.day.map(lambda day: f"2026-07-{day:02d}"))
    tx["sum_kzt"] = tx.tiyn.map(lambda amount: int(amount) / 100)
    tx = tx[["src", "dst", "date", "sum_kzt"]]
    edges = tx.groupby(["src", "dst"], as_index=False).agg(
        sum_kzt=("sum_kzt", "sum"), n_tx=("sum_kzt", "size")
    )
    edges["sum_kzt"] = edges.sum_kzt.round(2)
    edges["depth"] = 1
    graph = nx.DiGraph()
    graph.add_nodes_from(gids)
    graph.add_edges_from(edges[["src", "dst"]].itertuples(index=False, name=None))
    # All origins are seeds; every other active node's true depth comes from BFS.
    seeds = {gid for gid in gids if graph.in_degree(gid) == 0}
    seeds.update(gid for gid in gids if set(graph.predecessors(gid)) == {gid})
    depths = {gid: 0 for gid in seeds}
    for seed in seeds:
        for gid, distance in nx.single_source_shortest_path_length(graph, seed).items():
            depths[gid] = min(depths.get(gid, distance), distance)
    nodes = pd.DataFrame(
        {
            "gid": gids,
            "depth": [depths[gid] for gid in gids],
            "is_seed": [gid in seeds for gid in gids],
        }
    )
    return prepare_dataset(edges, nodes, tx)


def subject(rows, index):
    return run_analysis(dataset(rows)).roles.set_index("gid").loc[BASE + index]


class ExactMoneyTests(unittest.TestCase):
    def test_reported_35_percent_boundary_plus_minus_one_tiyn(self):
        incoming = [(0, 3, 1, 500001), (1, 3, 1, 500002), (2, 3, 1, 500097)]
        for delta, expected in [(-1, "consolidator"), (0, "consolidator"), (1, "peripheral")]:
            with self.subTest(delta=delta):
                row = subject(incoming + [(3, 4, 2, 525035 + delta)], 3)
                self.assertEqual(row.role, expected)
                self.assertEqual(row.in_tiyn, 1500100)
                self.assertEqual(row.out_tiyn, 525035 + delta)

    def test_concentration_80_percent_boundary_plus_minus_one_tiyn(self):
        for delta, expected in [(-1, "consolidator"), (0, "consolidator"), (1, "peripheral")]:
            with self.subTest(delta=delta):
                row = subject(
                    [
                        (0, 3, 1, 800000 + delta),
                        (1, 3, 1, 100000 - delta),
                        (2, 3, 1, 100000),
                        (3, 4, 2, 100000),
                    ],
                    3,
                )
                self.assertEqual(row.role, expected)

    def test_distribution_150_percent_boundary_plus_minus_one_tiyn(self):
        for delta, expected in [(-1, "peripheral"), (0, "distributor"), (1, "distributor")]:
            with self.subTest(delta=delta):
                row = subject(
                    [
                        (0, 1, 1, 20002),
                        (1, 2, 2, 10001),
                        (1, 3, 2, 10001),
                        (1, 4, 2, 10001 + delta),
                    ],
                    1,
                )
                self.assertEqual(row.role, expected)

    def test_transit_65_and_135_percent_boundaries_plus_minus_one_tiyn(self):
        for outgoing, expected in [
            (6499, False),
            (6500, True),
            (6501, True),
            (13499, True),
            (13500, True),
            (13501, False),
        ]:
            with self.subTest(outgoing=outgoing):
                row = subject([(0, 1, 1, 10000), (1, 2, 2, outgoing)], 1)
                self.assertEqual(row.role == "transit", expected)

    def test_transit_half_coverage_boundary_is_exact(self):
        for delta, expected in [(-1, False), (0, True), (1, True)]:
            with self.subTest(delta=delta):
                row = subject(
                    [(0, 1, 1, 5000 + delta), (0, 1, 3, 5000 - delta), (1, 2, 2, 10000)], 1
                )
                self.assertEqual(row.role == "transit", expected)

    def test_huge_integer_thresholds_do_not_overflow_or_round(self):
        amount = np.int64(9_000_000_000_000_000_000)
        self.assertTrue(ratio_le(3150000000000000000, amount, Fraction(7, 20)))
        self.assertFalse(ratio_le(3150000000000000001, amount, Fraction(7, 20)))
        amount = 10**35
        self.assertTrue(ratio_ge(amount + 1, amount, Fraction(1)))
        self.assertFalse(ratio_le(amount + 1, amount, Fraction(1)))
        scores = positive_percentile(pd.Series([amount + 1, amount, 0], dtype=object))
        self.assertEqual(scores.tolist(), [1.0, 0.5, 0.0])

    def test_many_money_partitions_and_row_permutations_preserve_decision(self):
        rng = np.random.default_rng(112)
        for _ in range(12):
            # Three comparable amounts always remain below the 80% concentration threshold.
            a, b = int(rng.integers(50000, 55000)), int(rng.integers(50000, 55000))
            total = 160000
            prepared = dataset(
                [(0, 3, 1, a), (1, 3, 1, b), (2, 3, 1, total - a - b), (3, 4, 2, total * 7 // 20)]
            )
            first = run_analysis(prepared)
            shuffled = prepare_dataset(
                prepared.edges.sample(frac=1, random_state=3),
                prepared.nodes.sample(frac=1, random_state=4),
                prepared.transactions.sample(frac=1, random_state=5),
            )
            second = run_analysis(shuffled)
            self.assertEqual(first.roles.set_index("gid").loc[BASE + 3, "role"], "consolidator")
            assert_frame_equal(first.roles, second.roles, check_exact=True)

    def test_stale_derived_input_columns_are_overwritten(self):
        prepared = dataset([(0, 1, 1, 500000), (1, 2, 2, 500000)])
        prepared.edges["sum_tiyn"] = 1
        prepared.transactions["sum_tiyn"] = 1
        prepared.transactions["day"] = pd.Timestamp("1900-01-01").date()
        fixed = prepare_dataset(prepared.edges, prepared.nodes, prepared.transactions)
        self.assertEqual(fixed.edges.sum_tiyn.tolist(), [500000, 500000])
        self.assertEqual(fixed.transactions.day.iloc[0], pd.Timestamp("2026-07-01").date())


class PolicyAndConfigTests(unittest.TestCase):
    def test_self_only_client_gets_zero_network_priority(self):
        result = run_analysis(dataset([(0, 0, 1, 100000000)]))
        row = result.roles.set_index("gid").loc[BASE]
        self.assertEqual(row.n_peers, 0)
        self.assertEqual(row.priority_score, 0)
        self.assertEqual(row.turnover_tiyn, 0)
        self.assertEqual(row.self_transfer_tiyn, 100000000)
        self.assertEqual(result.clusters.sum_kzt_internal.sum(), 1000000)
        self.assertIn("самопереводов=1", row.evidence)
        self.assertTrue(result.graph.has_edge(BASE, BASE))

    def test_self_transfer_does_not_raise_external_priority_or_change_roles(self):
        rows = [(0, 1, 1, 100000), (1, 2, 2, 100000)]
        first = run_analysis(dataset(rows)).roles
        second = run_analysis(dataset(rows + [(1, 1, 2, 900000000)])).roles
        columns = [
            "gid",
            "role",
            "priority_score",
            "in_tiyn",
            "out_tiyn",
            "n_peers",
            "pagerank",
            "betweenness",
            "cluster_id",
        ]
        assert_frame_equal(first[columns], second[columns], check_exact=True)

    def test_optional_diagnostic_does_not_change_decisions_or_priority(self):
        prepared = dataset([(0, 1, 1, 100000), (1, 2, 2, 100000)])
        first = run_analysis(prepared).roles
        second = run_analysis(prepared, AnalysisConfig(diagnostics=True)).roles
        self.assertNotIn("temporal_out_share", first)
        self.assertIn("temporal_out_share", second)
        assert_frame_equal(first, second.drop(columns="temporal_out_share"), check_exact=True)
        facts = json.loads(first.role_facts.iloc[1])
        self.assertEqual(facts["in_tiyn"], 100000)

    def test_invalid_and_mutated_configs_are_rejected(self):
        for change in [
            dict(top_n=19),
            dict(temporal_window_days=0),
            dict(community_resolution=float("nan")),
            dict(priority_weights=(("turnover_tiyn", 1.0),)),
            dict(transit_min_out_ratio=Fraction(2)),
        ]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                AnalysisConfig(**change)
        with self.assertRaises(FrozenInstanceError):
            AnalysisConfig().top_n = 25
        with self.assertRaises(ValueError):
            InputProfile(min_tx_tiyn=True)
        json.dumps(AnalysisConfig().to_dict(), allow_nan=False)


if __name__ == "__main__":
    unittest.main()
