import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import run
from interface.baseline import analyze
from interface.build import build_payload, write_interface
from starter import load, build_graph, basic_features, write_templates
from validation import validate_output_files, ValidationError


class InterfacePipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.edges, cls.nodes, cls.tx = load(ROOT / "data")
        cls.graph = build_graph(cls.edges.sort_values(["src", "dst"]), cls.nodes.sort_values("gid"))
        cls.features = basic_features(cls.graph, cls.nodes)
        cls.roles, cls.clusters, cls.top = analyze(cls.graph, cls.features)

    def test_real_dataset_contract_and_top(self):
        from validation import validate_outputs
        validate_outputs(self.roles, self.clusters, self.top, self.nodes, self.edges)
        self.assertEqual(len(self.roles), len(self.nodes))
        self.assertEqual(len(self.top), 50)
        self.assertTrue(self.top.priority_score.is_monotonic_decreasing)
        self.assertEqual(len(self.roles.loc[self.roles.in_deg + self.roles.out_deg == 0]), 19)
        self.assertTrue(self.roles.loc[self.roles.truncated_by_depth, "role"].ne("terminal").all())

    def test_deterministic_after_input_row_shuffle(self):
        import pandas as pd
        graph = build_graph(self.edges.sample(frac=1, random_state=7).sort_values(["src", "dst"]),
                            self.nodes.sample(frac=1, random_state=7).sort_values("gid"))
        actual = analyze(graph, basic_features(graph, self.nodes.sample(frac=1, random_state=3)))
        for left, right in zip(actual, (self.roles, self.clusters, self.top)):
            pd.testing.assert_frame_equal(left, right)

    def test_build_and_team_csv_integration(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            out = Path(directory) / "baseline"
            payload = run.run(ROOT / "data", out)
            validate_output_files(out, self.nodes, self.edges)
            imported = run.run(ROOT / "data", Path(directory) / "team", out)
            self.assertEqual(imported["meta"]["analysis_source"], "team_csv")
            self.assertEqual(payload["top"], imported["top"])
            self.assertEqual({node["gid"] for node in payload["nodes"]}, set(self.nodes.gid.astype(str)))
            self.assertTrue(all(isinstance(edge["src"], str) and isinstance(edge["dst"], str) for edge in payload["edges"]))
            html = (out / "index.html").read_text()
            self.assertNotIn("__GRAPH_", html)
            self.assertNotIn('<script src=', html)
            self.assertIn('id="searchInput"', html)
            self.assertIn('id="network"', html)
            self.assertEqual(json.loads((out / "graph.json").read_text()), payload)

    def test_blank_templates_cannot_be_imported(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            source = Path(directory) / "templates"
            write_templates(self.features, source)
            with self.assertRaises(ValidationError):
                run.run(ROOT / "data", Path(directory) / "out", source)

    def test_analyst_text_cannot_close_embedded_script(self):
        roles = self.roles.copy()
        roles.loc[0, "evidence"] = '</script><script>window.injected=1</script>'
        payload = build_payload(roles, self.clusters, self.top, self.features, self.edges, self.tx, "team_csv")
        with tempfile.TemporaryDirectory() as directory:
            write_interface(payload, directory)
            html = (Path(directory) / "index.html").read_text()
            self.assertNotIn('</script><script>window.injected', html)
            self.assertIn('\\u003c/script\\u003e', html)

    def test_top_cannot_be_smaller_than_twenty(self):
        with self.assertRaisesRegex(ValueError, "не меньше 20"):
            analyze(self.graph, self.features, top_count=19)


if __name__ == "__main__":
    unittest.main()
