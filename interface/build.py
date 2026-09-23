"""Build a self-contained HTML file; no server, CDN or browser fetch required."""

import json
from pathlib import Path

import pandas as pd


def records(frame, id_columns=()):
    result = []
    for row in frame.itertuples(index=False):
        item = row._asdict()
        for key in id_columns:
            item[key] = str(item[key])
        result.append({key: None if pd.isna(value) else value for key, value in item.items()})
    return result


def build_payload(roles, clusters, top, features, edges, tx, source):
    # Always recalculate observed metrics from raw data, even with imported analysis.
    observed = features.copy()
    analytical = roles.drop(columns=[c for c in features if c != "gid" and c in roles])
    enriched = observed.merge(analytical, on="gid", validate="one_to_one")
    if "priority_reason" not in enriched:
        enriched["priority_reason"] = enriched.gid.map(top.set_index("gid").why).fillna(
            "Клиент вне топа. Объяснение приоритета не передано аналитикой.")
    enriched.loc[enriched.gid.isin(top.gid), "priority_reason"] = enriched.gid.map(top.set_index("gid").why)
    return {
        "meta": {"schema_version": 1, "analysis_source": source,
                 "period_start": str(tx.date.min().date()) if len(tx) else None,
                 "period_end": str(tx.date.max().date()) if len(tx) else None,
                 "n_transactions": len(tx), "n_seed": int(features.is_seed.sum()),
                 "warning": "Роли — гипотезы. Приоритет не является вероятностью нарушения."},
        "nodes": records(enriched.sort_values("gid"), ("gid",)),
        "edges": records(edges.sort_values(["src", "dst"]), ("src", "dst")),
        "top": records(top, ("gid",)),
        "clusters": records(clusters),
    }


def write_interface(payload, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    (out_dir / "graph.json").write_text(serialized + "\n", encoding="utf-8")
    # Prevent closing the inert script element from any analyst-provided text.
    embedded = serialized.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = Path(__file__).with_name("template.html").read_text(encoding="utf-8")
    if template.count("__GRAPH_DATA__") != 1:
        raise ValueError("Interface template must contain exactly one data placeholder")
    template = template.replace("__GRAPH_STYLE__", Path(__file__).with_name("style.css").read_text(encoding="utf-8"))
    template = template.replace("__GRAPH_SCRIPT__", Path(__file__).with_name("app.js").read_text(encoding="utf-8"))
    (out_dir / "index.html").write_text(template.replace("__GRAPH_DATA__", embedded), encoding="utf-8")
