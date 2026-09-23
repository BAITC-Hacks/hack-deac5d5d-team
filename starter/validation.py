"""Input and submission contracts. Checks remain active under python -O."""

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.api.types import is_bool_dtype, is_integer_dtype, is_numeric_dtype

ROLES = ("consolidator", "transit", "distributor", "terminal", "coordinator", "peripheral")
ROLE_COLUMNS = ("gid", "role", "role_score", "cluster_id", "priority_score", "evidence")
CLUSTER_COLUMNS = ("cluster_id", "n_nodes", "n_seed", "sum_kzt_internal", "top_gids", "hypothesis")
TOP_COLUMNS = ("rank", "gid", "role", "priority_score", "why")


class ValidationError(ValueError):
    """A data contract was violated."""


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def columns(frame, required, label):
    require(frame.columns.is_unique, f"{label}: duplicate column names")
    missing = set(required) - set(frame.columns)
    require(not missing, f"{label}: missing columns {sorted(missing)}")
    require(not frame[list(required)].isna().any().any(), f"{label}: null required values")


def integers(series, label, minimum=0):
    # Never repair float IDs: their precision may already have been lost.
    require(is_integer_dtype(series.dtype) and not is_bool_dtype(series.dtype),
            f"{label}: expected integers, not floats/strings")
    require(not series.isna().any(), f"{label}: null integers")
    require(series.between(minimum, np.iinfo(np.int64).max).all(), f"{label}: out of int64 range")


def numeric(series, label):
    require(is_numeric_dtype(series.dtype) and not is_bool_dtype(series.dtype),
            f"{label}: expected numbers")
    require(np.isfinite(series.to_numpy(dtype=float)).all(), f"{label}: non-finite values")


def money_cents(series, label, positive=False):
    """Compare money in integer tiyn; no relative tolerance at large amounts."""
    numeric(series, label)
    values = []
    for value in series:
        try:
            cents = Decimal(str(value)) * 100
        except InvalidOperation as exc:
            raise ValidationError(f"{label}: invalid money") from exc
        require(cents == cents.to_integral_value(), f"{label}: more than two decimal places")
        require(cents > 0 if positive else cents >= 0, f"{label}: invalid amount")
        values.append(int(cents))
    # Python integers also avoid overflow while aggregating large datasets.
    return pd.Series(values, index=series.index, dtype=object)


def text_values(series, label, limit=None, with_number=False):
    require(series.map(lambda s: isinstance(s, str) and bool(s.strip())).all(),
            f"{label}: expected nonempty text")
    if limit is not None:
        require(series.str.len().le(limit).all(), f"{label}: exceeds {limit} characters")
    if with_number:
        require(series.str.contains(r"\d", regex=True).all(), f"{label}: numeric evidence required")


def validate_inputs(edges, nodes, tx):
    columns(nodes, ("gid", "depth", "is_seed"), "nodes")
    columns(edges, ("src", "dst", "sum_kzt", "n_tx", "depth"), "edges")
    columns(tx, ("src", "dst", "date", "sum_kzt"), "transactions")
    require(len(nodes) > 0, "nodes: empty dataset")
    integers(nodes.gid, "nodes.gid")
    require(nodes.gid.is_unique, "nodes.gid: duplicate IDs")
    integers(nodes.depth, "nodes.depth")
    require(nodes.depth.le(4).all(), "nodes.depth: expected 0..4")
    require(is_bool_dtype(nodes.is_seed), "nodes.is_seed: expected booleans")
    require(nodes.is_seed.eq(nodes.depth.eq(0)).all(), "nodes: seed/depth mismatch")
    known = set(nodes.gid)
    for label, frame in (("edges", edges), ("transactions", tx)):
        for col in ("src", "dst"):
            integers(frame[col], f"{label}.{col}")
            require(set(frame[col]) <= known, f"{label}.{col}: unknown client IDs")
    require(not edges.duplicated(["src", "dst"]).any(), "edges: duplicate src/dst pairs")
    integers(edges.n_tx, "edges.n_tx", minimum=1)
    integers(edges.depth, "edges.depth", minimum=1)
    require(edges.depth.le(4).all(), "edges.depth: expected 1..4")
    dates = pd.to_datetime(tx.date, errors="coerce")
    require(not dates.isna().any(), "transactions.date: invalid dates")

    amounts = tx[["src", "dst"]].copy()
    amounts["cents"] = money_cents(tx.sum_kzt, "transactions.sum_kzt", positive=True)
    agg = amounts.groupby(["src", "dst"]).agg(
        tx_cents=("cents", "sum"), tx_count=("cents", "size")).reset_index()
    expected = edges[["src", "dst", "n_tx"]].copy()
    expected["edge_cents"] = money_cents(edges.sum_kzt, "edges.sum_kzt", positive=True)
    merged = expected.merge(agg, on=["src", "dst"], how="outer", indicator=True,
                            validate="one_to_one")
    require(merged._merge.eq("both").all(), "edges/transactions: pair mismatch")
    for left, right, label in (("edge_cents", "tx_cents", "amount"),
                                ("n_tx", "tx_count", "transaction count")):
        bad = merged[left].ne(merged[right])
        sample = merged.loc[bad, ["src", "dst"]].head(3).to_dict("records")
        require(not bad.any(), f"edges/transactions: {label} mismatch: {sample}")
    return known - (set(edges.src) | set(edges.dst))


def validate_outputs(roles, clusters, top, nodes, edges):
    """Strict final submission validation; intentionally rejects blank templates."""
    for frame, required, label in ((roles, ROLE_COLUMNS, "nodes_roles"),
                                    (clusters, CLUSTER_COLUMNS, "clusters"),
                                    (top, TOP_COLUMNS, "top_nodes")):
        columns(frame, required, label)
    integers(nodes.gid, "nodes.gid")
    require(nodes.gid.is_unique, "nodes.gid: duplicate IDs")
    integers(roles.gid, "nodes_roles.gid")
    require(roles.gid.is_unique and set(roles.gid) == set(nodes.gid),
            "nodes_roles: IDs must cover every input client exactly once")
    require(roles.role.isin(ROLES).all(), "nodes_roles.role: invalid role")
    for frame, label, cols in ((roles, "nodes_roles", ("role_score", "priority_score")),
                                (top, "top_nodes", ("priority_score",))):
        for col in cols:
            numeric(frame[col], f"{label}.{col}")
            require(frame[col].between(0, 1).all(), f"{label}.{col}: expected 0..1")
    text_values(roles.evidence, "nodes_roles.evidence", limit=200, with_number=True)
    integers(roles.cluster_id, "nodes_roles.cluster_id")
    integers(clusters.cluster_id, "clusters.cluster_id")
    require(clusters.cluster_id.is_unique, "clusters: duplicate cluster IDs")
    require(set(roles.cluster_id) == set(clusters.cluster_id), "clusters: cluster references mismatch")
    integers(clusters.n_nodes, "clusters.n_nodes", minimum=1)
    integers(clusters.n_seed, "clusters.n_seed")
    text_values(clusters.hypothesis, "clusters.hypothesis")
    internal = money_cents(clusters.sum_kzt_internal, "clusters.sum_kzt_internal")
    assignment = roles.set_index("gid").cluster_id
    members = roles.groupby("cluster_id").gid.agg(set).to_dict()
    seeds = set(nodes.loc[nodes.is_seed, "gid"])
    sums = dict.fromkeys(members, 0)
    cents = money_cents(edges.sum_kzt, "edges.sum_kzt", positive=True)
    for edge, amount in zip(edges.itertuples(index=False), cents):
        require(edge.src in assignment.index and edge.dst in assignment.index,
                "edges: unknown client IDs")
        src_cluster, dst_cluster = assignment[edge.src], assignment[edge.dst]
        if src_cluster == dst_cluster:
            sums[src_cluster] += amount
    for cluster, amount in zip(clusters.itertuples(index=False), internal):
        group = members[cluster.cluster_id]
        require(cluster.n_nodes == len(group), "clusters.n_nodes: member count mismatch")
        require(cluster.n_seed == len(group & seeds), "clusters.n_seed: seed count mismatch")
        require(amount == sums[cluster.cluster_id], "clusters.sum_kzt_internal: amount mismatch")
        try:
            gids = json.loads(cluster.top_gids)
        except (TypeError, ValueError) as exc:
            raise ValidationError("clusters.top_gids: expected JSON array of string IDs") from exc
        require(isinstance(gids, list) and len(gids) > 0, "clusters.top_gids: expected nonempty list")
        require(all(isinstance(g, str) and g in {str(v) for v in group} for g in gids),
                "clusters.top_gids: expected string IDs belonging to this cluster")
        require(len(gids) == len(set(gids)), "clusters.top_gids: duplicate IDs")

    require(len(top) >= 20, "top_nodes: at least 20 rows required")
    integers(top.gid, "top_nodes.gid")
    integers(top["rank"], "top_nodes.rank", minimum=1)
    require(top.gid.is_unique and set(top.gid) <= set(roles.gid), "top_nodes: duplicate/unknown IDs")
    require(top["rank"].tolist() == list(range(1, len(top) + 1)), "top_nodes: ranks must be 1..N in order")
    require(top.priority_score.is_monotonic_decreasing, "top_nodes: priorities must descend")
    lookup = roles.set_index("gid").loc[top.gid]
    require(top.role.tolist() == lookup.role.tolist(), "top_nodes: roles disagree with nodes_roles")
    require(top.priority_score.tolist() == lookup.priority_score.tolist(),
            "top_nodes: scores disagree with nodes_roles")
    require(top.priority_score.tolist() == roles.priority_score.nlargest(len(top)).tolist(),
            "top_nodes: higher-priority clients are missing")
    text_values(top.why, "top_nodes.why", with_number=True)


def validate_output_files(out_dir: Path, nodes, edges):
    """Parse ID text exactly; reject decimal/exponent IDs before any conversion."""
    def read_with_ids(filename):
        frame = pd.read_csv(out_dir / filename, dtype={"gid": "string"}, float_precision="round_trip")
        require("gid" in frame, f"{filename}: missing gid column")
        require(frame.gid.notna().all() and frame.gid.str.fullmatch(r"0|[1-9][0-9]*").all(),
                f"{filename}.gid: expected exact decimal integer text")
        values = [int(value) for value in frame.gid]
        require(all(value <= np.iinfo(np.int64).max for value in values), f"{filename}.gid: out of int64 range")
        frame["gid"] = pd.Series(values, index=frame.index, dtype="int64")
        return frame

    roles = read_with_ids("nodes_roles.csv")
    clusters = pd.read_csv(out_dir / "clusters.csv", float_precision="round_trip")
    top = read_with_ids("top_nodes.csv")
    validate_outputs(roles, clusters, top, nodes, edges)
