"""Typed public boundaries and the immutable analysis specification."""

import math
from dataclasses import asdict, dataclass, field
from datetime import date
from fractions import Fraction
from typing import Any

import networkx as nx
import pandas as pd


@dataclass(frozen=True)
class InputProfile:
    name: str = "generic"
    start_date: str | None = None
    end_date: str | None = None
    min_tx_tiyn: int = 1
    max_depth: int = 4

    def __post_init__(self) -> None:
        if (
            not self.name
            or isinstance(self.min_tx_tiyn, bool)
            or not isinstance(self.min_tx_tiyn, int)
            or isinstance(self.max_depth, bool)
            or not isinstance(self.max_depth, int)
            or self.min_tx_tiyn < 1
            or self.max_depth < 0
        ):
            raise ValueError("Invalid input profile")
        start = date.fromisoformat(self.start_date) if self.start_date else None
        end = date.fromisoformat(self.end_date) if self.end_date else None
        if start and end and start > end:
            raise ValueError("Profile start date is after end date")


CASE_PROFILE = InputProfile("hackalem-july-2026", "2026-07-01", "2026-07-31", 500000, 4)


@dataclass(frozen=True)
class AnalysisConfig:
    random_seed: int = 42
    community_resolution: float = 1.0
    exact_max_nodes: int = 5000
    exact_max_edges: int = 50000
    betweenness_sources: int = 256
    temporal_window_days: int = 7
    diagnostics: bool = False
    top_n: int = 20
    consolidator_max_out_ratio: Fraction = Fraction(7, 20)
    max_counterparty_share: Fraction = Fraction(4, 5)
    distributor_min_out_ratio: Fraction = Fraction(3, 2)
    transit_min_out_ratio: Fraction = Fraction(13, 20)
    transit_max_out_ratio: Fraction = Fraction(27, 20)
    transit_min_matched_share: Fraction = Fraction(1, 2)
    min_fan_peers: int = 3
    distributor_degree_multiplier: int = 2
    coordinator_min_direction_peers: int = 2
    coordinator_min_peers: int = 5
    coordinator_min_external: int = 2
    coordinator_min_bridge_percentile: float = 0.9
    seed_flow_score_cap: float = 0.65
    priority_weights: tuple[tuple[str, float], ...] = (
        ("turnover_tiyn", 0.30),
        ("betweenness", 0.25),
        ("pagerank", 0.20),
        ("n_peers", 0.15),
        ("operation_count", 0.10),
    )
    input_profile: InputProfile = field(default_factory=InputProfile)

    def __post_init__(self) -> None:
        for name in (
            "exact_max_nodes",
            "exact_max_edges",
            "betweenness_sources",
            "temporal_window_days",
            "min_fan_peers",
            "distributor_degree_multiplier",
            "coordinator_min_direction_peers",
            "coordinator_min_peers",
            "coordinator_min_external",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name}: expected a positive integer")
        if isinstance(self.top_n, bool) or not isinstance(self.top_n, int) or self.top_n < 20:
            raise ValueError("top_n: at least 20 clients required")
        if not math.isfinite(self.community_resolution) or self.community_resolution <= 0:
            raise ValueError("community_resolution must be finite and positive")
        for name in (
            "consolidator_max_out_ratio",
            "max_counterparty_share",
            "distributor_min_out_ratio",
            "transit_min_out_ratio",
            "transit_max_out_ratio",
            "transit_min_matched_share",
        ):
            if not isinstance(getattr(self, name), Fraction) or getattr(self, name) <= 0:
                raise ValueError(f"{name}: expected a positive exact Fraction")
        if self.transit_min_out_ratio > self.transit_max_out_ratio:
            raise ValueError("Transit ratio interval is reversed")
        if self.max_counterparty_share > 1 or self.transit_min_matched_share > 1:
            raise ValueError("Share thresholds must not exceed one")
        if (
            not 0 <= self.seed_flow_score_cap <= 1
            or not 0 <= self.coordinator_min_bridge_percentile <= 1
        ):
            raise ValueError("Confidence/percentile thresholds must be in [0, 1]")
        weights = dict(self.priority_weights)
        allowed = {"turnover_tiyn", "betweenness", "pagerank", "n_peers", "operation_count"}
        if len(weights) != len(self.priority_weights) or set(weights) != allowed:
            raise ValueError("priority_weights: expected each supported feature exactly once")
        if not all(math.isfinite(v) and v >= 0 for v in weights.values()) or not math.isclose(
            sum(weights.values()), 1, abs_tol=1e-12
        ):
            raise ValueError("priority_weights must be finite, nonnegative and sum to one")

    def to_dict(self) -> dict[str, Any]:
        def convert(value: Any) -> Any:
            if isinstance(value, Fraction):
                return {"numerator": value.numerator, "denominator": value.denominator}
            if isinstance(value, dict):
                return {key: convert(item) for key, item in value.items()}
            if isinstance(value, (tuple, list)):
                return [convert(item) for item in value]
            return value

        return {key: convert(value) for key, value in asdict(self).items()}


@dataclass(frozen=True)
class Dataset:
    edges: pd.DataFrame
    nodes: pd.DataFrame
    transactions: pd.DataFrame


@dataclass(frozen=True)
class RuleDecision:
    role: str
    score: float
    rule_id: str
    facts: dict[str, Any]


@dataclass(frozen=True)
class AnalysisResult:
    graph: nx.DiGraph
    roles: pd.DataFrame
    clusters: pd.DataFrame
    top: pd.DataFrame
    nodes: pd.DataFrame
    edges: pd.DataFrame
    config: AnalysisConfig = field(default_factory=AnalysisConfig)
    input_hashes: dict[str, str] = field(default_factory=dict)
