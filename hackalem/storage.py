"""Validate, serialize and publish one coherent analysis generation.

Readers bind every file to an open directory descriptor. This also pins a
legacy physical directory when its pathname is atomically exchanged with a
symlink. Old generations must be retained while readers may hold descriptors.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from numbers import Integral, Real
from pathlib import Path
from typing import TYPE_CHECKING

import networkx as nx
import numpy as np
import pandas as pd

from .output_store import staged_output
from .validation import ValidationError, money_cents, require, validate_outputs

if TYPE_CHECKING:
    from .contracts import AnalysisResult

CSV_FILES = ("nodes_roles.csv", "clusters.csv", "top_nodes.csv")
DATA_FILES = (*CSV_FILES, "graph.json")
KNOWN_FILES = {*DATA_FILES, "manifest.json"}
ID_PATTERN = re.compile(r"0|[1-9][0-9]*")


def _path_forms(path: Path) -> set[Path]:
    return {Path(os.path.abspath(path)), Path(path).resolve()}


def _contains(parent: Path, child: Path) -> bool:
    return parent == child or parent in child.parents


def validate_paths(data_dir: Path, out_dir: Path, source_dir: Path) -> None:
    """Reject data/output overlap and output replacement of source directories.

    Both lexical and canonical paths matter: a symlink inside an output ancestor
    can point outside it, and replacing that ancestor would still destroy access.
    Output within source is permitted (the usual starter/out); output in or above
    data is forbidden. This check is intended before reading or calculating.
    """
    outputs, inputs, sources = map(_path_forms, (out_dir, data_dir, source_dir))
    for output in outputs:
        for data in inputs:
            require(
                not (_contains(output, data) or _contains(data, output)),
                "Unsafe paths: output and input directories overlap",
            )
        for source in sources:
            require(
                not _contains(output, source),
                "Unsafe paths: output would replace the source directory",
            )


class DirectorySnapshot:
    """Files opened through this object always belong to the same directory."""

    def __init__(self, path: Path):
        require(
            os.open in os.supports_dir_fd,
            "Directory-bound result reading is unsupported on this platform",
        )
        self.fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))

    def close(self) -> None:
        os.close(self.fd)

    def names(self) -> set[str]:
        return set(os.listdir(self.fd))

    @contextmanager
    def open(self, name: str, mode: str = "rb"):
        require(name in KNOWN_FILES, "Unrecognized result filename")
        fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=self.fd)
        with os.fdopen(fd, mode, encoding="utf-8" if "b" not in mode else None) as handle:
            yield handle

    def bytes(self, name: str) -> bytes:
        with self.open(name) as handle:
            return handle.read()


@contextmanager
def snapshot_directory(path: Path):
    snapshot = DirectorySnapshot(path)
    try:
        yield snapshot
    finally:
        snapshot.close()


def _read_ids(snapshot: DirectorySnapshot, filename: str) -> pd.DataFrame:
    with snapshot.open(filename) as handle:
        frame = pd.read_csv(handle, dtype={"gid": "string"}, float_precision="round_trip")
    require("gid" in frame, f"{filename}: missing gid column")
    require(
        frame.gid.notna().all() and frame.gid.str.fullmatch(ID_PATTERN).all(),
        f"{filename}.gid: expected exact decimal integer text",
    )
    values = [int(value) for value in frame.gid]
    require(
        all(value <= np.iinfo(np.int64).max for value in values),
        f"{filename}.gid: out of int64 range",
    )
    frame["gid"] = pd.Series(values, index=frame.index, dtype="int64")
    return frame


def _read_tables(snapshot: DirectorySnapshot):
    roles = _read_ids(snapshot, CSV_FILES[0])
    with snapshot.open(CSV_FILES[1]) as handle:
        clusters = pd.read_csv(handle, float_precision="round_trip")
    top = _read_ids(snapshot, CSV_FILES[2])
    return roles, clusters, top


def _strict_json(raw: bytes, label: str):
    def duplicate_free(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"{label}: duplicate JSON key {key}")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValidationError(f"{label}: non-finite number {value}")

    def finite_float(value):
        parsed = float(value)
        require(math.isfinite(parsed), f"{label}: non-finite number")
        return parsed

    try:
        return json.loads(
            raw,
            object_pairs_hook=duplicate_free,
            parse_constant=reject_constant,
            parse_float=finite_float,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{label}: invalid JSON") from exc


def _id(value, label: str) -> int:
    require(
        isinstance(value, str) and ID_PATTERN.fullmatch(value) is not None,
        f"{label}: expected exact decimal string ID",
    )
    parsed = int(value)
    require(parsed <= np.iinfo(np.int64).max, f"{label}: ID out of int64 range")
    return parsed


def _integer(value, label: str, minimum: int = 0) -> int:
    require(
        isinstance(value, Integral)
        and not isinstance(value, (bool, np.bool_))
        and value >= minimum,
        f"{label}: expected integer >= {minimum}",
    )
    return int(value)


def _amount(value, label: str) -> int:
    require(
        isinstance(value, Real) and not isinstance(value, (bool, np.bool_)),
        f"{label}: expected numeric money",
    )
    return int(money_cents(pd.Series([value]), label, positive=True).iloc[0])


def _expected_edges(edges: pd.DataFrame) -> dict:
    require(
        {"src", "dst", "sum_kzt", "n_tx", "depth"} <= set(edges.columns),
        "edges: missing columns for complete graph contract",
    )
    require(not edges.duplicated(["src", "dst"]).any(), "edges: duplicate src/dst pairs")
    cents = money_cents(edges.sum_kzt, "edges.sum_kzt", positive=True)
    expected = {}
    for row, amount in zip(edges.itertuples(index=False), cents):
        key = (_integer(row.src, "edges.src"), _integer(row.dst, "edges.dst"))
        expected[key] = (
            int(amount),
            _integer(row.n_tx, "edges.n_tx", 1),
            _integer(row.depth, "edges.depth", 1),
        )
    return expected


def validate_graph(graph: nx.DiGraph, nodes: pd.DataFrame, edges: pd.DataFrame) -> None:
    """Reject a graph from another run even when CSV tables are individually valid."""
    require(
        isinstance(graph, nx.DiGraph) and not graph.is_multigraph(),
        "graph: expected a directed simple graph",
    )
    gids = {_integer(gid, "graph.gid") for gid in graph.nodes}
    require(gids == set(nodes.gid), "graph: nodes disagree with input nodes")
    for node in nodes.itertuples(index=False):
        attrs = graph.nodes[node.gid]
        require(
            _integer(attrs.get("depth"), "graph.node.depth") == int(node.depth),
            "graph: node depth disagrees with input",
        )
        require(
            isinstance(attrs.get("is_seed"), (bool, np.bool_))
            and bool(attrs["is_seed"]) == bool(node.is_seed),
            "graph: node seed flag disagrees with input",
        )
    expected = _expected_edges(edges)
    actual = {}
    for src, dst, attrs in graph.edges(data=True):
        amount = _amount(attrs.get("sum_kzt"), "graph.sum_kzt")
        if "sum_tiyn" in attrs:
            require(
                _integer(attrs["sum_tiyn"], "graph.sum_tiyn", 1) == amount,
                "graph: sum_tiyn disagrees with sum_kzt",
            )
        actual[(src, dst)] = (
            amount,
            _integer(attrs.get("n_tx"), "graph.n_tx", 1),
            _integer(attrs.get("depth"), "graph.depth", 1),
        )
    require(actual == expected, "graph: edges/amounts/counts/depth disagree with input")


def _json_value(value):
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or pd.isna(value):
        return None
    return value.item() if isinstance(value, np.generic) else value


def write_graph_json(graph: nx.DiGraph, roles: pd.DataFrame, path: Path) -> None:
    records = []
    for row in roles.to_dict("records"):
        record = {key: _json_value(value) for key, value in row.items()}
        record["gid"] = str(row["gid"])
        if isinstance(record.get("role_facts"), str):
            record["role_facts"] = _strict_json(record["role_facts"].encode(), "role_facts")
        records.append(record)
    links = [
        {"src": str(src), "dst": str(dst), **_json_value(attrs)}
        for src, dst, attrs in graph.edges(data=True)
    ]
    status = (
        "complete"
        if {"role", "cluster_id", "priority_score"} <= set(roles.columns)
        else "features_only"
    )
    payload = {"schema_version": 3, "analysis_status": status, "nodes": records, "edges": links}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def _same_json_value(actual, expected, label: str) -> None:
    if isinstance(expected, dict):
        require(
            isinstance(actual, dict) and set(actual) == set(expected), f"{label}: object mismatch"
        )
        for key in expected:
            _same_json_value(actual[key], expected[key], f"{label}.{key}")
    elif isinstance(expected, list):
        require(
            isinstance(actual, list) and len(actual) == len(expected), f"{label}: list mismatch"
        )
        for left, right in zip(actual, expected):
            _same_json_value(left, right, label)
    elif expected is None:
        require(actual is None, f"{label}: expected null")
    elif isinstance(expected, bool):
        require(isinstance(actual, bool) and actual == expected, f"{label}: boolean mismatch")
    elif isinstance(expected, (int, float)):
        require(
            isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and math.isfinite(actual)
            and actual == expected,
            f"{label}: numeric mismatch",
        )
    else:
        require(isinstance(actual, str) and actual == expected, f"{label}: text mismatch")


def validate_graph_payload(
    payload, roles: pd.DataFrame, nodes: pd.DataFrame, edges: pd.DataFrame
) -> None:
    require(isinstance(payload, dict), "graph.json: expected object")
    require(
        payload.get("schema_version") in (1, 2, 3)
        and not isinstance(payload.get("schema_version"), bool),
        "graph.json: unsupported schema",
    )
    require(payload.get("analysis_status") == "complete", "graph.json: analysis is not complete")
    records, links = payload.get("nodes"), payload.get("edges")
    require(
        isinstance(records, list) and isinstance(links, list),
        "graph.json: missing nodes/edges arrays",
    )
    indexed = {}
    for record in records:
        require(isinstance(record, dict), "graph.json: node must be object")
        gid = _id(record.get("gid"), "graph.json.gid")
        require(gid not in indexed, "graph.json: duplicate node ID")
        indexed[gid] = record
    require(
        set(indexed) == set(nodes.gid) == set(roles.gid),
        "graph.json: node IDs disagree with CSV/input",
    )
    for row in roles.to_dict("records"):
        expected = {key: _json_value(value) for key, value in row.items()}
        expected["gid"] = str(row["gid"])
        if isinstance(expected.get("role_facts"), str):
            expected["role_facts"] = _strict_json(expected["role_facts"].encode(), "CSV role_facts")
        _same_json_value(indexed[row["gid"]], expected, "graph.json.node")
    expected_edges = _expected_edges(edges)
    actual = {}
    for link in links:
        require(isinstance(link, dict), "graph.json: edge must be object")
        src, dst = _id(link.get("src"), "graph.json.src"), _id(link.get("dst"), "graph.json.dst")
        require(src in indexed and dst in indexed, "graph.json: unknown edge endpoint")
        key = (src, dst)
        require(key not in actual, "graph.json: duplicate edge")
        amount = _amount(link.get("sum_kzt"), "graph.json.sum_kzt")
        if "sum_tiyn" in link:
            require(
                _integer(link["sum_tiyn"], "graph.json.sum_tiyn", 1) == amount,
                "graph.json: inconsistent amount representations",
            )
        actual[key] = (
            amount,
            _integer(link.get("n_tx"), "graph.json.n_tx", 1),
            _integer(link.get("depth"), "graph.json.depth", 1),
        )
    require(actual == expected_edges, "graph.json: edges/amounts/counts/depth disagree with input")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _validate_manifest(snapshot: DirectorySnapshot, manifest) -> None:
    require(snapshot.names() == KNOWN_FILES, "manifest: unexpected or missing bundle files")
    require(
        isinstance(manifest, dict) and manifest.get("schema_version") in (2, 3),
        "manifest: unsupported schema",
    )
    require(isinstance(manifest.get("config"), dict), "manifest: missing config")
    require(isinstance(manifest.get("input_hashes"), dict), "manifest: missing input hashes")
    require(
        all(
            isinstance(name, str)
            and isinstance(digest, str)
            and re.fullmatch(r"[a-f0-9]{64}", digest)
            for name, digest in manifest["input_hashes"].items()
        ),
        "manifest: invalid input hashes",
    )
    hashes = manifest.get("files")
    require(
        isinstance(hashes, dict) and set(hashes) == set(DATA_FILES),
        "manifest: incomplete file hashes",
    )
    for name, digest in hashes.items():
        require(
            isinstance(digest, str) and _sha256(snapshot.bytes(name)) == digest,
            f"manifest: checksum mismatch for {name}",
        )


def _validate_snapshot(snapshot: DirectorySnapshot, nodes, edges):
    names = snapshot.names()
    require(set(CSV_FILES) <= names, "Result bundle: missing CSV files")
    roles, clusters, top = _read_tables(snapshot)
    validate_outputs(roles, clusters, top, nodes, edges)
    payload = manifest = None
    if "graph.json" in names:
        payload = _strict_json(snapshot.bytes("graph.json"), "graph.json")
        validate_graph_payload(payload, roles, nodes, edges)
    if "manifest.json" in names:
        manifest = _strict_json(snapshot.bytes("manifest.json"), "manifest.json")
        _validate_manifest(snapshot, manifest)
    return roles, clusters, top, payload, manifest


def validate_bundle(out_dir: Path, nodes: pd.DataFrame, edges: pd.DataFrame):
    """Check one pinned generation; legacy CSV-only submissions remain readable."""
    with snapshot_directory(out_dir) as snapshot:
        return _validate_snapshot(snapshot, nodes, edges)


def _validate_existing(out_dir: Path, nodes, edges) -> None:
    with snapshot_directory(out_dir) as snapshot:
        require(
            snapshot.names() <= KNOWN_FILES,
            "Refusing to migrate an unrecognized output directory (unknown files)",
        )
        require(
            set(CSV_FILES) <= snapshot.names(),
            "Refusing to migrate an unrecognized output directory (missing CSV files)",
        )
        if "manifest.json" in snapshot.names():
            # Modern bundles carry their own ownership and integrity evidence.
            # They may describe a previous input dataset, so compare hashes here;
            # validation against the current input occurs only for the new result.
            manifest = _strict_json(snapshot.bytes("manifest.json"), "manifest.json")
            _validate_manifest(snapshot, manifest)
        else:
            _validate_snapshot(snapshot, nodes, edges)


def save_result(result: AnalysisResult, out_dir: Path, graph_writer=None) -> None:
    """Validate before writing and after serialization, then publish atomically."""
    validate_outputs(result.roles, result.clusters, result.top, result.nodes, result.edges)
    validate_graph(result.graph, result.nodes, result.edges)
    writer = graph_writer or write_graph_json
    with staged_output(
        out_dir, lambda path: _validate_existing(path, result.nodes, result.edges)
    ) as version:
        for name, frame in zip(CSV_FILES, (result.roles, result.clusters, result.top)):
            frame.to_csv(version / name, index=False)
        writer(result.graph, result.roles, version / "graph.json")
        manifest = {
            "schema_version": 3,
            "config": result.config.to_dict(),
            "input_hashes": dict(result.input_hashes),
            "files": {name: _sha256((version / name).read_bytes()) for name in DATA_FILES},
        }
        (version / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, allow_nan=False, sort_keys=True),
            encoding="utf-8",
        )
        validate_bundle(version, result.nodes, result.edges)


def export_bundle(source: Path, destination: Path) -> None:
    """Copy a verified version to a new ordinary directory for portable delivery."""
    destination = Path(destination)
    for src in _path_forms(source):
        for dst in _path_forms(destination):
            require(not (_contains(src, dst) or _contains(dst, src)), "Export paths overlap")
    require(
        not destination.exists() and not destination.is_symlink(), "Export destination must be new"
    )
    with snapshot_directory(source) as snapshot:
        require(
            snapshot.names() == KNOWN_FILES, "Portable export requires a complete versioned bundle"
        )
        manifest = _strict_json(snapshot.bytes("manifest.json"), "manifest.json")
        _validate_manifest(snapshot, manifest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}-export-", dir=destination.parent)
        )
        try:
            for name in sorted(KNOWN_FILES):
                (staging / name).write_bytes(snapshot.bytes(name))
            require(not destination.exists(), "Export destination appeared while copying")
            os.rename(staging, destination)
        except BaseException:
            shutil.rmtree(staging)
            raise
