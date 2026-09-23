"""Command-line arguments, user-facing status, and expected failure codes."""

import argparse
from pathlib import Path
from time import perf_counter

from .contracts import CASE_PROFILE, AnalysisConfig, InputProfile
from .pipeline import load_dataset, run_analysis
from .storage import export_bundle, save_result, validate_bundle, validate_paths
from .validation import ValidationError


def main(argv: list[str] | None = None, *, default_data: Path = Path("data")) -> None:
    parser = argparse.ArgumentParser(
        description="Граф денег: от Parquet до проверенного комплекта результатов"
    )
    parser.add_argument(
        "command", nargs="?", choices=["analyze", "validate", "export"], default="analyze"
    )
    parser.add_argument("--data", type=Path, default=default_data)
    parser.add_argument("--out", type=Path, default=Path("./out"))
    parser.add_argument(
        "--validate-output", type=Path, help="совместимый режим проверки готового комплекта"
    )
    parser.add_argument("--source", type=Path, help="версия результатов для export")
    parser.add_argument("--destination", type=Path, help="новая обычная папка для export")
    parser.add_argument(
        "--diagnostics", action="store_true", help="дополнительно считать старый временной признак"
    )
    parser.add_argument("--profile", choices=["generic", CASE_PROFILE.name], default="generic")
    parser.add_argument(
        "--debug", action="store_true", help="показывать traceback ожидаемых ошибок"
    )
    args = parser.parse_args(argv)
    cfg = AnalysisConfig(
        diagnostics=args.diagnostics,
        input_profile=CASE_PROFILE if args.profile == CASE_PROFILE.name else InputProfile(),
    )
    started = perf_counter()
    try:
        if args.command == "export":
            if args.source is None or args.destination is None:
                parser.error("export требует --source и --destination")
            export_bundle(args.source, args.destination)
            print(f"Переносимый комплект: {args.destination}")
            return
        checking = args.validate_output is not None or args.command == "validate"
        if not checking:
            # Do this before loading, calculating, creating directories or writing files.
            validate_paths(args.data, args.out, Path(__file__).parent)
            validate_paths(args.data, args.out, Path(__file__).resolve().parents[1] / "starter")
        dataset, input_hashes = load_dataset(args.data, cfg)
        print(
            f"Проверены входные данные: {len(dataset.nodes)} узлов, {len(dataset.edges)} рёбер, "
            f"{len(dataset.transactions)} операций; профиль={cfg.input_profile.name}"
        )
        if checking:
            validate_bundle(args.validate_output or args.out, dataset.nodes, dataset.edges)
            print("ГОТОВЫЕ ВЫГРУЗКИ: проверка пройдена (CSV, JSON и manifest при наличии)")
            return
        result = run_analysis(dataset, cfg, input_hashes)
        save_result(result, args.out)
        print(
            f"Клиентов: {len(result.roles)}; кластеров: {len(result.clusters)}; в топе: {len(result.top)}"
        )
        print(f"Роли: {result.roles.role.value_counts().to_dict()}")
        print(
            f"Посредничество: {result.roles.betweenness_mode.iloc[0]}, источников={result.roles.betweenness_sources.iloc[0]}"
        )
        print(f"Готовый комплект: {args.out}; полный пересчёт: {perf_counter() - started:.2f} с")
    except (ValidationError, OSError) as exc:
        if args.debug:
            raise
        parser.exit(1, f"Ошибка данных или публикации: {exc}\n")
