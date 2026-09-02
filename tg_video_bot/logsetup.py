"""Единая настройка логов для домашних ботов.

Ставится одинаковым файлом во все боты — правишь здесь, копируешь остальным.

Три вещи, ради которых она вообще есть:

1. Логи переживают пересборку. `docker compose up -d --build` пересоздаёт
   контейнер, а json-файл с логами лежит внутри старого — и уезжает вместе с
   ним. Поэтому пишем ещё и в файл на томе `/data`.
2. Строки одной операции сшиты. У каждого нажатия свой короткий id, он же
   подмешивается во все строки, которые это нажатие породило, включая фоновые
   задачи на часы вперёд. `grep 3f9a1c02 bot.log` — вся цепочка.
3. Секреты не утекают. Токен бота лежит прямо в URL Telegram API, поэтому
   раньше приходилось глушить httpx целиком. Здесь он маскируется, и HTTP-слой
   можно смотреть.
"""

from __future__ import annotations

import contextvars
import logging
import logging.handlers
import os
import re
import uuid
from pathlib import Path

# Кто и что сейчас делает. Держим в contextvars, а не таскаем аргументом: до
# места, где реально пишется строка, аргумент пришлось бы протаскивать через
# десяток функций.
_trace: contextvars.ContextVar[str] = contextvars.ContextVar("trace", default="-")
_actor: contextvars.ContextVar[str] = contextvars.ContextVar("actor", default="-")

DEFAULT_LEVEL = "DEBUG"
MAX_BYTES = 20 * 1024 * 1024
BACKUPS = 10                      # 200 МБ на бота — потолок, до которого расти

# Телеграмный токен (`123456:AA...`), Bearer, `token=`/`api_key=` в строке
# запроса и длинный ssh/base64-хвост. Маскируем формат, а не конкретные
# значения: секреты приходят и из чужого кода, которого мы не контролируем.
_SECRETS = re.compile(
    r"\d{6,}:[A-Za-z0-9_-]{30,}"
    r"|Bearer\s+[A-Za-z0-9._\-]{20,}"
    r"|(?<=token=)[^&\s\"']{6,}"
    r"|(?<=api_key=)[^&\s\"']{6,}",
    re.IGNORECASE,
)

# Длинный опрос Telegram шлёт getUpdates каждые несколько секунд. На DEBUG это
# десятки тысяч одинаковых строк в сутки, в которых тонет всё остальное.
_NOISE = re.compile(r"getUpdates|getMe\b|api\.telegram\.org/bot[^/]+/get")


def mask(text: str) -> str:
    """Заменить всё, похожее на секрет, на «…»."""
    return _SECRETS.sub("<скрыто>", text)


def short(value: object, limit: int = 600) -> str:
    """Значение для лога: одной строкой и с обрезкой хвоста."""
    text = value if isinstance(value, str) else repr(value)
    text = " ".join(text.split())
    if len(text) > limit:
        text = f"{text[:limit]}…(+{len(text) - limit})"
    return mask(text)


def new_trace(actor: object | None = None) -> str:
    """Начать новую операцию и вернуть её id."""
    tid = uuid.uuid4().hex[:8]
    _trace.set(tid)
    if actor is not None:
        _actor.set(str(actor))
    return tid


def current_trace() -> str:
    return _trace.get()


def adopt_trace(tid: str, actor: object | None = None) -> None:
    """Продолжить чужую операцию — для фоновых задач, отпочковавшихся от неё."""
    _trace.set(tid)
    if actor is not None:
        _actor.set(str(actor))


class _Context(logging.Filter):
    """Подмешать id операции и владельца, заодно замаскировать секреты."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace = _trace.get()
        record.actor = _actor.get()
        try:
            text = record.getMessage()
        except Exception:            # кривой формат — пусть ругается форматтер
            return True
        # Проверять шум надо по собранной строке: httpx кладёт URL не в msg,
        # а в args, и по сырому шаблону getUpdates не видно.
        if _NOISE.search(text):
            return False
        record.msg = mask(text)
        record.args = ()
        return True


def setup(name: str, data_dir: str | None = None, level: str | None = None) -> logging.Logger:
    """Собрать логирование: stdout + файл на томе. Вызывать один раз при старте."""
    level = (level or os.environ.get("LOG_LEVEL") or DEFAULT_LEVEL).upper()
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s [%(trace)s %(actor)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    ctx = _Context()

    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.DEBUG))
    for old in list(root.handlers):
        root.removeHandler(old)

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    stream.addFilter(ctx)
    root.addHandler(stream)

    target = Path(data_dir or os.environ.get("DATA_DIR") or "/data") / "logs"
    try:
        target.mkdir(parents=True, exist_ok=True)
        rotating = logging.handlers.RotatingFileHandler(
            target / f"{name}.log", maxBytes=MAX_BYTES, backupCount=BACKUPS,
            encoding="utf-8",
        )
        rotating.setFormatter(fmt)
        rotating.addFilter(ctx)
        root.addHandler(rotating)
    except OSError as exc:
        # Тома может не быть — бот всё равно должен подняться, просто без файла.
        root.warning("файловый лог недоступен (%s): пишу только в stdout", exc)

    # У httpx и telegram на INFO строка — это ровно «какой запрос ушёл и что
    # ответили», нам того и надо. На DEBUG они сыплют своими внутренностями
    # (кадры HTTP/2, состояния пула), и в этом тонет всё остальное.
    for noisy, cap in (("httpx", logging.INFO), ("telegram", logging.INFO),
                       ("urllib3", logging.INFO), ("PIL", logging.INFO),
                       ("apscheduler", logging.INFO),
                       ("httpcore", logging.WARNING), ("hpack", logging.WARNING),
                       ("asyncio", logging.WARNING)):
        logging.getLogger(noisy).setLevel(cap)

    log = logging.getLogger(name)
    log.info("логи: уровень %s, файл %s (%d×%d МБ)", level, target / f"{name}.log",
             BACKUPS + 1, MAX_BYTES // 1024 // 1024)
    return log
