"""Regression coverage for path safety and whole-generation storage contracts."""

import hashlib
import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import networkx as nx
import numpy as np
import pandas as pd

from contracts import AnalysisResult
from storage import export_bundle, save_result, validate_bundle, validate_paths, write_graph_json
from validation import ValidationError


def result_fixture():
    gids = [100000003684369100 + i for i in range(21)]
    nodes = pd.DataFrame({"gid": gids, "depth": [0] + [1] * 20, "is_seed": [True] + [False] * 20})
    edges = pd.DataFrame(
        {
            "src": [gids[0]] * 20,
            "dst": gids[1:],
            "sum_kzt": [1.01] * 20,
            "n_tx": [1] * 20,
            "depth": [1] * 20,
        }
    )
    roles = pd.DataFrame(
        {
            "gid": gids,
            "role": ["peripheral"] * 21,
            "role_score": [0.5] * 21,
            "cluster_id": [0] * 21,
            "priority_score": [1 - i / 25 for i in range(21)],
            "evidence": ["fixture 1"] * 21,
            "pass_through": [np.nan] + [0.0] * 20,
            "role_facts": [json.dumps({"rule": "fallback", "count": 1})] * 21,
        }
    )
    clusters = pd.DataFrame(
        {
            "cluster_id": [0],
            "n_nodes": [21],
            "n_seed": [1],
            "sum_kzt_internal": [20.20],
            "top_gids": [json.dumps([str(gids[0])])],
            "hypothesis": ["Fixture"],
        }
    )
    top = roles.head(20)[["gid", "role", "priority_score"]].copy()
    top.insert(0, "rank", range(1, 21))
    top["why"] = "fixture 1"
    graph = nx.DiGraph()
    for row in nodes.itertuples(index=False):
        graph.add_node(row.gid, depth=row.depth, is_seed=row.is_seed)
    for row in edges.itertuples(index=False):
        graph.add_edge(
            row.src, row.dst, sum_kzt=row.sum_kzt, sum_tiyn=101, n_tx=row.n_tx, depth=row.depth
        )
    return AnalysisResult(graph, roles, clusters, top, nodes, edges)


class StorageAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.out = self.root / "out"
        self.result = result_fixture()

    def validate(self, path=None):
        return validate_bundle(path or self.out, self.result.nodes, self.result.edges)

    def test_paths_reject_data_overlap_source_parent_and_symlink_aliases(self):
        data, source = self.root / "data", self.root / "source"
        data.mkdir()
        source.mkdir()
        alias = self.root / "alias"
        alias.symlink_to(data, target_is_directory=True)
        for output in (data, self.root, data / "result", alias, alias / "result", source):
            with (
                self.subTest(output=output),
                self.assertRaisesRegex(ValidationError, "Unsafe paths"),
            ):
                validate_paths(data, output, source)
        validate_paths(data, source / "out", source)
        validate_paths(data, self.out, source)

    def test_lexical_data_symlink_inside_output_cannot_be_replaced(self):
        parent = self.root / "output-parent"
        parent.mkdir()
        external = self.root / "external"
        external.mkdir()
        data = parent / "input"
        data.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValidationError, "Unsafe paths"):
            validate_paths(data, parent, self.root / "source")

    def test_unknown_existing_directory_is_never_migrated(self):
        self.out.mkdir()
        marker = self.out / "nodes.parquet"
        marker.write_bytes(b"original input")
        with self.assertRaisesRegex(ValidationError, "unrecognized output"):
            save_result(self.result, self.out)
        self.assertFalse(self.out.is_symlink())
        self.assertEqual(marker.read_bytes(), b"original input")
        self.assertFalse(list(self.root.glob(".out-version-*")))

    def test_symlink_parent_dotdot_uses_filesystem_path_semantics(self):
        physical = self.root / "physical"
        nested = physical / "nested"
        nested.mkdir(parents=True)
        link = self.root / "link"
        link.symlink_to(nested, target_is_directory=True)
        requested = link / ".." / "out"
        save_result(self.result, requested)
        self.assertTrue((physical / "out").is_symlink())
        self.assertFalse(self.out.exists())
        self.validate(requested)

    def test_empty_existing_directory_is_not_recognized_as_result(self):
        self.out.mkdir()
        with self.assertRaisesRegex(ValidationError, "unrecognized output"):
            save_result(self.result, self.out)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_complete_roundtrip_and_portable_export(self):
        save_result(self.result, self.out)
        roles, clusters, top, payload, manifest = self.validate()
        self.assertEqual(len(roles), 21)
        self.assertEqual(manifest["schema_version"], 3)
        self.assertEqual(manifest["config"], self.result.config.to_dict())
        self.assertIsNone(payload["nodes"][0]["pass_through"])
        self.assertIsInstance(payload["nodes"][0]["role_facts"], dict)
        exported = self.root / "portable"
        export_bundle(self.out, exported)
        self.assertFalse(exported.is_symlink())
        self.validate(exported)
        self.assertEqual(
            {p.name: p.read_bytes() for p in self.out.iterdir()},
            {p.name: p.read_bytes() for p in exported.iterdir()},
        )

    def test_mismatched_graph_is_rejected_before_writing(self):
        for change in ("nodes", "edges", "amount", "count", "depth", "cents"):
            graph = self.result.graph.copy()
            edge = next(iter(graph.edges))
            with self.subTest(change=change):
                if change == "nodes":
                    graph.add_node(999)
                elif change == "edges":
                    graph.remove_edge(*edge)
                else:
                    key, value = {
                        "amount": ("sum_kzt", 1.02),
                        "count": ("n_tx", 2),
                        "depth": ("depth", 2),
                        "cents": ("sum_tiyn", 102),
                    }[change]
                    graph.edges[edge][key] = value
                with self.assertRaises(ValidationError):
                    save_result(replace(self.result, graph=graph), self.out)
                self.assertFalse(self.out.exists())

    def test_json_writer_cannot_publish_bad_endpoints_amounts_or_node_fields(self):
        def writer_with_change(change):
            def writer(graph, roles, path):
                write_graph_json(graph, roles, path)
                payload = json.loads(path.read_text())
                change(payload)
                path.write_text(json.dumps(payload))

            return writer

        changes = [
            lambda p: p["edges"][0].update(src="999"),
            lambda p: p["edges"][0].update(sum_kzt=1.02),
            lambda p: p["edges"][0].update(n_tx=99),
            lambda p: p["edges"][0].update(depth=2),
            lambda p: p["nodes"][0].update(role="coordinator"),
            lambda p: p["nodes"][0].update(cluster_id=99),
            lambda p: p["nodes"][0].update(role_score=0.7),
            lambda p: p["nodes"][0].update(pass_through=0),
            lambda p: p["nodes"][0].update(role_facts={"rule": "different", "count": 1}),
            lambda p: p["nodes"][0].update(gid=int(p["nodes"][0]["gid"])),
        ]
        save_result(self.result, self.out)
        original = self.out.resolve()
        for index, change in enumerate(changes):
            with self.subTest(change=index), self.assertRaises(ValidationError):
                save_result(self.result, self.out, graph_writer=writer_with_change(change))
            self.assertEqual(self.out.resolve(), original)
            self.validate()

    def test_reader_checks_json_contract_even_when_hashes_are_consistent(self):
        save_result(self.result, self.out)
        path = self.out / "graph.json"
        graph = json.loads(path.read_text())
        graph["nodes"][0]["priority_score"] = 0.01
        path.write_text(json.dumps(graph))
        manifest_path = self.out / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"]["graph.json"] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValidationError, "numeric mismatch"):
            self.validate()

    def test_manifest_rejects_tampered_bytes(self):
        save_result(self.result, self.out)
        with (self.out / "graph.json").open("a") as handle:
            handle.write("\n")
        with self.assertRaisesRegex(ValidationError, "checksum mismatch"):
            self.validate()

    def test_legacy_migration_reader_remains_on_its_open_directory(self):
        save_result(self.result, self.out)
        legacy = self.root / "legacy"
        shutil.copytree(self.out, legacy)
        updated_roles, updated_top = self.result.roles.copy(), self.result.top.copy()
        updated_roles["evidence"] = "fixture 2"
        updated_top["why"] = "fixture 2"
        updated = replace(self.result, roles=updated_roles, top=updated_top)
        original_read = pd.read_csv
        switched = False

        def switch_during_read(handle, *args, **kwargs):
            nonlocal switched
            if not switched:
                switched = True
                save_result(updated, legacy)
            return original_read(handle, *args, **kwargs)

        with patch("storage.pd.read_csv", side_effect=switch_during_read):
            old_roles, _, old_top, old_graph, _ = self.validate(legacy)
        self.assertTrue(legacy.is_symlink())
        self.assertEqual(set(old_roles.evidence), {"fixture 1"})
        self.assertEqual(set(old_top.why), {"fixture 1"})
        self.assertEqual({row["evidence"] for row in old_graph["nodes"]}, {"fixture 1"})
        new_roles, _, _, _, _ = self.validate(legacy)
        self.assertEqual(set(new_roles.evidence), {"fixture 2"})

    def test_new_input_dataset_can_replace_owned_generation(self):
        save_result(self.result, self.out)
        nodes = self.result.nodes.copy()
        edges = self.result.edges.copy()
        roles = self.result.roles.copy()
        top = self.result.top.copy()
        clusters = self.result.clusters.copy()
        remap = {gid: gid + 100 for gid in nodes.gid}
        for frame, cols in (
            (nodes, ("gid",)),
            (roles, ("gid",)),
            (top, ("gid",)),
            (edges, ("src", "dst")),
        ):
            for col in cols:
                frame[col] = frame[col].map(remap)
        clusters["top_gids"] = [json.dumps([str(nodes.gid.iloc[0])])]
        updated = replace(
            self.result,
            graph=nx.relabel_nodes(self.result.graph, remap),
            roles=roles,
            top=top,
            clusters=clusters,
            nodes=nodes,
            edges=edges,
        )
        save_result(updated, self.out)
        validate_bundle(self.out, nodes, edges)


if __name__ == "__main__":
    unittest.main()
