import unittest
from pathlib import Path
from unittest.mock import Mock

import networkx as nx
import pandas as pd

from communities import assign_clusters, undirected_projection
from contracts import AnalysisConfig
from validation import ValidationError


class CommunityTests(unittest.TestCase):
    def graph(self):
        graph = nx.DiGraph()
        graph.add_nodes_from([5, 4, 3, 2, 1, 0])
        graph.add_edge(1, 2, sum_kzt=12.01, sum_tiyn=1201)
        graph.add_edge(2, 1, sum_kzt=3.02, sum_tiyn=302)
        graph.add_edge(3, 4, sum_kzt=40.0, sum_tiyn=4000)
        graph.add_edge(4, 5, sum_kzt=5.0, sum_tiyn=500)
        return graph

    def test_projection_sums_directions_exactly_and_excludes_loops(self):
        graph = self.graph()
        graph.add_edge(0, 0, sum_kzt=1000000.0, sum_tiyn=100000000)
        projected = undirected_projection(graph)
        self.assertEqual(sorted(projected), list(range(6)))
        self.assertEqual(projected.number_of_edges(), 3)
        self.assertEqual(projected[1][2]["sum_tiyn"], 1503)
        self.assertAlmostEqual(projected[1][2]["sum_kzt"], 15.03)
        self.assertEqual(projected.degree(0), 0)

    def test_forced_disconnected_community_is_split_before_ids_assigned(self):
        graph = self.graph()
        strategy = Mock(return_value=[{5, 4, 3, 2, 1}])
        config = AnalysisConfig(random_seed=7, community_resolution=0.8)
        result = assign_clusters(graph, config, strategy)
        self.assertEqual(result, {0: 0, 1: 1, 2: 1, 3: 2, 4: 2, 5: 2})
        passed_graph, passed_config = strategy.call_args.args
        self.assertEqual(set(passed_graph), {1, 2, 3, 4, 5})
        self.assertIs(passed_config, config)
        self.assertTrue(
            all(
                nx.is_connected(
                    undirected_projection(graph).subgraph(
                        [gid for gid, group in result.items() if group == cluster]
                    )
                )
                for cluster in set(result.values())
            )
        )

    def test_partition_order_does_not_change_ids(self):
        graph = self.graph()
        first = assign_clusters(graph, strategy=lambda *_: [{3, 4, 5}, {1, 2}])
        second = assign_clusters(graph, strategy=lambda *_: [{2, 1}, {5, 3, 4}])
        self.assertEqual(first, second)

    def test_invalid_strategies_are_rejected(self):
        for proposed, message in [
            ([{1, 2}, {2, 3, 4, 5}], "duplicate"),
            ([[1, 1, 2], [3, 4, 5]], "duplicate"),
            ([{1, 2, 3, 4, 5, 999}], "unknown"),
            ([{0, 1, 2, 3, 4, 5}], "isolated"),
            ([{1, 2}], "incomplete"),
            ([set(), {1, 2, 3, 4, 5}], "empty"),
        ]:
            with self.subTest(proposed=proposed):
                with self.assertRaisesRegex(ValidationError, message):
                    assign_clusters(self.graph(), strategy=lambda *_: proposed)

    def test_empty_and_self_transfer_only_graphs_skip_strategy(self):
        strategy = Mock(side_effect=AssertionError("strategy must not run"))
        self.assertEqual(assign_clusters(nx.DiGraph(), strategy=strategy), {})
        graph = nx.DiGraph()
        graph.add_nodes_from([8, 2])
        graph.add_edge(8, 8, sum_kzt=1000000.0, sum_tiyn=100000000)
        self.assertEqual(assign_clusters(graph, strategy=strategy), {2: 0, 8: 1})

    def test_generated_partitions_have_complete_connected_coverage(self):
        # Seeded graph families and deliberately coarse strategy test splitting,
        # independent of Louvain implementation details.
        for seed in range(12):
            random = nx.gnp_random_graph(30, 0.05, seed=seed, directed=True)
            nx.set_edge_attributes(random, 1.0, "sum_kzt")
            result = assign_clusters(random, strategy=lambda active, _: [list(active)])
            self.assertEqual(set(result), set(random))
            projection = undirected_projection(random)
            groups = [
                {gid for gid, cid in result.items() if cid == value}
                for value in set(result.values())
            ]
            self.assertEqual(
                sorted(map(len, groups)), sorted(map(len, nx.connected_components(projection)))
            )

    def test_real_data_communities_are_all_connected(self):
        data = Path(__file__).resolve().parents[2] / "data"
        if not (data / "edges.parquet").exists():
            self.skipTest("real dataset is not distributed with this checkout")
        nodes = pd.read_parquet(data / "nodes.parquet")
        edges = pd.read_parquet(data / "edges.parquet")
        graph = nx.DiGraph()
        graph.add_nodes_from(sorted(nodes.gid))
        graph.add_edges_from(
            (row.src, row.dst, {"sum_kzt": row.sum_kzt})
            for row in edges.sort_values(["src", "dst"]).itertuples(index=False)
        )
        result = assign_clusters(graph)
        projection = undirected_projection(graph)
        self.assertEqual(set(result), set(nodes.gid))
        self.assertTrue(
            all(
                nx.is_connected(
                    projection.subgraph([gid for gid, group in result.items() if group == cluster])
                )
                for cluster in set(result.values())
            )
        )


if __name__ == "__main__":
    unittest.main()
