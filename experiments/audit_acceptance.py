#!/usr/bin/env python3
"""Repeat the reported audit scenarios; all experimental writes are temporary.

Reads the specified Parquet dataset and source files. Only the JSON report
(default: reports/audit-after.json) is written outside a TemporaryDirectory. Exit status is
zero only when every acceptance condition passes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import networkx as nx
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hackalem.communities import undirected_projection
from hackalem.contracts import AnalysisResult
from hackalem.pipeline import load_dataset, prepare_dataset, run_analysis
from hackalem.storage import save_result, validate_bundle, write_graph_json
from hackalem.validation import ValidationError

PROJECT = Path(__file__).resolve().parents[1]


def fixture() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    base = 100000003684369100
    gids = [base + i for i in range(24)]
    depths = [0] * 24
    depths[3], depths[4] = 1, 2
    nodes = pd.DataFrame(
        {"gid": gids, "depth": depths, "is_seed": [depth == 0 for depth in depths]}
    )
    tx = pd.DataFrame(
        {
            "src": [gids[i] for i in [0, 1, 2, 3]],
            "dst": [gids[i] for i in [3, 3, 3, 4]],
            "date": pd.to_datetime(["2026-07-01"] * 3 + ["2026-07-02"]),
            "sum_kzt": [5000.01, 5000.02, 5000.97, 5250.35],
        }
    )
    edges = tx.groupby(["src", "dst"], as_index=False).agg(
        sum_kzt=("sum_kzt", "sum"), n_tx=("sum_kzt", "size")
    )
    edges["depth"] = [1, 1, 1, 2]
    return edges, nodes, tx


def file_hashes(directory: Path) -> dict[str, str]:
    return {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
        for name in ("edges.parquet", "nodes.parquet", "transactions.parquet")
    }


def rejection(action) -> dict:
    try:
        action()
    except ValidationError as exc:
        return {"rejected": True, "error": str(exc)}
    return {"rejected": False, "error": None}


def path_probes(root: Path, edges, nodes, tx) -> dict:
    report = {}
    for scenario in ("same", "ancestor", "symlink_alias"):
        case = root / scenario
        data = case / "input"
        data.mkdir(parents=True)
        for name, frame in (("edges", edges), ("nodes", nodes), ("transactions", tx)):
            frame.to_parquet(data / f"{name}.parquet", index=False)
        before = file_hashes(data)
        output = data if scenario == "same" else case
        if scenario == "symlink_alias":
            output = case / "alias"
            output.symlink_to(data, target_is_directory=True)
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(PROJECT / "starter" / "starter.py"),
                "--data",
                str(data),
                "--out",
                str(output),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        unchanged = data.is_dir() and not data.is_symlink() and file_hashes(data) == before
        report[scenario] = {
            "passed": completed.returncode != 0 and unchanged,
            "exit_code": completed.returncode,
            "error": completed.stderr.strip(),
            "input_is_symlink": data.is_symlink(),
            "input_sha256_unchanged": unchanged,
            "input_sha256": before,
        }
    return {"passed": all(row["passed"] for row in report.values()), "cases": report}


def graph_probes(root: Path, result: AnalysisResult) -> dict:
    bad_graph = result.graph.copy()
    bad_graph.add_edge(999, 998, sum_kzt=5000.0, sum_tiyn=500000, n_tx=1, depth=1)
    before = rejection(lambda: save_result(replace(result, graph=bad_graph), root / "bad-graph"))

    def inconsistent_writer(graph, roles, path):
        write_graph_json(graph, roles, path)
        payload = json.loads(path.read_text())
        payload["edges"].append(
            {"src": "999", "dst": "998", "sum_kzt": 5000.0, "n_tx": 1, "depth": 1}
        )
        path.write_text(json.dumps(payload), encoding="utf-8")

    after = rejection(
        lambda: save_result(result, root / "bad-json", graph_writer=inconsistent_writer)
    )
    published = (root / "bad-graph").exists() or (root / "bad-json").exists()
    return {
        "passed": before["rejected"] and after["rejected"] and not published,
        "inconsistent_input_graph": before,
        "inconsistent_serialized_json": after,
        "any_invalid_result_published": published,
    }


def legacy_reader_probe(root: Path, result: AnalysisResult) -> dict:
    versioned = root / "versioned"
    save_result(result, versioned)
    legacy = root / "legacy"
    shutil.copytree(versioned.resolve(), legacy)
    previous_manifest = json.loads((legacy / "manifest.json").read_text())
    new_roles, new_top = result.roles.copy(), result.top.copy()
    new_roles["priority_score"] = (new_roles.priority_score * 0.9).round(6)
    new_top["priority_score"] = (new_top.priority_score * 0.9).round(6)
    updated = replace(result, roles=new_roles, top=new_top)
    original_read = pd.read_csv
    switched = False

    def interleave(handle, *args, **kwargs):
        nonlocal switched
        frame = original_read(handle, *args, **kwargs)
        if not switched:
            # Publish after the old nodes_roles.csv is parsed, before the other
            # two CSVs, JSON and manifest are opened by the outer reader.
            switched = True
            save_result(updated, legacy)
        return frame

    with patch("hackalem.storage.pd.read_csv", side_effect=interleave):
        old_roles, old_clusters, old_top, old_graph, old_manifest = validate_bundle(
            legacy, result.nodes, result.edges
        )
    current_roles, _, current_top, _, current_manifest = validate_bundle(
        legacy, result.nodes, result.edges
    )
    old_csv = (
        old_roles.priority_score.tolist() == result.roles.priority_score.tolist()
        and old_top.priority_score.tolist() == result.top.priority_score.tolist()
        and old_clusters.cluster_id.tolist() == result.clusters.cluster_id.tolist()
    )
    old_json = [
        row["priority_score"] for row in old_graph["nodes"]
    ] == result.roles.priority_score.tolist()
    new_csv = (
        current_roles.priority_score.tolist() == updated.roles.priority_score.tolist()
        and current_top.priority_score.tolist() == updated.top.priority_score.tolist()
    )
    complete_old = old_csv and old_json and old_manifest == previous_manifest
    return {
        "passed": switched
        and legacy.is_symlink()
        and complete_old
        and new_csv
        and current_manifest != previous_manifest,
        "migration_occurred_between_csv_reads": switched,
        "legacy_became_version_pointer": legacy.is_symlink(),
        "reader_received_complete_old_generation": complete_old,
        "subsequent_reader_received_new_generation": new_csv,
        "old_and_new_manifest_differ": old_manifest != current_manifest,
    }


def build_report(data: Path, expected_clusters: int = 89) -> dict:
    edges, nodes, tx = fixture()
    result = run_analysis(prepare_dataset(edges, nodes, tx))
    row = result.roles.loc[result.roles.gid.eq(nodes.gid.iloc[3])].iloc[0]
    report = {
        "threshold_float": {
            "passed": row.role == "consolidator" and 20 * int(row.out_tiyn) == 7 * int(row.in_tiyn),
            "expected_role_by_documented_rule": "consolidator",
            "actual_role": row.role,
            "exact_in_tiyn": int(row.in_tiyn),
            "exact_out_tiyn": int(row.out_tiyn),
            "boundary_comparison": "20 * 525035 == 7 * 1500100",
            "evidence": row.evidence,
        }
    }
    bad_nodes = nodes.copy()
    bad_nodes.loc[4, "depth"] = 4
    wrong_depth = rejection(lambda: prepare_dataset(edges, bad_nodes, tx))
    report["incorrect_depth_accepted"] = {
        "passed": wrong_depth["rejected"],
        "gid": str(nodes.gid.iloc[4]),
        "true_shortest_depth": 2,
        "attempted_depth": 4,
        **wrong_depth,
    }
    with tempfile.TemporaryDirectory(prefix="hackalem-acceptance-") as temporary:
        root = Path(temporary)
        report["input_output_collision"] = path_probes(root, edges, nodes, tx)
        report["json_not_validated"] = graph_probes(root, result)
        report["legacy_reader_race"] = legacy_reader_probe(root, result)

    self_nodes = nodes.copy()
    self_nodes["depth"], self_nodes["is_seed"] = 0, True
    self_tx = pd.DataFrame(
        {
            "src": [self_nodes.gid.iloc[0]],
            "dst": [self_nodes.gid.iloc[0]],
            "date": pd.to_datetime(["2026-07-01"]),
            "sum_kzt": [1000000.0],
        }
    )
    self_edges = self_tx[["src", "dst", "sum_kzt"]].copy()
    self_edges["n_tx"], self_edges["depth"] = 1, 1
    self_result = run_analysis(prepare_dataset(self_edges, self_nodes, self_tx))
    self_row = self_result.roles.loc[self_result.roles.gid.eq(self_nodes.gid.iloc[0])].iloc[0]
    report["self_transfer_priority"] = {
        "passed": self_row.priority_score == 0
        and self_row.n_peers == 0
        and self_row.turnover_tiyn == 0
        and self_row.self_transfer_tiyn == 100000000,
        "gid": str(self_row.gid),
        "n_peers": int(self_row.n_peers),
        "priority_score": float(self_row.priority_score),
        "external_turnover_tiyn": int(self_row.turnover_tiyn),
        "self_transfer_tiyn": int(self_row.self_transfer_tiyn),
        "role": self_row.role,
    }

    before_hashes = file_hashes(data)
    dataset, input_hashes = load_dataset(data)
    actual = run_analysis(dataset, input_hashes=input_hashes)
    projection = undirected_projection(actual.graph)
    disconnected = []
    for cid, group in actual.roles.groupby("cluster_id"):
        components = list(nx.connected_components(projection.subgraph(group.gid)))
        if len(components) > 1:
            disconnected.append(
                {"cluster_id": int(cid), "component_sizes": sorted(map(len, components))}
            )
    unchanged = file_hashes(data) == before_hashes
    report["disconnected_communities"] = {
        "passed": not disconnected and len(actual.clusters) == expected_clusters and unchanged,
        "nodes": len(actual.nodes),
        "clusters": len(actual.clusters),
        "expected_clusters": expected_clusters,
        "disconnected": disconnected,
        "actual_input_sha256_unchanged": unchanged,
        "input_sha256": input_hashes,
    }
    report["all_passed"] = all(item["passed"] for item in report.values())
    report["source_sha256"] = {
        path.relative_to(PROJECT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(
            [*(PROJECT / "hackalem").glob("*.py"), PROJECT / "starter" / "starter.py"]
        )
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=PROJECT / "data")
    parser.add_argument("--expected-clusters", type=int, default=89)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT / "reports" / "audit-after.json",
        help="JSON report path (default: reports/audit-after.json)",
    )
    args = parser.parse_args()
    report = build_report(args.data, args.expected_clusters)
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    print(encoded, end="")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded, encoding="utf-8")
    raise SystemExit(0 if report["all_passed"] else 1)


if __name__ == "__main__":
    main()
