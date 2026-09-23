#!/usr/bin/env python3
"""Measure complete isolated pipeline runs on one immutable set of Parquet files.

python experiments/benchmark_pipeline.py --project .. --baseline /path/to/old/project --runs 5

No profiler is enabled. Each process loads inputs, validates, analyzes, validates
and publishes to a fresh temporary directory. Baseline is optional. Neither
source code nor the input files are modified.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

MODES = ("baseline", "diagnostics_off", "diagnostics_on")
LABELS = {
    "baseline": "До исправлений",
    "diagnostics_off": "Новый, diagnostics off",
    "diagnostics_on": "Новый, diagnostics on",
}
TIMINGS = (
    "prepare_seconds",
    "analysis_seconds",
    "publication_seconds",
    "pipeline_seconds",
    "process_seconds",
)


def starter_directory(path: Path) -> Path:
    directory = path.resolve()
    if not (directory / "starter.py").is_file():
        directory /= "starter"
    if not (directory / "starter.py").is_file():
        raise ValueError(f"Cannot find starter.py under {path}")
    return directory


def fingerprints(paths: list[Path]) -> dict[str, str]:
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}


def worker(mode: str, project: Path, data: Path, output: Path) -> dict[str, Any]:
    """Import one implementation, then time its complete functional pipeline."""
    sys.path.insert(0, str(project))
    if mode == "baseline":
        import starter
        from analysis import analyze

        started = perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            edges, nodes, tx = starter.load(data)
            starter.sanity_check(edges, nodes, tx)
            prepared = perf_counter()
            graph = starter.build_graph(edges, nodes)
            roles, clusters, top = analyze(graph, starter.basic_features(graph, nodes), edges, tx)
            analyzed = perf_counter()
            starter.write_outputs(roles, clusters, top, nodes, edges, output, graph=graph)
            published = perf_counter()
    else:
        from contracts import AnalysisConfig
        from pipeline import load_dataset, run_analysis
        from storage import save_result

        config = AnalysisConfig(diagnostics=mode == "diagnostics_on")
        started = perf_counter()
        dataset, input_hashes = load_dataset(data, config)
        prepared = perf_counter()
        result = run_analysis(dataset, config, input_hashes)
        analyzed = perf_counter()
        save_result(result, output)
        published = perf_counter()
        nodes, edges, tx = dataset.nodes, dataset.edges, dataset.transactions
        roles, clusters, top = result.roles, result.clusters, result.top

    core = roles[["gid", "role", "role_score", "cluster_id", "priority_score", "evidence"]]
    digest = hashlib.sha256(core.sort_values("gid").to_csv(index=False).encode()).hexdigest()
    return {
        "prepare_seconds": prepared - started,
        "analysis_seconds": analyzed - prepared,
        "publication_seconds": published - analyzed,
        "pipeline_seconds": published - started,
        "nodes": len(nodes),
        "edges": len(edges),
        "transactions": len(tx),
        "clusters": len(clusters),
        "role_counts": {str(key): int(value) for key, value in roles.role.value_counts().items()},
        "top_gids": [str(gid) for gid in top.gid],
        "core_roles_sha256": digest,
        "betweenness_modes": sorted(set(roles.betweenness_mode)),
        "betweenness_sources": sorted(int(value) for value in set(roles.betweenness_sources)),
    }


def summarize(runs: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    return {
        name: {
            "median": statistics.median(values := [run[name] for run in runs]),
            "min": min(values),
            "max": max(values),
        }
        for name in TIMINGS
    }


def report(payload: dict[str, Any]) -> str:
    environment = payload["environment"]
    records = payload["modes"]
    lines = [
        "# Измерение полного пайплайна",
        "",
        f"Измерено {payload['created_at']}. Python {environment['python']}, "
        f"{environment['system']} {environment['machine']}; "
        f"{payload['runs_per_mode']} запусков каждого варианта, без профилировщика.",
        "",
        "В каждом запуске отдельный процесс читает одни и те же Parquet, проверяет вход, "
        "считает граф, признаки, роли, кластеры и приоритет, затем проверяет и публикует "
        "результат в новую временную папку. Таблица «пайплайн» начинается после импортов "
        "основных модулей; отложенные импорты внутри расчёта включены. «Процесс» дополнительно "
        "включает запуск Python, все импорты и завершение процесса. Это запуск API, "
        "соответствующего полной обработке CLI, без форматирования консольной сводки CLI.",
        "",
        "Варианты чередовались с циклическим сдвигом порядка. Прогрев отдельно не выполнялся, "
        "кэш файловой системы не сбрасывался. Окружение одинаковое; время установки "
        "пакетов и удаления временных файлов не измерялось. Замеры отражают эту машину "
        "и обычный локальный диск, без гарантий по холодному диску или другим платформам.",
        "",
        "| Вариант | Подготовка, медиана с | Анализ, медиана с | Публикация, медиана с | Пайплайн, медиана [min–max], с | Процесс, медиана [min–max], с |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for mode, record in records.items():
        values = record["timings"]

        def interval(key: str) -> str:
            return (
                f"{values[key]['median']:.3f} [{values[key]['min']:.3f}–{values[key]['max']:.3f}]"
            )

        lines.append(
            f"| {LABELS[mode]} | {values['prepare_seconds']['median']:.3f} | "
            f"{values['analysis_seconds']['median']:.3f} | {values['publication_seconds']['median']:.3f} | "
            f"{interval('pipeline_seconds')} | {interval('process_seconds')} |"
        )
    off = records["diagnostics_off"]["timings"]["pipeline_seconds"]["median"]
    on = records["diagnostics_on"]["timings"]["pipeline_seconds"]["median"]
    saving = on - off
    lines.extend(
        [
            "",
            "Медианы этапов рассчитаны независимо и не обязаны в сумме равняться медиане полного времени.",
            "",
            f"Разница медиан нового пайплайна при включении старого временного признака: "
            f"{saving:+.3f} с ({saving / on:+.1%} от времени с diagnostics on). "
            "Это сравнение режимов на одной реализации; малые различия следует сопоставлять "
            "с диапазоном измерений. Отключение диагностики не меняет основные роли, их оценки, "
            "кластеры, приоритет и объяснения: контрольные SHA-256 этих полей совпали во всех запусках.",
        ]
    )
    if "baseline" in records:
        baseline = records["baseline"]["timings"]["pipeline_seconds"]["median"]
        delta = off - baseline
        lines.extend(
            [
                "",
                f"Разница новой версии без диагностики с исходной: {delta:+.3f} с "
                f"({delta / baseline:+.1%}). Эта разница объединяет все изменения. "
                "В новой версии добавлены точные денежные правила, BFS, разделение несвязных "
                "кластеров, расширенный JSON, сверка всего комплекта и манифест с хешами. "
                "Поэтому сравнение с исходной версией не выделяет эффект одной оптимизации "
                "и не предполагает равенства результатов разных версий.",
            ]
        )
    example = records["diagnostics_off"]["runs"][0]
    lines.extend(
        [
            "",
            f"Данные: {example['nodes']} клиентов, {example['edges']} направленных рёбер, "
            f"{example['transactions']} операций. Во всех вариантах сохранено точное "
            f"посредничество Brandes по всем {example['nodes']} источникам. Установленный seed=42 "
            "относится к кластеризации; приближение посредничества здесь не включалось.",
            "",
            "Исходные данные и код были хешированы до и после измерения; изменений за время "
            "серии не обнаружено. Подробные времена каждого запуска, порядок, версии "
            "зависимостей, распределения ролей, топ и SHA-256 находятся в "
            "[benchmark-results.json](experiments/benchmark-results.json).",
            "",
            "## Повторение",
            "",
            "Из папки starter/, в окружении с requirements.txt:",
            "",
            "```bash",
            "python experiments/benchmark_pipeline.py --project .. --baseline /path/to/old/project --runs 5",
            "```",
            "",
            "`--baseline` можно опустить для сравнения только двух новых режимов. "
            "`--project` и `--baseline` принимают корень проекта либо папку starter. "
            "При необходимости задайте `--data`, `--report` и `--output-json`. "
            "Входные Parquet, исходники и существующая папка out не изменяются.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument(
        "--output-json", type=Path, default=Path(__file__).with_name("benchmark-results.json")
    )
    parser.add_argument(
        "--report", type=Path, default=Path(__file__).resolve().parents[1] / "BENCHMARK.md"
    )
    parser.add_argument("--worker", choices=MODES, help=argparse.SUPPRESS)
    parser.add_argument("--worker-output", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    project = starter_directory(args.project)
    data = (args.data or project.parent / "data").resolve()
    if args.worker:
        print(json.dumps(worker(args.worker, project, data, args.worker_output)))
        return
    if args.runs < 1:
        parser.error("--runs must be positive")
    modes = ["diagnostics_off", "diagnostics_on"]
    sources = {"project": project}
    if args.baseline is not None:
        modes.insert(0, "baseline")
        sources["baseline"] = starter_directory(args.baseline)
    source_hashes = {name: fingerprints(list(path.glob("*.py"))) for name, path in sources.items()}
    input_files = [data / f"{name}.parquet" for name in ("nodes", "edges", "transactions")]
    input_hashes = fingerprints(input_files)
    collected: dict[str, list[dict[str, Any]]] = {mode: [] for mode in modes}
    order = []
    with tempfile.TemporaryDirectory(prefix="hackalem-benchmark-") as temporary:
        for repetition in range(args.runs):
            offset = repetition % len(modes)
            for mode in modes[offset:] + modes[:offset]:
                source = sources["baseline"] if mode == "baseline" else project
                output = Path(temporary) / f"{mode}-{repetition}"
                command = [
                    sys.executable,
                    "-B",
                    str(Path(__file__).resolve()),
                    "--worker",
                    mode,
                    "--project",
                    str(source),
                    "--data",
                    str(data),
                    "--worker-output",
                    str(output),
                ]
                started = perf_counter()
                process = subprocess.run(command, capture_output=True, text=True, check=True)
                elapsed = perf_counter() - started
                measurement = json.loads(process.stdout)
                measurement["process_seconds"] = elapsed
                measurement["run"] = repetition + 1
                if measurement["betweenness_modes"] != ["exact"] or measurement[
                    "betweenness_sources"
                ] != [measurement["nodes"]]:
                    raise RuntimeError("This benchmark requires exact all-source betweenness")
                collected[mode].append(measurement)
                order.append({"mode": mode, "run": repetition + 1})
                print(
                    f"{mode} {repetition + 1}/{args.runs}: pipeline={measurement['pipeline_seconds']:.3f}s, process={elapsed:.3f}s",
                    flush=True,
                )
    if input_hashes != fingerprints(input_files):
        raise RuntimeError("Input data changed during benchmark; no report written")
    if source_hashes != {
        name: fingerprints(list(path.glob("*.py"))) for name, path in sources.items()
    }:
        raise RuntimeError("Source code changed during benchmark; no report written")
    core_hashes = {
        run["core_roles_sha256"]
        for mode in ("diagnostics_off", "diagnostics_on")
        for run in collected[mode]
    }
    if len(core_hashes) != 1:
        raise RuntimeError("Diagnostics altered core outputs or results were nondeterministic")
    payload = {
        "schema_version": 1,
        "created_at": datetime.now().astimezone().isoformat(),
        "runs_per_mode": args.runs,
        "environment": {
            "python": platform.python_version(),
            "system": platform.system(),
            "machine": platform.machine(),
            "logical_cpus": os.cpu_count(),
            "dependencies": {
                name: importlib.metadata.version(name)
                for name in ("networkx", "numpy", "pandas", "pyarrow", "scipy")
            },
        },
        "input_sha256": input_hashes,
        "source_sha256": source_hashes,
        "sources_unchanged_during_run": True,
        "input_unchanged_during_run": True,
        "diagnostics_core_outputs_identical": True,
        "execution_order": order,
        "modes": {
            mode: {"timings": summarize(runs), "runs": runs} for mode, runs in collected.items()
        },
    }
    args.output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.report.write_text(report(payload), encoding="utf-8")
    print(f"Results: {args.output_json}\nReport: {args.report}")


if __name__ == "__main__":
    main()
