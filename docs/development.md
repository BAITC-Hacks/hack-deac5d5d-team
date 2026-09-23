# Разработка и проверки

Все команды выполняются из корня проекта в окружении CPython 3.14.
Установка для обычного запуска описана в [README](../README.md).

## Проверки перед изменением

```bash
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pip check
.venv/bin/python -B -m unittest discover -s tests -v
.venv/bin/python -O -B -m unittest discover -s tests -v
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy --config-file pyproject.toml
```

Для применения форматирования: `.venv/bin/python -m ruff format .`.
Ruff проверяет ошибки Python, неиспользуемые импорты и порядок импортов (E/F/I).
Длинные объяснения и URL не ограничиваются отдельно от форматтера. Перед
`ruff check --fix` проверяйте публичные реэкспорты: явная запись
`from module import name as name` сохраняет их назначение.

Mypy охватывает `hackalem/contracts.py` и `hackalem/input_contracts.py`:
сигнатуры, конфигурацию и обменные структуры. Остальные модули и столбцы pandas
не покрыты полной статической типизацией; схемы таблиц проверяются при выполнении.

97 тестов покрывают денежные границы ±1 тиын и большие целые, случайные разбиения
и перестановки сумм, глубины, FIFO, самопереводы, связность сообществ, JSON/CSV,
опасные пути, ошибки записи и чтение во время миграции. Проверки контрактов
используют явные исключения и остаются активными с `python -O`.

## Проверенная среда

23 сентября 2026 года зависимости установлены заново в отдельное окружение:
**CPython 3.14.0, macOS 26.5.2 arm64, pip 25.2**.
Подтверждено `include-system-site-packages = false`; NetworkX, NumPy, pandas,
PyArrow и SciPy импортировались из нового окружения. Закреплённые версии доступны,
`pip check` завершился с `No broken requirements found.`.

| Проверка | Результат |
|---|---|
| Полный набор | 97 тестов, OK |
| Тот же набор с `-O` | 97 тестов, OK |

Точный набор версий хранится в [requirements.txt](../requirements.txt);
инструменты разработки — в [requirements-dev.txt](../requirements-dev.txt).
Повторить проверку без использования рабочего окружения можно так:

```bash
python3.14 -m venv .venv-clean
.venv-clean/bin/python -m pip install --no-cache-dir -r requirements.txt
.venv-clean/bin/python -m pip check
.venv-clean/bin/python -m unittest discover -s tests -v
.venv-clean/bin/python -O -m unittest discover -s tests -v
```

GitHub Actions настроен для `ubuntu-latest` и `macos-latest`, Python 3.14,
включая тесты с `-O`, Ruff и mypy. Workflow только проверяет код.
**Удалённая матрица CI не запускалась в рамках локальной проверки**;
результат macOS не подтверждает работоспособность Linux.

## Границы модулей

Весь код находится в пакете `hackalem/`. Файлы разделены по ответственности:

| Модули | Назначение |
|---|---|
| `__main__.py`, `cli.py` | Точка входа, аргументы, сообщения и коды завершения |
| `contracts.py` | Неизменяемая конфигурация, Dataset, RuleDecision, AnalysisResult |
| `input_contracts.py`, `validation.py` | BFS, профиль данных, схемы и согласованность |
| `pipeline.py`, `analysis.py` | Подготовка данных и последовательность расчётов |
| `features.py`, `temporal.py` | Внешние структурные признаки и денежное сопоставление |
| `communities.py` | Проекция, стратегия кластеризации, связность и сводки |
| `rules.py` | Упорядоченные правила, факты и отдельное формирование объяснений |
| `ranking.py` | Вклады приоритета, топ и объяснение ранга |
| `storage.py`, `output_store.py` | CSV/JSON, проверка комплекта, снимок чтения и публикация |
| `teaching.py` | Учебные шаблоны и альтернативные расстояния вне основного запуска |

Предпочтительный API передаёт на сохранение единый результат:

```python
from pathlib import Path

from hackalem.contracts import AnalysisConfig, CASE_PROFILE
from hackalem.pipeline import load_dataset, run_analysis
from hackalem.storage import save_result

config = AnalysisConfig(input_profile=CASE_PROFILE)
dataset, hashes = load_dataset(Path("data"), config)
result = run_analysis(dataset, config, hashes)
save_result(result, Path("result"))
```

Для собственных DataFrame сначала вызывайте
`prepare_dataset(edges, nodes, tx, config)`; низкоуровневые расчёты ожидают
подготовленные данные. CLI дополнительно проверяет отношения файловых путей.
`AnalysisResult` объединяет граф и таблицы; изменяемое содержимое DataFrame
повторно проверяется при сохранении.

Алгоритмы сообществ можно сравнивать через `cluster_strategy(projection, config)`,
не копируя пайплайн. Стратегия возвращает покрывающие активные узлы группы;
проверка покрытия и разделение несвязных групп остаются общими.

`storage.snapshot_directory()` и `validate_bundle()` фиксируют открытый
дескриптор каталога при чтении нескольких файлов. Используйте их при параллельной
публикации: одного `Path.resolve()` недостаточно для миграции обычной папки.
Полный контракт хранения — в [методике](methodology.md).

## Параметры и совместимость CLI

```bash
# Профиль кейса: июль 2026, минимум 5 000 KZT на операцию.
.venv/bin/python -m hackalem --data data --out out --profile hackalem-july-2026

# Добавить старый диагностический временной признак.
.venv/bin/python -m hackalem --data data --out reports/diagnostic-result --diagnostics

# Прежний вход из корня проекта.
.venv/bin/python starter/starter.py --data data --out out
```

`--diagnostics` добавляет `temporal_out_share`, не меняя основные роли,
кластеры, оценки и топ. Прежняя форма
`--data data --validate-output out` также поддерживается. Проверка результата
ничего не записывает; успешный запуск возвращает 0, ошибка данных или публикации — 1.
`--debug` показывает traceback ожидаемых ошибок.

Запуск из `starter/` с активированным окружением остаётся доступен:
`python starter.py --data ../data --out ./out`.
Совместимые реэкспорты доступны из корня через `starter.starter`, а при запуске
из `starter/` — через `starter`; новый код использует API пакета `hackalem`.

Инструменты и настройки:
[Ruff](https://docs.astral.sh/ruff/configuration/),
[mypy](https://mypy.readthedocs.io/en/stable/config_file.html),
[checkout](https://github.com/actions/checkout),
[setup-python](https://github.com/actions/setup-python).
