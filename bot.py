"""Telegram-бот для выгрузки расписания и .ics файлов."""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import List, Tuple

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from schedule_parser import (
    DEFAULT_GROUP,
    DEFAULT_URL,
    ScheduleEvent,
    build_ics,
    fetch_events,
    format_weekly_schedule,
    slugify_group_name,
)

TELEGRAM_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
DEFAULT_URL_ENV = "SCHEDULE_URL"
DEFAULT_GROUP_ENV = "SCHEDULE_GROUP"


def get_default_params() -> Tuple[str, str]:
    """Возвращает URL и группу из переменных окружения или значений по умолчанию."""

    url = os.getenv(DEFAULT_URL_ENV, DEFAULT_URL)
    group = os.getenv(DEFAULT_GROUP_ENV, DEFAULT_GROUP)
    return url, group


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Приветственное сообщение с подсказками по командам."""

    if not update.message:
        return
    url, group = get_default_params()
    await update.message.reply_text(
        "Доступные команды:\n"
        "• /schedule_files [url] [group] — отправить два .ics файла."\
        " Если параметры не переданы, используется URL по умолчанию."\
        "\n• /schedule_text [url] [group] — показать расписание недели текстом."\
        "\n• Сообщение с текстом ‘расписание’ — покажет расписание недели.\n"
        f"Текущие значения по умолчанию: URL={url}, группа={group}"
    )


async def send_schedule_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Строит и отправляет .ics файлы для мобильного и Google календаря."""

    if not update.message:
        return
    url, group = resolve_args(context)
    events = await fetch_events_async(url, group)
    if not events:
        await update.message.reply_text("Не удалось найти занятия для указанной группы.")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        mobile_path = Path(tmpdir) / f"schedule_{slugify_group_name(group)}.ics"
        google_path = Path(tmpdir) / f"schedule_{slugify_group_name(group)}_google.ics"
        build_ics(events, mobile_path, target="mobile")
        build_ics(events, google_path, target="google")

        await update.message.reply_document(
            document=mobile_path.open("rb"),
            filename=mobile_path.name,
        )
        await update.message.reply_document(
            document=google_path.open("rb"),
            filename=google_path.name,
        )


async def send_weekly_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет расписание недели текстом."""

    if not update.message:
        return
    url, group = resolve_args(context)
    events = await fetch_events_async(url, group)
    text = format_weekly_schedule(events)
    await update.message.reply_text(text)


async def reply_on_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Реагирует на упоминание слова 'расписание' в группе."""

    await send_weekly_text(update, context)


def resolve_args(context: ContextTypes.DEFAULT_TYPE) -> Tuple[str, str]:
    """Определяет URL и код группы из аргументов команды или окружения."""

    url_default, group_default = get_default_params()
    args = context.args
    if not args:
        return url_default, group_default
    if len(args) == 1:
        return args[0], group_default
    return args[0], args[1]


async def fetch_events_async(url: str, group: str) -> List[ScheduleEvent]:
    """Получает события в отдельном потоке, чтобы не блокировать бота."""

    def _load() -> List[ScheduleEvent]:
        with requests.Session() as session:
            return fetch_events(url, group, session)

    return await asyncio.to_thread(_load)


def build_application(token: str) -> Application:
    """Создаёт экземпляр Application с зарегистрированными хендлерами."""

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler(["start", "help"], start))
    application.add_handler(CommandHandler(["schedule_files", "ics"], send_schedule_files))
    application.add_handler(CommandHandler(["schedule_text", "week"], send_weekly_text))
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex("(?i)расписание"),
            reply_on_keyword,
        )
    )
    return application


def main() -> None:
    """Точка входа для запуска бота."""

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    token = os.getenv(TELEGRAM_TOKEN_ENV)
    if not token:
        msg = (
            f"Не задан токен. Установите переменную {TELEGRAM_TOKEN_ENV}="
            "<telegram_bot_token>"
        )
        raise SystemExit(msg)

    application = build_application(token)
    application.run_polling()


if __name__ == "__main__":
    main()
