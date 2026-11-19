#!/usr/bin/env python3
"""Универсальный конвейер системного анализа.

Метод: https://habr.com/ru/articles/1098788/
Домен живёт только в project.md и inputs.md. Промпты домен не знают.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROMPTS = ROOT / "prompts"
TEMPLATES = ROOT / "templates"
WORK = Path.cwd() / "saflow-work"

STEPS = [
    {
        "id": "01",
        "slug": "user-story",
        "title": "User Story",
        "system": "01-user-story.md",
        "files": ["01-user-story.md"],
        "needs": [],
        "task": (
            "Составь пользовательские истории и критерии приёмки. "
            "Если результат не помещается в один спринт одной команды — раздели истории явно."
        ),
    },
    {
        "id": "02",
        "slug": "use-case",
        "title": "Use Case",
        "system": "02-use-case.md",
        "files": ["02-use-case.md"],
        "needs": ["01"],
        "task": "Составь use case по пользовательской истории. Покрой основной сценарий и расширения.",
    },
    {
        "id": "03",
        "slug": "data-model",
        "title": "Модель данных",
        "system": "03-data-model.md",
        "files": ["03-data-model.md"],
        "needs": ["02"],
        "task": (
            "Построй концептуальную модель данных. Покажи, какие сущности "
            "переиспользуются как есть, каким нужны новые атрибуты и какие появляются впервые."
        ),
    },
    {
        "id": "04",
        "slug": "dbml",
        "title": "Структура БД",
        "system": "04-dbml.md",
        "files": ["04-dbml.md"],
        "needs": ["03"],
        "task": "Выдай DBML и блоки «Миграция» и «Проверить у DBA».",
    },
    {
        "id": "05",
        "slug": "solution",
        "title": "Солюшен-архитектура",
        "system": "05-solution.md",
        "files": ["05-solution.md"],
        "needs": ["02", "04"],
        "task": "Разметь, где появится новый код. Новые сервисы не предлагай.",
    },
    {
        "id": "06",
        "slug": "sequence",
        "title": "Sequence",
        "system": "06-sequence.md",
        "files": ["06-sequence.puml"],
        "needs": ["02", "05"],
        "task": (
            "Построй sequence-диаграмму основного сценария и тех расширений, "
            "которые меняют порядок вызовов. Участники — только из списка проекта."
        ),
    },
    {
        "id": "07",
        "slug": "contracts",
        "title": "Контракты API",
        "system": "07-contracts.md",
        "files": ["07-openapi.yaml", "07-asyncapi.yaml", "07-notes.md"],
        "needs": ["02", "04", "06"],
        "task": (
            "Опиши контракты операций этой фичи. Коды ответов выводи из шагов "
            "и расширений use case, а не из общего списка HTTP."
        ),
    },
    {
        "id": "08",
        "slug": "tasks",
        "title": "Декомпозиция",
        "system": "08-tasks.md",
        "files": ["08-tasks.md"],
        "needs": ["01", "02", "03", "04", "05", "06", "07"],
        "task": "Разложи фичу на задачи. Для каждой — исполнитель, ссылка на артефакт, оценка.",
    },
]

BY_ID = {step["id"]: step for step in STEPS}
HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.M)
OPEN_Q = re.compile(r"открытые вопросы", re.I)
SKIP_LINE = re.compile(r"^-\s*(\d{2})\s*:\s*нет\s*[—-]\s*(.+)$", re.I | re.M)
SERVICE_LINE = re.compile(r"^-\s+([^—\-:]+?)\s*[—-]", re.M)


@dataclass
class Feature:
    slug: str
    path: Path

    @property
    def meta_path(self) -> Path:
        return self.path / "meta.json"

    def meta(self) -> dict:
        return json.loads(self.meta_path.read_text(encoding="utf-8"))

    def save_meta(self, meta: dict) -> None:
        self.meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def work_root(explicit: str | None) -> Path:
    return Path(explicit).resolve() if explicit else WORK


def feature_path(root: Path, slug: str) -> Feature:
    check_slug(slug)
    return Feature(slug, root / slug)


def check_slug(slug: str) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,48}", slug):
        raise SystemExit("slug: латиница, цифры и дефис, до 49 символов")


def read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def section(text: str, title: str) -> str:
    """Текст секции до следующего заголовка того же или более высокого уровня."""
    match = None
    for found in HEADING.finditer(text):
        if found.group(2).strip().lower() == title.lower():
            match = found
            break
    if not match:
        return ""
    level = len(match.group(1))
    start = match.end()
    end = len(text)
    for found in HEADING.finditer(text, start):
        if len(found.group(1)) <= level:
            end = found.start()
            break
    return text[start:end].strip()


def applicability(project_text: str) -> dict[str, str]:
    skipped = {}
    body = section(project_text, "Применимость")
    for match in SKIP_LINE.finditer(body):
        skipped[match.group(1)] = match.group(2).strip()
    return skipped


def services(project_text: str) -> list[str]:
    body = section(project_text, "Сервисы")
    names = []
    for match in SERVICE_LINE.finditer(body):
        name = match.group(1).strip(" *`")
        if name and name.lower() not in {"имя", "пример"}:
            names.append(name)
    return names


def open_questions(text: str) -> list[str]:
    items = []
    for found in HEADING.finditer(text):
        if not OPEN_Q.search(found.group(2)):
            continue
        level = len(found.group(1))
        start = found.end()
        end = len(text)
        for nxt in HEADING.finditer(text, start):
            if len(nxt.group(1)) <= level:
                end = nxt.start()
                break
        block = text[start:end]
        if re.search(r"(?i)нет открытых|открытых вопросов нет|отсутствуют", block):
            continue
        for line in block.splitlines():
            line = line.strip()
            if re.match(r"^([-*]|\d+[.)])\s+\S", line):
                items.append(re.sub(r"^([-*]|\d+[.)])\s+", "", line))
    return items


def has_open_block(text: str) -> bool:
    return any(OPEN_Q.search(found.group(2)) for found in HEADING.finditer(text))


def artifact(feature: Feature, step_id: str) -> str:
    step = BY_ID[step_id]
    parts = []
    for name in step["files"]:
        body = read(feature.path / name).strip()
        if body:
            parts.append(f"<!-- {name} -->\n{body}")
    return "\n\n".join(parts)


def skipped_reason(feature: Feature, step_id: str) -> str | None:
    meta = feature.meta()
    reason = meta.get("skipped", {}).get(step_id)
    if reason:
        return reason
    return applicability(read(feature.path / "project.md")).get(step_id)


def require_feature(feature: Feature) -> None:
    if not feature.meta_path.exists():
        raise SystemExit(f"нет фичи {feature.slug}. Сначала: saflow.py init {feature.slug}")


def cmd_init(args: argparse.Namespace) -> int:
    feature = feature_path(work_root(args.root), args.slug)
    if feature.path.exists() and not args.force:
        raise SystemExit(f"уже есть: {feature.path}")
    feature.path.mkdir(parents=True, exist_ok=True)
    for name in ("project.md", "inputs.md", "answers.md"):
        target = feature.path / name
        if args.force or not target.exists():
            shutil.copyfile(TEMPLATES / name, target)
    meta = {
        "slug": feature.slug,
        "title": args.title or feature.slug,
        "created": date.today().isoformat(),
        "source": "https://habr.com/ru/articles/1098788/",
        "skipped": {},
    }
    feature.save_meta(meta)
    print(feature.path)
    return 0


def cmd_skip(args: argparse.Namespace) -> int:
    feature = feature_path(work_root(args.root), args.slug)
    require_feature(feature)
    if args.step not in BY_ID:
        raise SystemExit("шаг: 01 … 08")
    meta = feature.meta()
    meta.setdefault("skipped", {})[args.step] = args.reason.strip()
    feature.save_meta(meta)
    print(f"{args.step} пропущен: {args.reason.strip()}")
    return 0


def block(title: str, body: str) -> str:
    body = body.strip()
    if not body:
        return f"<{title}>\n(пусто)\n"
    return f"<{title}>\n{body}\n"


def cmd_prompt(args: argparse.Namespace) -> int:
    feature = feature_path(work_root(args.root), args.slug)
    require_feature(feature)
    step = BY_ID.get(args.step)
    if not step:
        raise SystemExit("шаг: 01 … 08")
    reason = skipped_reason(feature, step["id"])
    if reason and not args.force:
        raise SystemExit(f"шаг {step['id']} выключен: {reason}. Снимите пропуск или передайте --force.")

    missing = []
    carried = []
    for need in step["needs"]:
        why = skipped_reason(feature, need)
        if why:
            carried.append(f"Шаг {need} ({BY_ID[need]['title']}) пропущен: {why}")
            continue
        body = artifact(feature, need)
        if not body:
            missing.append(need)
        else:
            carried.append(block(f"{BY_ID[need]['title']} — результат шага {need}", body))
    if missing:
        raise SystemExit(
            "нет артефактов шагов: " + ", ".join(missing) + ". Сначала сохраните их или явно пропустите."
        )

    project = read(feature.path / "project.md")
    inputs = read(feature.path / "inputs.md")
    answers = read(feature.path / "answers.md")
    prior = []
    for need in step["needs"]:
        if skipped_reason(feature, need):
            continue
        for item in open_questions(artifact(feature, need)):
            prior.append(f"- [{need}] {item}")

    custom = section(inputs, "Задача шага")
    task = custom or step["task"]
    user = "\n".join(
        [
            block("ПРОЕКТ", project),
            block("БРИФ ФИЧИ", inputs),
            block("ОТВЕТЫ НА ОТКРЫТЫЕ ВОПРОСЫ", answers),
            block(
                "НЕЗАКРЫТЫЕ ВОПРОСЫ ПРЕДЫДУЩИХ ШАГОВ",
                "\n".join(prior) if prior else "Нет перенесённых вопросов.",
            ),
            "Не закрывай эти вопросы догадкой. Если ответ уже есть во входных данных — сошлись на него. Иначе перенеси вопрос в ОТКРЫТЫЕ ВОПРОСЫ этого шага.",
            "",
            *carried,
            "",
            "ЗАДАЧА",
            task,
            "",
        ]
    )
    system = (PROMPTS / step["system"]).read_text(encoding="utf-8").strip()
    save_hint = "\n".join(str(feature.path / name) for name in step["files"])
    text = (
        f"# saflow {step['id']} {step['slug']}\n"
        f"# фича: {feature.slug}\n"
        "# Контекст этого шага собран ниже. Не подмешивай прошлый диалог.\n\n"
        "## SYSTEM\n\n"
        f"{system}\n\n"
        "## USER\n\n"
        f"{user}\n"
        "## SAVE\n\n"
        f"{save_hint}\n"
    )
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(args.out)
    else:
        print(text, end="")
    return 0


def problems(feature: Feature, step_id: str) -> tuple[list[str], list[str]]:
    step = BY_ID[step_id]
    errors: list[str] = []
    warnings: list[str] = []
    chunks = [(name, read(feature.path / name)) for name in step["files"]]
    for name, body in chunks:
        if not body.strip():
            errors.append(f"пустой файл {name}")
    text = "\n\n".join(body for _, body in chunks)
    if step_id != "07":
        notes = text
    else:
        notes = read(feature.path / "07-notes.md")
        if not has_open_block(notes):
            errors.append("в 07-notes.md нет блока «ОТКРЫТЫЕ ВОПРОСЫ»")
    if step_id != "07" and text.strip() and not has_open_block(text):
        errors.append("нет блока «ОТКРЫТЫЕ ВОПРОСЫ»")

    project = read(feature.path / "project.md")
    if step_id == "01":
        if not re.search(r"(?is)как\s+.+?я хочу\s+.+?чтобы\s+", text):
            errors.append("нет истории «Как …, я хочу …, чтобы …»")
        scenarios = len(re.findall(r"(?i)\b(дано|given)\b", text))
        if scenarios < 3:
            errors.append(f"сценариев Дано/Given: {scenarios}, нужно минимум 3")
        if not re.search(r"(?i)вне области", text):
            errors.append("нет блока «Вне области задач»")
        for word in ("некоторая сумма", "любой статус", "корректно"):
            if re.search(word, text, re.I):
                errors.append(f"запрещённая размытая формулировка: {word}")
    elif step_id == "02":
        for title in (
            "область",
            "действующ",
            "предуслов",
            "минимальн",
            "гарантии успеха",
            "триггер",
            "основной сценарий",
            "расширен",
        ):
            if not re.search(title, text, re.I):
                errors.append(f"нет раздела use case: {title}")
        if not re.search(r"(?i)\d+[a-z]|расширений нет", text):
            errors.append("нет расширений (3a, 5a, …) и нет явного «расширений нет»")
    elif step_id == "03":
        for mark in ("[существует]", "[+поле]", "[новая]"):
            if mark not in text:
                warnings.append(f"нет метки {mark} — так и задумано, только если таких сущностей нет")
        if not any(mark in text for mark in ("[существует]", "[+поле]", "[новая]")):
            errors.append("нет ни одной метки сущности")
        if not re.search(r"(?i)решени[яе].*альтернатив", text):
            errors.append("нет блока «Решения и альтернативы»")
        if not re.search(r"\b[01]\.\.[1*nN]\b|\b1:N\b|\bN:1\b|\b1:1\b|\bN:M\b", text):
            warnings.append("не вижу кардинальности связей")
    elif step_id == "04":
        if not re.search(r"(?i)\bTable\b", text):
            errors.append("в DBML нет Table")
        if not re.search(r"(?i)\bRef\b|связей нет", text):
            errors.append("нет Ref и нет явного «связей нет»")
        if not re.search(r"(?i)миграц", text):
            errors.append("нет блока «Миграция»")
        if not re.search(r"(?i)проверить у dba|dba", text):
            errors.append("нет блока «Проверить у DBA»")
    elif step_id == "05":
        if not re.search(r"(?i)оценка охвата", text):
            errors.append("нет блока «Оценка охвата»")
        known = [name.lower() for name in services(project)]
        if not known:
            warnings.append("в project.md пустой раздел «Сервисы» — проверить выдуманные зоны нельзя")
        else:
            for line in text.splitlines():
                if "требуется решение архитектора" in line.lower():
                    continue
                hit = re.search(r"(?i)новый сервис\s+[«\"]?([A-Za-zА-Яа-я0-9_-]+)", line)
                if hit and hit.group(1).lower() not in known:
                    errors.append(f"похоже на новый сервис, которого нет во входе: {hit.group(1)}")
    elif step_id == "06":
        if "@startuml" not in text or "@enduml" not in text:
            errors.append("PlantUML должен начинаться с @startuml и заканчиваться @enduml")
        if not re.search(r"(?i)\balt\b", text):
            warnings.append("нет alt: расширения сценария могут быть не показаны")
        declared = section(project, "Участники диаграммы")
        if "participant" not in declared and "actor" not in declared:
            warnings.append("в project.md не заданы участники диаграммы")
        else:
            allowed = set(re.findall(r'(?i)(?:actor|participant|queue|database)\s+"?([^"\n]+?)"?\s+as\s+(\w+)', declared))
            aliases = {alias for _, alias in allowed}
            for alias in re.findall(r"\b([A-Z][A-Za-z0-9_]*)\s*(?:->|-->|->>)", text):
                if alias not in aliases and alias != "???":
                    warnings.append(f"участник {alias} не из списка проекта")
    elif step_id == "07":
        openapi = read(feature.path / "07-openapi.yaml")
        asyncapi = read(feature.path / "07-asyncapi.yaml")
        if not re.search(r"(?m)^openapi:\s*3", openapi):
            errors.append("07-openapi.yaml должен начинать контракт с openapi: 3")
        if not re.search(r"(?m)^asyncapi:\s*3", asyncapi):
            errors.append("07-asyncapi.yaml должен содержать asyncapi: 3")
        if re.search(r"(?im)^\s*(post|put|patch|delete):", openapi) and "Idempotency-Key" not in openapi:
            errors.append("операция меняет состояние, но нет Idempotency-Key")
        if "СОБЫТИЙ НЕТ" not in asyncapi and "channels:" not in asyncapi:
            errors.append("AsyncAPI без каналов и без пометки «СОБЫТИЙ НЕТ»")
    elif step_id == "08":
        if not re.search(r"(?i)покрытие", text):
            errors.append("нет блока «Покрытие»")
        if not re.search(r"(?i)порядок", text):
            errors.append("нет блока «Порядок»")
        points = re.findall(r"(?i)(?:story points|sp|оценка)\s*[:—-]?\s*([0-9]+)", text)
        bad = [p for p in points if p not in {"1", "2", "3", "5", "8"}]
        if not points:
            errors.append("нет оценок в story points")
        if bad:
            errors.append("оценки вне шкалы 1, 2, 3, 5, 8: " + ", ".join(bad))
        if not re.search(r"(?i)UC-|US-|DBML|OpenAPI|AsyncAPI|критер", text):
            errors.append("задачи не ссылаются на артефакты")
    return errors, warnings


def cmd_validate(args: argparse.Namespace) -> int:
    feature = feature_path(work_root(args.root), args.slug)
    require_feature(feature)
    ids = [args.step] if args.step else [step["id"] for step in STEPS]
    failed = False
    for step_id in ids:
        reason = skipped_reason(feature, step_id)
        if reason:
            print(f"{step_id} пропущен: {reason}")
            continue
        if not artifact(feature, step_id):
            print(f"{step_id} ещё не заполнен")
            continue
        errors, warnings = problems(feature, step_id)
        for message in warnings:
            print(f"{step_id} предупреждение: {message}")
        if errors:
            failed = True
            for message in errors:
                print(f"{step_id} ошибка: {message}")
        elif not warnings:
            print(f"{step_id} ок")
        else:
            print(f"{step_id} ок с предупреждениями")
    return 1 if failed else 0


def cmd_save(args: argparse.Namespace) -> int:
    feature = feature_path(work_root(args.root), args.slug)
    require_feature(feature)
    step = BY_ID.get(args.step)
    if not step:
        raise SystemExit("шаг: 01 … 08")
    if len(step["files"]) != 1 and not args.file:
        raise SystemExit(
            "у этого шага несколько файлов. Запишите их сами и запустите validate. Файлы:\n"
            + "\n".join(str(feature.path / name) for name in step["files"])
        )
    if args.file:
        body = Path(args.file).read_text(encoding="utf-8")
    else:
        body = sys.stdin.read()
    target = feature.path / step["files"][0]
    target.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")
    print(target)
    errors, warnings = problems(feature, step["id"])
    for message in warnings:
        print(f"предупреждение: {message}")
    for message in errors:
        print(f"ошибка: {message}")
    if errors and not args.force:
        return 1
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    feature = feature_path(work_root(args.root), args.slug)
    require_feature(feature)
    meta = feature.meta()
    print(f"{meta.get('title', feature.slug)}  ({feature.path})")
    for step in STEPS:
        reason = skipped_reason(feature, step["id"])
        if reason:
            state = f"пропущен — {reason}"
        elif not artifact(feature, step["id"]):
            state = "пусто"
        else:
            errors, warnings = problems(feature, step["id"])
            if errors:
                state = f"ошибки: {len(errors)}"
            elif warnings:
                state = f"готово, предупреждения: {len(warnings)}"
            else:
                state = "готово"
        print(f"  {step['id']} {step['title']:<24} {state}")
    questions = []
    for step in STEPS:
        if skipped_reason(feature, step["id"]):
            continue
        for item in open_questions(artifact(feature, step["id"])):
            questions.append(f"[{step['id']}] {item}")
    print(f"открытых вопросов: {len(questions)}")
    for item in questions:
        print(f"  {item}")
    return 0


def cmd_questions(args: argparse.Namespace) -> int:
    return cmd_status(args)


def cmd_bundle(args: argparse.Namespace) -> int:
    feature = feature_path(work_root(args.root), args.slug)
    require_feature(feature)
    parts = [f"# {feature.meta().get('title', feature.slug)}\n"]
    for name in ("project.md", "inputs.md", "answers.md"):
        parts.append(f"\n\n# {name}\n\n")
        parts.append(read(feature.path / name))
    for step in STEPS:
        reason = skipped_reason(feature, step["id"])
        parts.append(f"\n\n# {step['id']} {step['title']}\n\n")
        if reason:
            parts.append(f"Пропущен: {reason}\n")
            continue
        body = artifact(feature, step["id"])
        parts.append(body or "(пусто)\n")
    target = feature.path / "bundle.md"
    target.write_text("".join(parts), encoding="utf-8")
    print(target)
    return 0


def cmd_card(args: argparse.Namespace) -> int:
    cards = ROOT / "cards"
    raw = (args.step or "").strip().lower()
    if raw in {"", "index", "list"}:
        print((cards / "index.md").read_text(encoding="utf-8"), end="")
        return 0
    number = raw.zfill(2) if raw.isdigit() else raw
    path = cards / f"{number}.md"
    if not path.exists():
        raise SystemExit("карточка: 00 … 08")
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def cmd_example(args: argparse.Namespace) -> int:
    slug = args.slug or "repeat-order"
    ns = argparse.Namespace(root=args.root, slug=slug, title="Повторить заказ", force=args.force)
    cmd_init(ns)
    feature = feature_path(work_root(args.root), slug)
    (feature.path / "project.md").write_text(EXAMPLE_PROJECT, encoding="utf-8")
    (feature.path / "inputs.md").write_text(EXAMPLE_INPUTS, encoding="utf-8")
    print(feature.path)
    return 0


def selftest() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        base = [sys.executable, str(ROOT / "saflow.py")]
        tail = ["--root", str(root)]
        run(base + ["init", "demo", "--title", "Демо", *tail])
        feature = root / "demo"
        bad = feature / "01-user-story.md"
        bad.write_text("# Черновик\n\nСделать кнопку.\n\n## ОТКРЫТЫЕ ВОПРОСЫ\n- нет\n", encoding="utf-8")
        proc = subprocess.run(base + ["validate", "demo", "01", *tail], capture_output=True, text=True)
        if proc.returncode == 0:
            raise SystemExit("selftest: пустая история не должна проходить проверку")
        good = """# US-1 Повтор

Как клиент, я хочу повторить заказ 1001, чтобы не собирать те же позиции заново.

## Критерии

Дано заказ 1001 содержит «Молоко 1 л» за 89 рублей и товар в наличии в магазине 7.
Когда клиент подтверждает повтор заказа 1001.
Тогда в корзине магазина 7 лежит «Молоко 1 л» по цене 92 рубля.

Дано в заказе 1001 две позиции и «Хлеб» отсутствует в магазине 7.
Когда клиент подтверждает повтор.
Тогда «Молоко 1 л» попадает в корзину, а «Хлеб» показан как отсутствующий.

Дано сервис остатков магазина 7 не ответил за 2 секунды.
Когда клиент подтверждает повтор заказа 1001.
Тогда корзина остаётся прежней.

## Вне области задач

Повтор из карточки товара.

## ОТКРЫТЫЕ ВОПРОСЫ

- Кому: продакт. Повторяем ли отменённый заказ 1001?
"""
        proc = subprocess.run(base + ["save", "demo", "01", *tail], input=good, capture_output=True, text=True)
        if proc.returncode != 0:
            raise SystemExit("selftest: годная история отклонена\n" + proc.stdout + proc.stderr)
        proc = subprocess.run(base + ["prompt", "demo", "02", *tail], capture_output=True, text=True)
        if proc.returncode != 0 or "US-1 Повтор" not in proc.stdout or "ПРОЕКТ" not in proc.stdout:
            raise SystemExit("selftest: промпт шага 2 не содержит историю")
        if "отменённый заказ" not in proc.stdout:
            raise SystemExit("selftest: открытый вопрос не перенесён")
        proc = subprocess.run(base + ["prompt", "demo", "03", *tail], capture_output=True, text=True)
        if proc.returncode == 0:
            raise SystemExit("selftest: шаг 3 не должен собираться без use case")
        run(base + ["skip", "demo", "02", "--reason", "поведение уже зафиксировано снаружи", *tail])
        proc = subprocess.run(base + ["prompt", "demo", "03", *tail], capture_output=True, text=True)
        if proc.returncode != 0 or "пропущен" not in proc.stdout:
            raise SystemExit("selftest: пропуск шага не попал в следующий промпт")
        proc = subprocess.run(base + ["card", "02"], capture_output=True, text=True)
        if proc.returncode != 0 or "четыре вопроса" not in proc.stdout.lower() and "Расширения нумеровать" not in proc.stdout:
            raise SystemExit("selftest: карточка 02 не открывается\n" + proc.stdout)
        proc = subprocess.run(base + ["card"], capture_output=True, text=True)
        if proc.returncode != 0 or "08" not in proc.stdout:
            raise SystemExit("selftest: оглавление карточек пустое")
    print("selftest ок")
    return 0


def run(argv: list[str]) -> None:
    proc = subprocess.run(argv, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(proc.stdout + proc.stderr)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="каталог фич, по умолчанию ./saflow-work")
    parser = argparse.ArgumentParser(
        prog="saflow",
        description="Конвейер системного анализа из восьми артефактов",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", parents=[common], help="создать фичу из шаблона проекта")
    init.add_argument("slug")
    init.add_argument("--title", default="")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    skip = sub.add_parser("skip", parents=[common], help="явно выключить шаг, которого в системе нет")
    skip.add_argument("slug")
    skip.add_argument("step")
    skip.add_argument("--reason", required=True)
    skip.set_defaults(func=cmd_skip)

    prompt = sub.add_parser("prompt", parents=[common], help="собрать промпт шага из проекта и прошлых артефактов")
    prompt.add_argument("slug")
    prompt.add_argument("step")
    prompt.add_argument("--out")
    prompt.add_argument("--force", action="store_true")
    prompt.set_defaults(func=cmd_prompt)

    save = sub.add_parser("save", parents=[common], help="записать артефакт одиночного шага и проверить его")
    save.add_argument("slug")
    save.add_argument("step")
    save.add_argument("--file")
    save.add_argument("--force", action="store_true")
    save.set_defaults(func=cmd_save)

    validate = sub.add_parser("validate", parents=[common], help="проверить структурные правила шага")
    validate.add_argument("slug")
    validate.add_argument("step", nargs="?")
    validate.set_defaults(func=cmd_validate)

    status = sub.add_parser("status", parents=[common], help="состояние конвейера и открытые вопросы")
    status.add_argument("slug")
    status.set_defaults(func=cmd_status)

    questions = sub.add_parser("questions", parents=[common], help="то же, что status")
    questions.add_argument("slug")
    questions.set_defaults(func=cmd_questions)

    bundle = sub.add_parser("bundle", parents=[common], help="собрать все артефакты в один файл")
    bundle.add_argument("slug")
    bundle.set_defaults(func=cmd_bundle)

    card = sub.add_parser("card", parents=[common], help="открыть карточку справочника: 00 … 08")
    card.add_argument("step", nargs="?", default="")
    card.set_defaults(func=cmd_card)

    example = sub.add_parser("example", parents=[common], help="заготовка фичи «Повторить заказ» из статьи")
    example.add_argument("--slug", default="repeat-order")
    example.add_argument("--force", action="store_true")
    example.set_defaults(func=cmd_example)

    test = sub.add_parser("selftest", parents=[common], help="проверить сам инструмент")
    test.set_defaults(func=lambda args: selftest())
    return parser


EXAMPLE_PROJECT = """# Проект

Маркетплейс доставки продуктов. Сервис корзин собирает позиции, заказы и остатки лежат снаружи.

## Глоссарий

- Заказ — документ на момент оформления, состав после этого не меняется.
- Корзина — единственная корзина клиента в одном магазине.
- Повтор заказа — сборка новой корзины по составу прошлого заказа по актуальным ценам и остаткам.

## Как устроена система

Клиент работает через мобильное приложение. BFF собирает данные экрана. Корзиной владеет один сервис. Заказы, каталог и остатки — внешние синхронные вызовы. События уходят в Kafka, аналитика их читает.

## Сервисы

- Мобильное приложение — экраны клиента.
- BFF — агрегация данных для экранов.
- Сервис корзин — владеет корзиной и её позициями.
- Сервис заказов — владеет заказами, отдаёт состав.
- Каталог — цены по магазинам, статус товара.
- Остатки — наличие в магазине.
- Kafka — шина событий.
- Аналитика — читает события из брокера.

## Участники диаграммы

```
actor Клиент
participant "Сервис корзин" as Cart
participant "Сервис Заказов" as Ord
participant "Сервис Каталога" as Cat
participant "Сервис Остатков" as Stk
queue Kafka
participant "Сервис Аналитики" as Analyt
```

## Текущие сущности

Клиент, Магазин, Товар, Заказ, Позиция заказа, Корзина, Позиция корзины, Слот доставки, Платёж.

## Существующие таблицы

carts, cart_items.

## Соглашения по типам

- Идентификаторы — uuid.
- Деньги — bigint в копейках, суффикс поля _kopecks.
- Количество — numeric(10,3): бывают весовые товары.
- Даты — timestamptz, хранение в UTC.
- Перечисления — varchar с закрытым списком значений в комментарии.

## Соглашения по API

- Ошибки — RFC 7807, application/problem+json, плюс поле code из enum.
- Идемпотентность — заголовок Idempotency-Key для всех операций, изменяющих состояние.
- Деньги в API — строка с двумя знаками + отдельное поле currency.
- События — CloudEvents 1.0, транспорт Kafka, имя топика polka.<домен>.<событие>.v<версия>.

## Ограничения

- Состав заказа неизменяем.
- Корзина у клиента одна на магазин.
- Вызовы заказов, каталога и остатков из сервиса корзин синхронные.

## Применимость

- 01: да
- 02: да
- 03: да
- 04: да
- 05: да
- 06: да
- 07: да
- 08: да
"""

EXAMPLE_INPUTS = """# Бриф фичи

Кнопка «Повторить заказ» в сервисе доставки продуктов.

## Данные от заказчика

1. На странице заказа — кнопка «Повторить заказ».
2. По нажатию собирается такой же заказ.
3. Если чего-то нет в наличии — показать, чего нет.
4. Цены берём актуальные.
5. Готово, когда повтор оформляется в два клика.

## Известно дополнительно

- 38 % заказов — повторяющиеся наборы (данные аналитики за квартал).
- Повтор из карточки товара («купить ещё раз») — отдельная задача, в эту не входит.

## Ограничения этой фичи

- Состав заказа неизменяем: заказ — это документ на момент оформления.
- Корзина у клиента одна на магазин.
- Сервисы заказов, каталога и остатков — внешние по отношению к сервису корзин, вызовы синхронные.

## Задача шага
"""


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
