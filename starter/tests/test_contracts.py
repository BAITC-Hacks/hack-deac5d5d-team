import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

from starter import (basic_features, betweenness_features, build_graph,
                     write_graph_json, write_outputs, write_templates)
from validation import ValidationError, validate_inputs, validate_outputs, validate_output_files


def input_fixture():
    gids = [100000003684369100 + i for i in range(3)]
    nodes = pd.DataFrame({"gid": gids, "depth": [0, 1, 0], "is_seed": [True, False, True]})
    edges = pd.DataFrame({"src": [gids[0]], "dst": [gids[1]], "sum_kzt": [10000000.03],
                          "n_tx": [2], "depth": [1]})
    tx = pd.DataFrame({"src": [gids[0]] * 2, "dst": [gids[1]] * 2,
                       "date": pd.to_datetime(["2026-07-01", "2026-07-02"]),
                       "sum_kzt": [10000000.01, .02]})
    return edges, nodes, tx


def output_fixture():
    gids = [100000003684369100 + i for i in range(21)]
    nodes = pd.DataFrame({"gid": gids, "depth": [0] + [1] * 20,
                          "is_seed": [True] + [False] * 20})
    # Two directions in one cluster: both amounts must contribute to internal turnover.
    edges = pd.DataFrame({"src": [gids[0], gids[1]], "dst": [gids[1], gids[0]],
                          "sum_kzt": [10.01, 20.02]})
    roles = pd.DataFrame({"gid": gids, "role": ["peripheral"] * 21,
                          "role_score": [.5] * 21, "cluster_id": [0] * 21,
                          "priority_score": [1 - i / 25 for i in range(21)],
                          "evidence": ["in_deg=1; fixture only"] * 21})
    clusters = pd.DataFrame({"cluster_id": [0], "n_nodes": [21], "n_seed": [1],
                             "sum_kzt_internal": [30.03],
                             "top_gids": [json.dumps([str(gids[0])])], "hypothesis": ["Test fixture"]})
    top = roles.head(20)[["gid", "role", "priority_score"]].copy()
    top.insert(0, "rank", range(1, 21))
    top["why"] = "in_deg=1; fixture only"
    return roles, clusters, top, nodes, edges


class InputTests(unittest.TestCase):
    def test_exact_money_and_isolates(self):
        e, n, t = input_fixture()
        self.assertEqual(validate_inputs(e, n, t), {n.gid.iloc[2]})

    def test_one_tiyn_mismatch_at_large_amount_is_rejected(self):
        e, n, t = input_fixture()
        e.loc[0, "sum_kzt"] = 10000000.04
        with self.assertRaisesRegex(ValidationError, "amount mismatch"):
            validate_inputs(e, n, t)

    def test_count_mismatch(self):
        e, n, t = input_fixture()
        e.loc[0, "n_tx"] = 3
        with self.assertRaisesRegex(ValidationError, "count mismatch"):
            validate_inputs(e, n, t)

    def test_pair_mismatch(self):
        e, n, t = input_fixture()
        t.loc[0, "dst"] = n.gid.iloc[2]
        with self.assertRaisesRegex(ValidationError, "pair mismatch"):
            validate_inputs(e, n, t)

    def test_unknown_endpoint(self):
        e, n, t = input_fixture()
        e.loc[0, "src"] = 999
        with self.assertRaisesRegex(ValidationError, "unknown client"):
            validate_inputs(e, n, t)

    def test_duplicate_edges_and_nodes(self):
        e, n, t = input_fixture()
        for edges, nodes in ((pd.concat([e, e]), n), (e, pd.concat([n, n.iloc[:1]]))):
            with self.subTest(edges=len(edges), nodes=len(nodes)), self.assertRaisesRegex(ValidationError, "duplicate"):
                validate_inputs(edges, nodes, t)

    def test_bad_numeric_values(self):
        for bad in [np.nan, np.inf, -1., 0., 1.001]:
            e, n, t = input_fixture()
            t.loc[0, "sum_kzt"] = bad
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                validate_inputs(e, n, t)

    def test_float_ids_are_not_silently_repaired(self):
        e, n, t = input_fixture()
        n["gid"] = n.gid.astype(float)
        with self.assertRaisesRegex(ValidationError, "expected integers"):
            validate_inputs(e, n, t)

    def test_duplicate_transactions_remain_valid(self):
        e, n, t = input_fixture()
        t = pd.concat([t.iloc[:1], t.iloc[:1]], ignore_index=True)
        e["sum_kzt"] = 20000000.02
        validate_inputs(e, n, t)


class GraphTests(unittest.TestCase):
    def test_isolate_participates_in_pagerank(self):
        e, n, _ = input_fixture()
        g = build_graph(e, n)
        f = basic_features(g, n).set_index("gid")
        self.assertEqual(set(g), set(n.gid))
        self.assertEqual(nx.number_weakly_connected_components(g), 2)
        isolated = n.gid.iloc[2]
        self.assertEqual(f.loc[isolated, "in_deg"], 0)
        self.assertEqual(f.loc[isolated, "out_deg"], 0)
        self.assertGreater(f.loc[isolated, "pagerank"], 0)
        self.assertAlmostEqual(f.pagerank.sum(), 1)

    def test_json_preserves_all_digits_and_uses_null(self):
        e, n, _ = input_fixture()
        g = build_graph(e, n)
        f = basic_features(g, n)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "graph.json"
            write_graph_json(g, f, path)
            raw = path.read_text()
            data = json.loads(raw)
        self.assertNotIn("NaN", raw)
        self.assertEqual({row["gid"] for row in data["nodes"]}, {str(v) for v in n.gid})
        self.assertTrue(all(isinstance(row["gid"], str) for row in data["nodes"]))
        for row in data["edges"]:
            self.assertEqual(row["src"], str(e.src.iloc[0]))
            self.assertEqual(row["dst"], str(e.dst.iloc[0]))
        self.assertIsNone(data["nodes"][2]["pass_through"])

    def test_distance_semantics(self):
        g = nx.DiGraph()
        g.add_edge("a", "b", sum_kzt=100.)
        g.add_edge("b", "c", sum_kzt=100.)
        g.add_edge("a", "c", sum_kzt=1.)
        self.assertEqual(betweenness_features(g, "hops")["b"], 0)
        self.assertGreater(betweenness_features(g, "inverse_amount")["b"], 0)
        self.assertNotIn("distance", g["a"]["b"])
        with self.assertRaises(ValueError):
            betweenness_features(g, "sum_kzt")
        g["a"]["b"]["sum_kzt"] = 0
        with self.assertRaises(ValidationError):
            betweenness_features(g, "inverse_amount")


class OutputTests(unittest.TestCase):
    def test_valid_submission_roundtrip(self):
        r, c, t, n, e = output_fixture()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            write_outputs(r, c, t, n, e, Path(tmp))
            validate_output_files(Path(tmp), n, e)
            loaded = pd.read_csv(Path(tmp) / "nodes_roles.csv", dtype={"gid": "int64"})
            self.assertEqual(loaded.gid.tolist(), n.gid.tolist())

    def test_required_columns(self):
        r, c, t, n, e = output_fixture()
        with self.assertRaisesRegex(ValidationError, "missing columns"):
            validate_outputs(r.drop(columns="role"), c, t, n, e)

    def test_node_coverage_and_uniqueness(self):
        r, c, t, n, e = output_fixture()
        for bad in [r.iloc[:-1], pd.concat([r, r.iloc[:1]])]:
            with self.subTest(size=len(bad)), self.assertRaisesRegex(ValidationError, "every input"):
                validate_outputs(bad, c, t, n, e)

    def test_invalid_roles_scores_and_evidence(self):
        for col, value in [("role", "unknown"), ("role_score", -1.), ("role_score", np.inf),
                           ("priority_score", 1.01), ("evidence", ""), ("evidence", "high score"),
                           ("evidence", "1" * 201)]:
            r, c, t, n, e = output_fixture()
            r.loc[0, col] = value
            with self.subTest(col=col, value=value), self.assertRaises(ValidationError):
                validate_outputs(r, c, t, n, e)

    def test_cluster_membership_statistics_and_top_ids(self):
        for col, value in [("cluster_id", 7), ("n_nodes", 20), ("n_seed", 0),
                           ("sum_kzt_internal", 30.04), ("top_gids", '["999"]'),
                           ("top_gids", '[100000003684369100]'), ("hypothesis", " ")]:
            r, c, t, n, e = output_fixture()
            c.loc[0, col] = value
            with self.subTest(col=col), self.assertRaises(ValidationError):
                validate_outputs(r, c, t, n, e)

    def test_duplicate_clusters(self):
        r, c, t, n, e = output_fixture()
        with self.assertRaisesRegex(ValidationError, "duplicate cluster"):
            validate_outputs(r, pd.concat([c, c]), t, n, e)

    def test_top_count_ranks_roles_scores_and_explanations(self):
        for col, value in [("rank", 2), ("gid", 999), ("role", "transit"),
                           ("priority_score", .9), ("why", " ")]:
            r, c, t, n, e = output_fixture()
            t.loc[0, col] = value
            with self.subTest(col=col), self.assertRaises(ValidationError):
                validate_outputs(r, c, t, n, e)
        r, c, t, n, e = output_fixture()
        with self.assertRaisesRegex(ValidationError, "at least 20"):
            validate_outputs(r, c, t.iloc[:19], n, e)

    def test_ranked_top_cannot_skip_a_higher_priority_node(self):
        r, c, t, n, e = output_fixture()
        t = r.iloc[1:][["gid", "role", "priority_score"]].copy()
        t.insert(0, "rank", range(1, 21))
        t["why"] = "in_deg=1"
        with self.assertRaisesRegex(ValidationError, "higher-priority"):
            validate_outputs(r, c, t, n, e)

    def test_top_must_be_sorted(self):
        r, c, t, n, e = output_fixture()
        t = t.iloc[::-1].reset_index(drop=True)
        t["rank"] = range(1, 21)
        with self.assertRaisesRegex(ValidationError, "priorities must descend"):
            validate_outputs(r, c, t, n, e)

    def test_tied_scores_can_have_any_order(self):
        r, c, t, n, e = output_fixture()
        r["priority_score"] = .5
        t = t.iloc[::-1].reset_index(drop=True)
        t["rank"] = range(1, 21)
        t["priority_score"] = .5
        validate_outputs(r, c, t, n, e)

    def test_invalid_submission_does_not_overwrite_existing_files(self):
        r, c, t, n, e = output_fixture()
        r.loc[0, "evidence"] = ""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nodes_roles.csv"
            path.write_text("existing result")
            with self.assertRaises(ValidationError):
                write_outputs(r, c, t, n, e, Path(tmp))
            self.assertEqual(path.read_text(), "existing result")

    def test_templates_are_rejected(self):
        e, n, _ = input_fixture()
        f = basic_features(build_graph(e, n), n)
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            write_templates(f, Path(tmp))
            with self.assertRaises(ValidationError):
                validate_output_files(Path(tmp), n, e)

    def test_csv_float_or_exponent_ids_are_rejected(self):
        r, c, t, n, e = output_fixture()
        for bad in ["1.000000036843691e17", "100000003684369100.0"]:
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                r.to_csv(Path(tmp) / "nodes_roles.csv", index=False)
                c.to_csv(Path(tmp) / "clusters.csv", index=False)
                t.to_csv(Path(tmp) / "top_nodes.csv", index=False)
                path = Path(tmp) / "nodes_roles.csv"
                path.write_text(path.read_text().replace(str(n.gid.iloc[0]), bad))
                with self.assertRaisesRegex(ValidationError, "exact decimal integer"):
                    validate_output_files(Path(tmp), n, e)


if __name__ == "__main__":
    unittest.main()
