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


SITUATIONS = [
    ("00", "Не знаю, с какого шага начать"),
    ("01", "Заказчик дал несколько предложений"),
    ("02", "История есть, поведение не разложено"),
    ("03", "Неясно, новые ли это данные"),
    ("04", "Нужно описать, как это хранится"),
    ("05", "Неясно, какие части системы менять"),
    ("06", "Нужно показать порядок вызовов"),
    ("07", "Разработчику нужен контракт"),
    ("08", "Пора заводить задачи"),
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


def build() -> None:
    module = load_saflow()
    by_id = {step["id"]: step for step in module.STEPS}
    steps = []
    for number, situation in SITUATIONS:
        card = (ROOT / "cards" / f"{number}.md").read_text(encoding="utf-8").strip()
        title = card.splitlines()[0].lstrip("# ").strip()
        entry = {
            "id": number,
            "situation": situation,
            "title": title,
            "card": card,
            "system": "",
            "user": "",
            "files": [],
        }
        if number in by_id:
            step = by_id[number]
            entry["system"] = (ROOT / "prompts" / step["system"]).read_text(encoding="utf-8").strip()
            entry["user"] = user_prompt(module, step)
            entry["files"] = step["files"]
        steps.append(entry)

    data = json.dumps({"steps": steps}, ensure_ascii=False).replace("<", "\\u003c")
    template = (ROOT / "handbook.template.html").read_text(encoding="utf-8")
    if "__DATA__" not in template:
        raise SystemExit("в шаблоне нет маркера __DATA__")
    html = template.replace("__DATA__", data)
    (ROOT / "index.html").write_text(html, encoding="utf-8")
    print(ROOT / "index.html")


if __name__ == "__main__":
    build()
