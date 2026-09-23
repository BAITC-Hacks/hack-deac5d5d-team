# Проверки качества

Из корня репозитория, после создания виртуального окружения CPython 3.14:

```bash
.venv/bin/python -m pip install -r starter/requirements.txt -r requirements-dev.txt
.venv/bin/python -m pip check
PYTHONPATH=starter .venv/bin/python -B -m unittest discover -s starter/tests -v
PYTHONPATH=starter .venv/bin/python -O -B -m unittest discover -s starter/tests -v
.venv/bin/python -m ruff check starter
.venv/bin/python -m ruff format --check starter --output-format concise
.venv/bin/python -m mypy --config-file pyproject.toml
```

Для исправления форматирования: `.venv/bin/python -m ruff format starter`.
Правила Ruff проверяют ошибки Python, неиспользуемые импорты и сортировку импортов
(E/F/I); длину длинных текстовых объяснений не ограничивают отдельно от форматтера.
Перед применением `ruff check --fix` проверьте публичные реэкспорты: их следует
обозначать явным `from module import name as name`, чтобы сохранить API.

Типизация вводится постепенно. Обязательная проверка mypy охватывает
`contracts.py` и `input_contracts.py`: сигнатуры функций, конфигурацию и обменные
структуры. Она не доказывает типовую корректность остальных модулей и столбцов
pandas; схемы таблиц дополнительно проверяются валидаторами во время выполнения.

GitHub Actions запускает эти команды в чистых окружениях Python 3.14 на
`ubuntu-latest` и `macos-latest`, включая повторный запуск тестов с `-O`.
Workflow выполняет только проверки и ничего не публикует. Наличие workflow
не означает, что удалённая матрица CI уже выполнена: результат доступен после
запуска Actions в репозитории. Локальный запуск на macOS не подтверждает Linux.

Источники настроек: [Ruff](https://docs.astral.sh/ruff/configuration/),
[mypy](https://mypy.readthedocs.io/en/stable/config_file.html),
[checkout](https://github.com/actions/checkout),
[setup-python](https://github.com/actions/setup-python).
