import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import networkx as nx
import pandas as pd
from pandas.testing import assert_frame_equal

from analysis import (analyze, analysis_features, assign_clusters, assign_roles_and_priorities,
                      temporal_support, undirected_projection)
from starter import basic_features, build_graph
from validation import ValidationError, validate_inputs, validate_output_files, validate_outputs


BASE = 100000003684369100


def fixture():
    """Independent collection, distribution, dated transit and clipped motifs."""
    gids = [BASE + i for i in range(24)]
    depths = [0] * 24
    depths[4], depths[11], depths[12], depths[13] = 1, 1, 2, 4
    nodes = pd.DataFrame({"gid": gids, "depth": depths,
                          "is_seed": [depth == 0 for depth in depths]})
    transfers = [(0, 4, 100.01, 1), (1, 4, 100.01, 1), (2, 4, 100.01, 1),
                 (5, 6, 30.01, 1), (5, 7, 30.01, 1), (5, 8, 30.01, 1),
                 (10, 11, 90.02, 1), (11, 12, 90.02, 2),
                 (14, 13, 500.03, 1), (15, 13, 500.03, 1), (16, 13, 500.03, 1)]
    tx = pd.DataFrame({"src": [gids[src] for src, _, _, _ in transfers],
                       "dst": [gids[dst] for _, dst, _, _ in transfers],
                       "date": pd.to_datetime([f"2026-07-{day:02d}" for _, _, _, day in transfers]),
                       "sum_kzt": [amount for _, _, amount, _ in transfers]})
    edges = tx.groupby(["src", "dst"], as_index=False).agg(sum_kzt=("sum_kzt", "sum"), n_tx=("sum_kzt", "size"))
    edges["depth"] = 1
    return edges, nodes, tx


def run_analysis(edges, nodes, tx):
    validate_inputs(edges, nodes, tx)
    graph = build_graph(edges, nodes)
    outputs = analyze(graph, basic_features(graph, nodes), edges, tx)
    validate_outputs(*outputs, nodes, edges)
    return outputs


class AnalysisTests(unittest.TestCase):
    def test_motifs_and_incomplete_observations(self):
        e, n, t = fixture()
        roles, clusters, top = run_analysis(e, n, t)
        indexed = roles.set_index("gid")
        for node, expected in [(4, "consolidator"), (5, "distributor"), (11, "transit"), (12, "terminal")]:
            self.assertEqual(indexed.loc[BASE + node, "role"], expected)
        clipped = indexed.loc[BASE + 13]
        self.assertEqual(clipped.role, "peripheral")
        self.assertLessEqual(clipped.role_score, .2)
        self.assertIn("обрезан", clipped.evidence)
        self.assertGreater(clipped.priority_score, 0)
        isolated = indexed.loc[BASE + 23]
        self.assertEqual(isolated.role_score, 0)
        self.assertEqual(isolated.priority_score, 0)
        self.assertGreater(isolated.pagerank, 0)
        self.assertIn("данных", isolated.evidence)
        self.assertEqual(len(top), 20)
        self.assertEqual(int(clusters.n_nodes.sum()), len(n))

    def test_incoming_outgoing_sum_and_exact_internal_turnover(self):
        e, n, t = fixture()
        reverse = t.iloc[:1].copy()
        reverse[["src", "dst"]] = reverse[["dst", "src"]].to_numpy()
        reverse["sum_kzt"] = .02
        t = pd.concat([t, reverse], ignore_index=True)
        e = t.groupby(["src", "dst"], as_index=False).agg(sum_kzt=("sum_kzt", "sum"), n_tx=("sum_kzt", "size"))
        e["depth"] = 1
        graph = build_graph(e, n)
        self.assertAlmostEqual(undirected_projection(graph)[BASE][BASE + 4]["sum_kzt"], 100.03)
        r, c, _ = run_analysis(e, n, t)
        cid = r.set_index("gid").loc[BASE + 4, "cluster_id"]
        self.assertEqual(c.set_index("cluster_id").loc[cid, "sum_kzt_internal"], 300.05)

    def test_dates_require_previous_day_within_seven_days(self):
        e, n, tx = fixture()
        for day, expected in [(1, 0.), (2, 1.), (8, 1.), (9, 0.)]:
            t = tx.copy()
            t.loc[t.src.eq(BASE + 11), "date"] = pd.Timestamp(f"2026-07-{day:02d}")
            with self.subTest(day=day):
                self.assertEqual(temporal_support(t)[BASE + 11], expected)
                roles, _, _ = run_analysis(e, n, t)
                role = roles.set_index("gid").loc[BASE + 11, "role"]
                self.assertEqual(role == "transit", expected == 1.)
        tx.loc[tx.dst.eq(BASE + 11), "date"] = pd.Timestamp("2026-07-10")
        self.assertEqual(temporal_support(tx)[BASE + 11], 0.)

    def test_temporal_share_is_amount_weighted_and_excludes_self_transfers(self):
        tx = pd.DataFrame({"src": [1, 2, 2, 2], "dst": [2, 3, 4, 2],
                           "date": pd.to_datetime(["2026-07-02", "2026-07-01", "2026-07-03", "2026-07-02"]),
                           "sum_kzt": [100., 75., 25., 1000.]})
        self.assertEqual(temporal_support(tx)[2], .25)

    def test_shuffled_inputs_are_identical_and_ids_are_exact(self):
        e, n, t = fixture()
        first = run_analysis(e, n, t)
        second = run_analysis(e.sample(frac=1, random_state=5), n.sample(frac=1, random_state=6),
                              t.sample(frac=1, random_state=7))
        for left, right in zip(first, second):
            assert_frame_equal(left, right, check_exact=True)
        self.assertEqual(set(first[0].gid), set(n.gid))
        for gids in first[1].top_gids:
            self.assertTrue(all(isinstance(gid, str) for gid in json.loads(gids)))

    def test_all_isolates_and_tied_priorities(self):
        e, n, t = fixture()
        e, t = e.iloc[:0].copy(), t.iloc[:0].copy()
        r, c, top = run_analysis(e, n, t)
        self.assertTrue(r.priority_score.eq(0).all())
        self.assertTrue(c.n_nodes.eq(1).all())
        self.assertTrue(c.sum_kzt_internal.eq(0).all())
        self.assertEqual(top.gid.tolist(), sorted(n.gid)[:20])

    def test_too_few_clients_fail_explicitly(self):
        e, n, t = fixture()
        e, t, n = e.iloc[:0].copy(), t.iloc[:0].copy(), n.iloc[:19].copy()
        graph = build_graph(e, n)
        with self.assertRaisesRegex(ValidationError, "минимум 20"):
            analyze(graph, basic_features(graph, n), e, t)

    def test_coordinator_bridges_strong_communities(self):
        # Three strongly connected groups, joined through a single bidirectional hub.
        graph = nx.DiGraph()
        for start in [1, 6, 11]:
            for src in range(start, start + 5):
                for dst in range(start, start + 5):
                    if src != dst:
                        graph.add_edge(src, dst, sum_kzt=100., n_tx=1, depth=1)
            for peer in [start, start + 1]:
                graph.add_edge(0, peer, sum_kzt=1., n_tx=1, depth=1)
                graph.add_edge(peer, 0, sum_kzt=1., n_tx=1, depth=1)
        nodes = pd.DataFrame({"gid": sorted(graph), "depth": 0, "is_seed": True})
        tx = pd.DataFrame([{"src": src, "dst": dst, "date": pd.Timestamp("2026-07-01"),
                            "sum_kzt": attrs["sum_kzt"]} for src, dst, attrs in graph.edges(data=True)])
        features = analysis_features(graph, basic_features(graph, nodes), tx, assign_clusters(graph))
        roles = assign_roles_and_priorities(features).set_index("gid")
        self.assertEqual(roles.loc[0, "role"], "coordinator")
        self.assertEqual(roles.betweenness.idxmax(), 0)

    def test_priority_does_not_depend_on_role_confidence(self):
        e, n, t = fixture()
        roles_before, _, _ = run_analysis(e, n, t)
        t.loc[t.src.eq(BASE + 11), "date"] = pd.Timestamp("2026-07-01")
        roles_after, _, _ = run_analysis(e, n, t)
        self.assertFalse(roles_before.role.equals(roles_after.role))
        self.assertFalse(roles_before.role_score.equals(roles_after.role_score))
        self.assertTrue(roles_before.priority_score.equals(roles_after.priority_score))

    def test_cli_writes_validated_outputs_and_json_then_validation_is_read_only(self):
        e, n, t = fixture()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, frame in [("edges", e), ("nodes", n), ("transactions", t)]:
                frame.to_parquet(root / f"{name}.parquet", index=False)
            command = [sys.executable, "-B", str(Path(__file__).resolve().parents[1] / "starter.py"), "--data", str(root)]
            out = root / "output"
            run = subprocess.run(command + ["--out", str(out)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            validate_output_files(out, n, e)
            snapshot = {p.name: p.read_bytes() for p in out.iterdir()}
            graph = json.loads(snapshot["graph.json"])
            self.assertEqual(graph["analysis_status"], "complete")
            self.assertEqual({r["gid"] for r in graph["nodes"]}, {str(gid) for gid in n.gid})
            self.assertTrue(all(r["role"] for r in graph["nodes"]))
            check = subprocess.run(command + ["--validate-output", str(out)], capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertEqual(snapshot, {p.name: p.read_bytes() for p in out.iterdir()})
            # Broken inputs must fail before overwriting a previously valid submission.
            e.loc[0, "sum_kzt"] = round(e.loc[0, "sum_kzt"] + .01, 2)
            e.to_parquet(root / "edges.parquet", index=False)
            bad = subprocess.run(command + ["--out", str(out)], capture_output=True, text=True)
            self.assertEqual(bad.returncode, 1)
            self.assertIn("amount mismatch", bad.stderr)
            self.assertEqual(snapshot, {p.name: p.read_bytes() for p in out.iterdir()})


if __name__ == "__main__":
    unittest.main()
