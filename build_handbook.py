#!/usr/bin/env python3
"""Собирает index.html из карточек и системных промптов.

Страница самодостаточная: её открывают файлом, без сервера.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


GROUPS = [
    ("work", "Проектирование"),
    ("review", "Контроль кода"),
    ("study", "База"),
]

SITUATIONS = [
    ("work", "00", "Не знаю, с какого шага начать"),
    ("work", "01", "Заказчик дал несколько предложений"),
    ("work", "02", "История есть, поведение не разложено"),
    ("work", "03", "Неясно, новые ли это данные"),
    ("work", "04", "Нужно описать, как это хранится"),
    ("work", "05", "Неясно, какие части системы менять"),
    ("work", "06", "Нужно показать порядок вызовов"),
    ("work", "07", "Разработчику нужен контракт"),
    ("work", "08", "Пора заводить задачи"),
    ("review", "r0", "Куда ставить проверку кода"),
    ("review", "r1", "Есть фрагмент модуля, надо сверить"),
    ("study", "s1", "Неясно, как системы разговаривают"),
    ("study", "s2", "Требования путаются со схемами"),
    ("study", "s3", "Спорим про архитектуру"),
    ("study", "s4", "Нужно описать обмен"),
    ("study", "s5", "Неясно, когда задачу можно считать готовой"),
]

REVIEW_ITEMS = [
    ("general", "Каждый критерий приёмки найден в коде или записан как дыра"),
    ("general", "Каждое расширение сценария имеет ветку или явное «не обработано»"),
    ("general", "Повтор того же вызова не создаёт второй эффект, либо это вынесено в вопрос"),
    ("general", "Ошибка внешней системы и неполный ответ не остаются молчаливыми"),
    ("general", "Запись состояния происходит раньше исходящего события"),
    ("general", "Публичная операция, которой нет в контракте или сценарии, отмечена как лишняя"),
    ("onec", "Изменённые объекты метаданных есть в модели этой фичи"),
    ("onec", "Статусы и значения перечисления совпадают с закрытым списком"),
    ("onec", "Проведение пишет только регистры из модели"),
    ("onec", "Отмена проведения снимает те же движения"),
    ("onec", "Модуль назван и совпадает с зоной из разметки: объект, менеджер, общий, HTTP-сервис"),
    ("onec", "URL-шаблон и HTTP-метод совпадают с контрактом"),
    ("onec", "Привилегированный режим есть только там, где он назван в требованиях"),
    ("onec", "Роли на новую операцию названы или вынесены в вопрос"),
    ("onec", "Подписка на событие не дублирует проведение"),
    ("onec", "Запрос не читает объект, которого нет в модели этой фичи"),
    ("onec", "План обмена затронут только если обмен был в сценарии"),
]


def load_saflow():
    spec = importlib.util.spec_from_file_location("saflow", ROOT / "saflow.py")
    if spec is None or spec.loader is None:
        raise SystemExit("не найден saflow.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["saflow"] = module
    spec.loader.exec_module(module)
    return module


def user_prompt(module, step: dict) -> str:
    lines = [
        "<ПРОЕКТ>",
        "Вставьте сюда страницу проекта целиком: глоссарий, зоны ответственности, сущности, соглашения, ограничения.",
        "",
        "<БРИФ ФИЧИ>",
        "Вставьте слова заказчика и известные факты. Решение этого шага сюда не дописывайте.",
        "",
        "<ОТВЕТЫ НА ОТКРЫТЫЕ ВОПРОСЫ>",
        "Вставьте ответы людей. Если их ещё нет, напишите: ответов пока нет.",
        "",
    ]
    for need in step["needs"]:
        prev = module.BY_ID[need]
        files = ", ".join(prev["files"])
        lines.append(f"<{prev['title']} — результат шага {need}>")
        lines.append(f"Вставьте {files}. Если шаг выключен, вставьте причину, а не выдуманный артефакт.")
        lines.append("")
    lines.append("ЗАДАЧА")
    lines.append(step["task"])
    lines.append("")
    return "\n".join(lines)


def study_user(card: str) -> str:
    return (
        "<ШПАРГАЛКА>\n"
        + card
        + "\n\n<ЗАДАЧА>\nВставьте описание своей системы и вопрос, на который нужно ответить по этой шпаргалке.\n"
    )


def review_user() -> str:
    return (
        "<ПРОФИЛЬ>\nПодставится из переключателя на странице.\n\n"
        "<КРИТЕРИИ И СЦЕНАРИЙ>\nВставьте критерии приёмки и расширения.\n\n"
        "<ФРАГМЕНТ КОДА>\nВставьте одну процедуру, метод HTTP-сервиса или общий модуль.\n\n"
        "ЗАДАЧА\nСверь фрагмент с артефактами. Модуль не переписывай.\n"
    )


def build() -> None:
    module = load_saflow()
    by_id = {step["id"]: step for step in module.STEPS}
    study_system = (ROOT / "prompts" / "study.md").read_text(encoding="utf-8").strip()
    review_system = (ROOT / "prompts" / "09-code-review.md").read_text(encoding="utf-8").strip()
    steps = []
    for group, number, situation in SITUATIONS:
        card = (ROOT / "cards" / f"{number}.md").read_text(encoding="utf-8").strip()
        title = card.splitlines()[0].lstrip("# ").strip()
        entry = {
            "id": number,
            "group": group,
            "situation": situation,
            "title": title,
            "card": card,
            "system": "",
            "user": "",
            "files": [],
            "review": number == "r1",
        }
        if number in by_id:
            step = by_id[number]
            entry["system"] = (ROOT / "prompts" / step["system"]).read_text(encoding="utf-8").strip()
            entry["user"] = user_prompt(module, step)
            entry["files"] = step["files"]
        elif number == "r1":
            entry["system"] = review_system
            entry["user"] = review_user()
        elif group == "study":
            entry["system"] = study_system
            entry["user"] = study_user(card)
        steps.append(entry)

    payload = {
        "groups": [{"id": group_id, "label": label} for group_id, label in GROUPS],
        "reviewItems": [
            {"id": f"c{index}", "profile": profile, "text": text}
            for index, (profile, text) in enumerate(REVIEW_ITEMS, start=1)
        ],
        "steps": steps,
    }
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    template = (ROOT / "handbook.template.html").read_text(encoding="utf-8")
    if "__DATA__" not in template:
        raise SystemExit("в шаблоне нет маркера __DATA__")
    html = template.replace("__DATA__", data)
    (ROOT / "index.html").write_text(html, encoding="utf-8")
    print(ROOT / "index.html")


if __name__ == "__main__":
    build()
