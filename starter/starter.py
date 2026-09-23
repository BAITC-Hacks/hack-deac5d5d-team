#!/usr/bin/env python3
"""
Код кейса «Граф денег» — HackAlem AI.

Что он делает:
  1. грузит три parquet-файла и проверяет их консистентность;
  2. собирает направленный взвешенный граф;
  3. считает признаки, кластеры, гипотезы ролей и приоритет проверки;
  4. проверяет и пишет три готовых CSV и безопасный для браузера graph.json;
  5. проверяет готовые выгрузки отдельной командой --validate-output.

Правила и ограничения гипотез описаны в METHODOLOGY.md.

Запуск:
    python starter.py --data ../data --out ./out
"""

import argparse
import json
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd
import networkx as nx

from analysis import analyze
from output_store import staged_output

from validation import (ROLES, CLUSTER_COLUMNS, TOP_COLUMNS, ValidationError,
                        validate_inputs, validate_outputs, validate_output_files)


# ---------------------------------------------------------------- загрузка

def load(data_dir: Path):
    edges = pd.read_parquet(data_dir / "edges.parquet")
    nodes = pd.read_parquet(data_dir / "nodes.parquet")
    tx = pd.read_parquet(data_dir / "transactions.parquet")
    tx["date"] = pd.to_datetime(tx["date"])
    return edges, nodes, tx


def sanity_check(edges, nodes, tx):
    """Проверки, которые стоит пройти до того, как строить модель."""
    orphans = validate_inputs(edges, nodes, tx)
    print("=" * 64)
    print("ПРОВЕРКА ДАННЫХ")
    print("=" * 64)
    print(f"  узлов в nodes.parquet : {len(nodes):>6}")
    print(f"  рёбер                 : {len(edges):>6}")
    print(f"  транзакций            : {len(tx):>6}")
    print(f"  seed-клиентов         : {int(nodes.is_seed.sum()):>6}")
    print(f"  оборот, KZT           : {edges.sum_kzt.sum():>14,.2f}")
    if not tx.empty:
        print(f"  период                : {tx.date.min().date()} — {tx.date.max().date()}")
    print("  edges == transactions : OK (пары, суммы до тиына, количество)")
    print(f"\n  ВНИМАНИЕ: {len(orphans)} узлов нет ни в одном ребре "
          f"(из них seed: {len(orphans & set(nodes[nodes.is_seed].gid))})")
    print("  → все включены в граф и nodes_roles.csv")
    print("=" * 64, "\n")
    return orphans


# ---------------------------------------------------------------- граф

def build_graph(edges, nodes) -> nx.DiGraph:
    """Направленный граф. sum_kzt — вес ребра, n_tx — количество переводов."""
    G = nx.DiGraph()
    for r in nodes.sort_values("gid").itertuples(index=False):
        G.add_node(int(r.gid), depth=int(r.depth), is_seed=bool(r.is_seed))
    for r in edges.sort_values(["src", "dst"]).itertuples(index=False):
        if r.src not in G or r.dst not in G:
            raise ValidationError("edges: unknown client IDs; run sanity_check before build_graph")
        G.add_edge(r.src, r.dst, sum_kzt=float(r.sum_kzt), n_tx=int(r.n_tx), depth=int(r.depth))
    return G


def basic_features(G: nx.DiGraph, nodes: pd.DataFrame) -> pd.DataFrame:
    """Базовые метрики. Это старт, а не финиш — добавляйте свои."""
    in_deg = dict(G.in_degree())
    out_deg = dict(G.out_degree())
    in_kzt = dict(G.in_degree(weight="sum_kzt"))
    out_kzt = dict(G.out_degree(weight="sum_kzt"))
    in_tx = dict(G.in_degree(weight="n_tx"))
    out_tx = dict(G.out_degree(weight="n_tx"))
    pr = nx.pagerank(G, weight="sum_kzt")

    df = nodes[["gid", "depth", "is_seed"]].sort_values("gid").reset_index(drop=True).copy()
    df["in_deg"] = df.gid.map(in_deg).fillna(0).astype(int)
    df["out_deg"] = df.gid.map(out_deg).fillna(0).astype(int)
    df["in_kzt"] = df.gid.map(in_kzt).fillna(0.0)
    df["out_kzt"] = df.gid.map(out_kzt).fillna(0.0)
    df["in_tx"] = df.gid.map(in_tx).fillna(0).astype(int)
    df["out_tx"] = df.gid.map(out_tx).fillna(0).astype(int)
    df["pagerank"] = df.gid.map(pr).fillna(0.0)

    # Соотношение наблюдаемых сумм, не доказательство транзита тех же денег.
    # Внешние поступления, начальные остатки и операции вне периода неизвестны.
    df["pass_through"] = np.where(df.in_kzt > 0, df.out_kzt / df.in_kzt.replace(0, np.nan), np.nan)

    # ЛОВУШКА КЕЙСА: узел на 4-м колене без исходящих может быть не «стоком»,
    # а просто местом, где закончился обход. Разберитесь с этим.
    df["truncated_by_depth"] = (df.depth == 4) & (df.out_deg == 0)
    return df


# ---------------------------------------------------------------- выгрузки

def write_templates(df: pd.DataFrame, out_dir: Path):
    """Учебные шаблоны. Строгую проверку готового результата НЕ проходят."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. nodes_roles.csv — схема из ТЗ, роли не заполнены
    roles = df[["gid"]].copy()
    roles["role"] = ""            # TODO: одна из ROLES
    roles["role_score"] = 0.0     # TODO: 0..1
    roles["cluster_id"] = -1      # TODO: номер кластера
    roles["priority_score"] = 0.0 # TODO: 0..1
    roles["evidence"] = ""        # TODO: почему — с числами, до 200 символов
    roles = roles.merge(
        df[["gid", "in_deg", "out_deg", "in_kzt", "out_kzt", "pagerank",
            "pass_through", "depth", "is_seed", "truncated_by_depth"]],
        on="gid", how="left")
    roles.to_csv(out_dir / "nodes_roles.csv", index=False)

    # 2. clusters.csv — пустой каркас
    pd.DataFrame(columns=CLUSTER_COLUMNS) \
        .to_csv(out_dir / "clusters.csv", index=False)

    # 3. top_nodes.csv — пустой каркас, нужно ≥20 строк
    pd.DataFrame(columns=TOP_COLUMNS) \
        .to_csv(out_dir / "top_nodes.csv", index=False)

    print(f"ШАБЛОНЫ записаны в {out_dir}/; это НЕ готовое решение, строгая проверка их отклонит.")


def write_outputs(roles, clusters, top, nodes, edges, out_dir: Path, graph=None):
    """Проверить полный комплект в отдельной версии и атомарно опубликовать."""
    validate_outputs(roles, clusters, top, nodes, edges)
    with staged_output(out_dir) as version:
        for name, frame in (("nodes_roles", roles), ("clusters", clusters), ("top_nodes", top)):
            frame.to_csv(version / f"{name}.csv", index=False)
        if graph is not None:
            write_graph_json(graph, roles, version / "graph.json")
        validate_output_files(version, nodes, edges)
    print(f"Готовые выгрузки проверены и опубликованы: {out_dir}/")


def write_graph_json(G: nx.DiGraph, df: pd.DataFrame, path: Path):
    """Browser boundary: all client IDs are strings; missing ratios become null."""
    records = []
    for row in df.itertuples(index=False):
        record = row._asdict()
        record["gid"] = str(row.gid)
        records.append({key: None if pd.isna(value) else value for key, value in record.items()})
    edges = [{"src": str(src), "dst": str(dst), **attributes}
             for src, dst, attributes in G.edges(data=True)]
    status = "complete" if {"role", "cluster_id", "priority_score"} <= set(df.columns) else "features_only"
    payload = {"schema_version": 1, "analysis_status": status,
               "nodes": records, "edges": edges}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def betweenness_features(G: nx.DiGraph, distance_mode="hops", sample_size=None, seed=42):
    """Explicit distance choice. Amount strength is not a shortest-path length."""
    if distance_mode == "hops":
        graph, weight = G, None
    elif distance_mode == "inverse_amount":
        graph, weight = G.copy(), "distance"
        for _, _, attrs in graph.edges(data=True):
            amount = attrs["sum_kzt"]
            if not np.isfinite(amount) or amount <= 0:
                raise ValidationError("inverse_amount requires finite positive sums")
            attrs[weight] = 1.0 / amount
    else:
        raise ValueError("distance_mode must be 'hops' or 'inverse_amount'")
    return nx.betweenness_centrality(graph, k=sample_size, weight=weight, seed=seed)


# ---------------------------------------------------------------- подсказки

def hints(G: nx.DiGraph, df: pd.DataFrame):
    """Куда смотреть дальше. Ответов здесь нет — только направления."""
    print("\nС ЧЕГО НАЧАТЬ")
    print("-" * 64)
    print(f"  узлов, получающих от 3+ разных плательщиков : {(df.in_deg >= 3).sum()}")
    print(f"  узлов, рассылающих на 10+ получателей       : {(df.out_deg >= 10).sum()}")
    print(f"  узлов и с входом, и с выходом               : {((df.in_deg > 0) & (df.out_deg > 0)).sum()}")
    print(f"  узлов, обрезанных 4-м коленом               : {df.truncated_by_depth.sum()}  <- разберитесь")
    print(f"  слабосвязных компонент                      : {nx.number_weakly_connected_components(G)}")
    print("""
  Вопросы, на которые стоит ответить метриками:
    * чем «деньги пришли и остались» отличается от «пришли и ушли дальше»?
    * что важнее для роли — количество плательщиков или сумма?
    * узел собирает средства от нескольких SEED — это случайность или структура?
    * если убрать узел, сеть распадётся или переживёт?

  Полезное в networkx: pagerank, hits, betweenness_centrality,
  community.louvain_communities, simple_cycles, all_simple_paths.
  Не забудьте: граф НАПРАВЛЕННЫЙ и ВЗВЕШЕННЫЙ.
  Для betweenness сумма — НЕ расстояние. Используйте betweenness_features:
  hops (по числу шагов) или inverse_amount (явная гипотеза: большой поток ближе).
  Ни один вариант не доказывает движение тех же денег; нужны даты переводов.
""")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="../data", help="папка с parquet-файлами")
    ap.add_argument("--out", default="./out", help="куда писать выгрузки")
    ap.add_argument("--validate-output", type=Path,
                    help="проверить готовые CSV в папке без пересчёта")
    a = ap.parse_args()

    try:
        started = perf_counter()
        edges, nodes, tx = load(Path(a.data))
        sanity_check(edges, nodes, tx)
        if a.validate_output is not None:
            validate_output_files(a.validate_output, nodes, edges)
            print("ГОТОВЫЕ ВЫГРУЗКИ: проверка пройдена")
            return
        G = build_graph(edges, nodes)
        df = basic_features(G, nodes)
        roles, clusters, top = analyze(G, df, edges, tx)
        write_outputs(roles, clusters, top, nodes, edges, Path(a.out), graph=G)
        print(f"Клиентов: {len(roles)}; кластеров: {len(clusters)}; в топе: {len(top)}")
        print(f"Роли: {roles.role.value_counts().to_dict()}")
        print(f"Посредничество: {roles.betweenness_mode.iloc[0]}, источников={roles.betweenness_sources.iloc[0]}")
        print(f"Полный пересчёт: {perf_counter() - started:.2f} с")
    except (ValueError, OSError, KeyError, pd.errors.ParserError, nx.NetworkXException) as exc:
        ap.exit(1, f"Ошибка проверки или чтения: {exc}\n")


if __name__ == "__main__":
    main()
