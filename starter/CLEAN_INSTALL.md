# Проверка чистой установки

Проверено 23 сентября 2026 года на macOS 26.5.2, arm64, CPython 3.14.0
(Clang 16.0.0), pip 25.2. Рабочий каталог:
`/private/tmp/hackalem-contracts-gz2_6p7s/project/starter`.

Создано отдельное окружение `/private/tmp/hackalem-clean-runtime`.
В `pyvenv.cfg` подтверждено `include-system-site-packages = false`.
Проверено, что NetworkX, NumPy, pandas, PyArrow и SciPy импортируются из нового
окружения. Существующее окружение в Downloads не изменялось: его интерпретатор
использовался только для создания нового venv.

## Выполненные команды

```bash
/Users/ramazananarbekov/Downloads/Hackalem/.venv/bin/python -m venv /private/tmp/hackalem-clean-runtime
/private/tmp/hackalem-clean-runtime/bin/python -m pip install --no-cache-dir -r requirements.txt
/private/tmp/hackalem-clean-runtime/bin/python -m pip check
/private/tmp/hackalem-clean-runtime/bin/python -m unittest discover -s tests -v
/private/tmp/hackalem-clean-runtime/bin/python -O -m unittest discover -s tests -v
```

Первый запрос к индексу пакетов в песочнице завершился ошибкой DNS.
Та же команда установки после разрешения сетевого доступа завершилась успешно.
Все закреплённые версии из `requirements.txt` доступны и установлены; пины
не изменялись.

## Результаты

| Проверка | Результат |
|---|---|
| Установка requirements.txt | Успешно, код завершения 0 |
| pip check | `No broken requirements found.`, код завершения 0 |
| unittest, обычный режим | 97 тестов, OK, код завершения 0; время unittest 11.747 с |
| unittest, режим -O | 97 тестов, OK, код завершения 0; время unittest 5.657 с |

Проверялись все обнаруженные тесты, включая CLI, точные денежные границы,
входные контракты, связность сообществ, JSON/CSV, сохранение предыдущей версии
при ошибках и чтение во время первоначальной миграции результатов.
Разница времени двух последовательных запусков не является измерением
ускорения от `-O`.

Установленные версии:

```text
networkx==3.7
numpy==2.5.3
pandas==3.0.6
pyarrow==25.0.1
python-dateutil==2.9.0.post0
scipy==1.18.1
six==1.17.0
```

Это локальная проверка macOS arm64. Linux и запуск CI в рамках этой проверки
не выполнялись; их успешное прохождение здесь не заявляется. Необязательный
эксперимент с Leiden проверен отдельно и описан в `COMMUNITY_EXPERIMENT.md`.

После восстановления совместимости отдельного экспорта `features_only` полный набор
повторён в этом же чистом окружении: 97 тестов OK за 5,664 с и с `-O` — 97 тестов OK
за 5,920 с. Последняя проверка включает создание вложенной папки экспорта и его статус.
