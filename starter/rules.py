"""Ordered role hypotheses return structured facts; rendering is separate."""

from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Callable

from contracts import AnalysisConfig, RuleDecision

Facts = dict[str, Any]


def ratio_le(numerator: int, denominator: int, threshold: Fraction) -> bool:
    return (
        denominator > 0
        and int(numerator) * threshold.denominator <= int(denominator) * threshold.numerator
    )


def ratio_ge(numerator: int, denominator: int, threshold: Fraction) -> bool:
    return (
        denominator > 0
        and int(numerator) * threshold.denominator >= int(denominator) * threshold.numerator
    )


def _isolated(f: Facts, _: AnalysisConfig) -> float | None:
    return 0.0 if f["in_deg"] + f["out_deg"] == 0 else None


def _truncated(f: Facts, _: AnalysisConfig) -> float | None:
    return 0.2 if f["truncated_by_depth"] else None


def _coordinator(f: Facts, cfg: AnalysisConfig) -> float | None:
    if (
        f["in_deg"] >= cfg.coordinator_min_direction_peers
        and f["out_deg"] >= cfg.coordinator_min_direction_peers
        and f["n_peers"] >= cfg.coordinator_min_peers
        and f["external_communities"] >= cfg.coordinator_min_external
        and f["betweenness_percentile"] >= cfg.coordinator_min_bridge_percentile
    ):
        return min(
            0.9,
            0.55 + 0.2 * f["betweenness_percentile"] + 0.15 * min(f["external_communities"] / 4, 1),
        )
    return None


def _consolidator(f: Facts, cfg: AnalysisConfig) -> float | None:
    if (
        f["in_deg"] >= cfg.min_fan_peers
        and ratio_le(f["out_tiyn"], f["in_tiyn"], cfg.consolidator_max_out_ratio)
        and ratio_le(f["largest_in_tiyn"], f["in_tiyn"], cfg.max_counterparty_share)
    ):
        return min(
            0.95,
            0.55
            + 0.2 * min(f["in_deg"] / 10, 1)
            + 0.15 * (1 - f["out_tiyn"] / f["in_tiyn"])
            + 0.1 * (1 - f["largest_in_tiyn"] / f["in_tiyn"]),
        )
    return None


def _distributor(f: Facts, cfg: AnalysisConfig) -> float | None:
    if (
        f["out_deg"] >= cfg.min_fan_peers
        and f["out_deg"] >= cfg.distributor_degree_multiplier * max(f["in_deg"], 1)
        and (
            f["in_deg"] == 0 or ratio_ge(f["out_tiyn"], f["in_tiyn"], cfg.distributor_min_out_ratio)
        )
        and ratio_le(f["largest_out_tiyn"], f["out_tiyn"], cfg.max_counterparty_share)
    ):
        return min(
            0.9,
            0.55
            + 0.2 * min(f["out_deg"] / 10, 1)
            + 0.15 * (1 - f["largest_out_tiyn"] / f["out_tiyn"]),
        )
    return None


def _transit(f: Facts, cfg: AnalysisConfig) -> float | None:
    if (
        f["in_deg"] > 0
        and f["out_deg"] > 0
        and ratio_ge(f["out_tiyn"], f["in_tiyn"], cfg.transit_min_out_ratio)
        and ratio_le(f["out_tiyn"], f["in_tiyn"], cfg.transit_max_out_ratio)
        and ratio_ge(
            f["temporal_matched_tiyn"], f["temporal_outgoing_tiyn"], cfg.transit_min_matched_share
        )
    ):
        balance = min(f["in_tiyn"], f["out_tiyn"]) / max(f["in_tiyn"], f["out_tiyn"])
        return 0.45 + 0.10 * balance + 0.35 * f["temporal_matched_out_share"]
    return None


def _terminal(f: Facts, _: AnalysisConfig) -> float | None:
    return min(0.75, 0.55 + 0.04 * f["in_deg"]) if f["in_deg"] > 0 and f["out_deg"] == 0 else None


def _peripheral(f: Facts, _: AnalysisConfig) -> float:
    return 0.55 if f["n_peers"] <= 2 else 0.3


@dataclass(frozen=True)
class Rule:
    name: str
    role: str
    evaluate: Callable[[Facts, AnalysisConfig], float | None]


RULES = (
    Rule("no_external_operations", "peripheral", _isolated),
    Rule("depth_boundary", "peripheral", _truncated),
    Rule("community_bridge", "coordinator", _coordinator),
    Rule("consolidation", "consolidator", _consolidator),
    Rule("distribution", "distributor", _distributor),
    Rule("dated_amount_transit", "transit", _transit),
    Rule("observed_terminal", "terminal", _terminal),
    Rule("fallback", "peripheral", _peripheral),
)


def decide_role(row: Any, config: AnalysisConfig | None = None) -> RuleDecision:
    cfg = config or AnalysisConfig()
    integers = (
        "in_deg",
        "out_deg",
        "n_peers",
        "external_communities",
        "depth",
        "in_tiyn",
        "out_tiyn",
        "largest_in_tiyn",
        "largest_out_tiyn",
        "turnover_tiyn",
        "temporal_matched_tiyn",
        "temporal_outgoing_tiyn",
        "same_day_uncertain_tiyn",
        "self_transfer_tiyn",
        "self_transfer_n_tx",
    )
    facts: Facts = {key: int(getattr(row, key)) for key in integers}
    for key in (
        "betweenness",
        "betweenness_percentile",
        "temporal_matched_out_share",
        "same_day_uncertain_out_share",
    ):
        facts[key] = float(getattr(row, key))
    for key in ("is_seed", "truncated_by_depth"):
        facts[key] = bool(getattr(row, key))
    facts["window_days"] = cfg.temporal_window_days
    facts["observation_completeness"] = row.observation_completeness
    for rule in RULES:
        score = rule.evaluate(facts, cfg)
        if score is None:
            continue
        score = round(score, 6)
        flow_role = rule.role in ("consolidator", "distributor", "transit", "terminal")
        cap = cfg.seed_flow_score_cap if facts["is_seed"] and flow_role else 1.0
        facts.update(rule_id=rule.name, score_uncapped=score, score_cap=cap)
        return RuleDecision(rule.role, min(score, cap), rule.name, facts)
    raise RuntimeError("Role rules lack an unconditional fallback")


def format_tiyn(tiyn: int) -> str:
    integer, cents = divmod(int(tiyn), 100)
    return f"{integer}.{cents:02d}"


def render_evidence(decision: RuleDecision) -> str:
    """Compact explanation from the same facts published for a detailed UI card."""
    f = decision.facts
    rule = decision.rule_id
    if rule == "no_external_operations":
        text = f"Внешних связей=0; самопереводов={f['self_transfer_n_tx']}; данных для внешней роли нет"
    elif rule == "depth_boundary":
        text = f"Входов={f['in_deg']}, вход={format_tiyn(f['in_tiyn'])} KZT; выход=0; глубина={f['depth']}: обход обрезан, роль неизвестна"
    elif rule == "community_bridge":
        text = f"Гипотеза связующего: соседей={f['n_peers']}; внешних групп={f['external_communities']}; посредничество={f['betweenness']:.5f}; не доказательство управления"
    elif rule == "consolidation":
        text = f"Гипотеза сбора: входов={f['in_deg']}; вход={format_tiyn(f['in_tiyn'])} KZT; выход/вход={f['out_tiyn'] / f['in_tiyn']:.3f}; крупнейший вход={f['largest_in_tiyn'] / f['in_tiyn']:.0%}"
    elif rule == "distribution":
        text = f"Гипотеза распределения: входов={f['in_deg']}, получателей={f['out_deg']}; выход={format_tiyn(f['out_tiyn'])} KZT; крупнейший выход={f['largest_out_tiyn'] / f['out_tiyn']:.0%}; источник неизвестен"
    elif rule == "dated_amount_transit":
        text = f"Гипотеза транзита: вход={format_tiyn(f['in_tiyn'])}, выход={format_tiyn(f['out_tiyn'])} KZT; FIFO 1–{f['window_days']}д={f['temporal_matched_out_share']:.0%}; тот же день=?{f['same_day_uncertain_out_share']:.0%}; те же деньги не установлены"
    elif rule == "observed_terminal":
        text = f"Наблюдаемый получатель: входов={f['in_deg']}; вход={format_tiyn(f['in_tiyn'])} KZT; выход=0; глубина={f['depth']}; вне выборки операции неизвестны"
    else:
        text = f"{'Мало связей' if f['n_peers'] <= 2 else 'Роль неоднозначна'}: входов={f['in_deg']}, выходов={f['out_deg']}; FIFO 1–{f['window_days']}д={f['temporal_matched_out_share']:.0%}; тот же день=?{f['same_day_uncertain_out_share']:.0%}; иные правила не выполнены"
    suffix = "; seed: вход неполон" if f["is_seed"] else ""
    if len(text + suffix) > 200:
        # Semantic compact form retains the decision, measurable support and uncertainty.
        text = (
            f"Гипотеза {decision.role}: входов={f['in_deg']}, выходов={f['out_deg']}; "
            f"FIFO={f['temporal_matched_out_share']:.0%}; тот же день=?{f['same_day_uncertain_out_share']:.0%}; "
            f"глубина={f['depth']}; полнота не установлена; подробности в role_facts"
        )
    return text + suffix


def role_hypothesis(row: Any, config: AnalysisConfig | None = None) -> tuple[str, float, str]:
    decision = decide_role(row, config)
    return decision.role, decision.score, render_evidence(decision)
