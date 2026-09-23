"""Semantic input checks, separate from tabular and output validation."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import date
from typing import TYPE_CHECKING

import pandas as pd
from pandas.api.types import is_bool_dtype

from .validation import ValidationError, columns, integers, money_cents, require

if TYPE_CHECKING:
    from .contracts import InputProfile


def validate_depths(edges: pd.DataFrame, nodes: pd.DataFrame, max_depth: int = 4) -> None:
    """Require the declared depth to equal the shortest directed path from any seed.

    Every seed starts at zero, including isolated seeds. Nonseed clients must be
    reachable. Edge ``depth`` is acquisition metadata: this check deliberately
    makes no assumptions about its relationship to shortest node distances.
    The multi-source breadth-first traversal takes O(V + E) time and space.
    """
    require(
        isinstance(max_depth, int) and not isinstance(max_depth, bool) and max_depth >= 0,
        "profile.max_depth: expected a nonnegative integer",
    )
    columns(nodes, ("gid", "depth", "is_seed"), "nodes")
    columns(edges, ("src", "dst"), "edges")
    integers(nodes.gid, "nodes.gid")
    integers(nodes.depth, "nodes.depth")
    require(nodes.gid.is_unique, "nodes.gid: duplicate IDs")
    require(is_bool_dtype(nodes.is_seed), "nodes.is_seed: expected booleans")
    known = set(nodes.gid)
    for endpoint in ("src", "dst"):
        integers(edges[endpoint], f"edges.{endpoint}")
        require(set(edges[endpoint]) <= known, f"edges.{endpoint}: unknown client IDs")

    successors: dict[int, list[int]] = defaultdict(list)
    for src, dst in edges[["src", "dst"]].itertuples(index=False, name=None):
        successors[int(src)].append(int(dst))
    distances = {int(gid): 0 for gid in nodes.loc[nodes.is_seed, "gid"]}
    pending = deque(distances)
    while pending:
        src = pending.popleft()
        for dst in successors.get(src, ()):
            if dst not in distances:
                distances[dst] = distances[src] + 1
                pending.append(dst)

    sample = []
    for gid, declared in nodes[["gid", "depth"]].itertuples(index=False, name=None):
        actual = distances.get(int(gid))
        if actual is None or actual != declared or actual > max_depth:
            sample.append(
                {
                    "gid": int(gid),
                    "declared": int(declared),
                    "actual": actual if actual is not None else "unreachable",
                }
            )
            if len(sample) == 3:
                break
    require(
        not sample,
        f"nodes.depth: shortest directed distance from seeds mismatch "
        f"(maximum {max_depth}): {sample}",
    )


def _profile_date(value: str | None, label: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"profile.{label}: expected an ISO calendar date") from exc


def validate_profile(tx: pd.DataFrame, profile: InputProfile) -> None:
    """Check optional inclusive calendar dates and the exact minimum in tiyn.

    Bounds are interpreted in the transaction's recorded calendar timezone;
    times on the last permitted day are included. The caller passes
    ``profile.max_depth`` to :func:`validate_depths` because transactions carry
    no node depths. A generic profile can leave both date bounds unset.
    """
    columns(tx, ("date", "sum_kzt"), "transactions")
    require(
        isinstance(profile.min_tx_tiyn, int)
        and not isinstance(profile.min_tx_tiyn, bool)
        and profile.min_tx_tiyn >= 1,
        "profile.min_tx_tiyn: expected a positive integer",
    )
    start = _profile_date(profile.start_date, "start_date")
    end = _profile_date(profile.end_date, "end_date")
    require(
        start is None or end is None or start <= end, "profile: start_date must not exceed end_date"
    )
    parsed = pd.to_datetime(tx.date, errors="coerce")
    require(not parsed.isna().any(), "transactions.date: invalid dates")
    if start is not None or end is not None:
        days = parsed.dt.date
        if start is not None:
            require(
                days.ge(start).all(),
                f"transactions.date: earlier than profile {profile.name} start {start}",
            )
        if end is not None:
            require(
                days.le(end).all(),
                f"transactions.date: later than profile {profile.name} end {end}",
            )
    amounts = money_cents(tx.sum_kzt, "transactions.sum_kzt", positive=True)
    require(
        amounts.ge(profile.min_tx_tiyn).all(),
        f"transactions.sum_kzt: below profile {profile.name} minimum {profile.min_tx_tiyn} tiyn",
    )
