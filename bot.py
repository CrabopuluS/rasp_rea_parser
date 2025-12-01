"""Telegram-бот для выгрузки расписания и .ics файлов."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import tempfile
from pathlib import Path
from typing import List, Tuple

import requests
from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from schedule_parser import (
    DEFAULT_GROUP,
    DEFAULT_URL,
    MOSCOW_TZ,
    ScheduleEvent,
    build_ics,
    fetch_events,
    format_weekly_schedule,
    slugify_group_name,
)

TELEGRAM_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
DEFAULT_URL_ENV = "SCHEDULE_URL"
DEFAULT_GROUP_ENV = "SCHEDULE_GROUP"
BUTTON_TEXT_WEEKLY = "📅 Расписание недели"
BUTTON_TEXT_ICS = "📂 Получить .ics"
BUTTON_TEXT_PLAN = "⏰ Запланировать отправку"
REPLY_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BUTTON_TEXT_WEEKLY), KeyboardButton(BUTTON_TEXT_ICS)],
        [KeyboardButton(BUTTON_TEXT_PLAN)],
    ],
    resize_keyboard=True,
)


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
        "• /ics [url] [group] — отправить два .ics файла (мобильный и Google)."\
        "\n• /week [url] [group] — показать расписание недели текстом."\
        "\n• /plan <YYYY-MM-DD> <HH:MM> [url] [group] — запланировать отправку текста."\
        "\n• Просто напишите боту любое сообщение в личке — он вернет расписание."
        f"Текущие значения по умолчанию: URL={url}, группа={group}",
        reply_markup=REPLY_KEYBOARD,
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


async def plan_scheduled_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Планирует отправку расписания на заданные дату и время (МСК)."""

    if not update.message:
        return
    if len(context.args) < 2:
        await update.message.reply_text(
            "Укажите дату и время: /schedule_plan YYYY-MM-DD HH:MM [url] [group]",
            reply_markup=REPLY_KEYBOARD,
        )
        return

    date_arg, time_arg, *rest = context.args
    run_at = parse_schedule_datetime(date_arg, time_arg)
    if not run_at:
        await update.message.reply_text(
            "Неверный формат. Дата YYYY-MM-DD, время HH:MM (24ч).",
            reply_markup=REPLY_KEYBOARD,
        )
        return
    now = dt.datetime.now(tz=MOSCOW_TZ)
    if run_at <= now:
        await update.message.reply_text(
            "Время должно быть в будущем относительно московского времени.",
            reply_markup=REPLY_KEYBOARD,
        )
        return

    url, group = resolve_scheduled_args(rest)
    job = context.job_queue.run_once(
        send_scheduled_text,
        when=run_at,
        chat_id=update.effective_chat.id if update.effective_chat else None,
        data={"url": url, "group": group, "reference_date": run_at.date()},
        name=f"schedule-{update.effective_chat.id if update.effective_chat else 'chat'}",
    )

    if not job:
        await update.message.reply_text(
            "Не удалось запланировать отправку, попробуйте позже.",
            reply_markup=REPLY_KEYBOARD,
        )
        return

    await update.message.reply_text(
        (
            "Плановая отправка настроена. Расписание будет отправлено "
            f"{run_at.strftime('%d.%m.%Y %H:%M %Z')} "
            f"для группы {group} по адресу {url}."
        ),
        reply_markup=REPLY_KEYBOARD,
    )


async def send_scheduled_text(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Колбек для отложенной отправки текстового расписания."""

    job = context.job
    if not job or job.chat_id is None:
        return
    url = job.data.get("url") if job.data else DEFAULT_URL
    group = job.data.get("group") if job.data else DEFAULT_GROUP
    reference_date = job.data.get("reference_date") if job.data else None
    events = await fetch_events_async(url, group)
    text = format_weekly_schedule(events, reference_date=reference_date)
    await context.bot.send_message(chat_id=job.chat_id, text=text)


async def handle_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает нажатия кнопок основного меню."""

    if not update.message:
        return
    if update.message.text == BUTTON_TEXT_WEEKLY:
        await send_weekly_text(update, context)
    elif update.message.text == BUTTON_TEXT_ICS:
        await send_schedule_files(update, context)
    elif update.message.text == BUTTON_TEXT_PLAN:
        await update.message.reply_text(
            "Используйте команду /schedule_plan <YYYY-MM-DD> <HH:MM> [url] [group]"
            " для плановой отправки текстового расписания. Время — московское.",
            reply_markup=REPLY_KEYBOARD,
        )


async def handle_private_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отвечает на произвольные сообщения в личных чатах расписанием или файлами."""

    if not update.message:
        return
    text = update.message.text.lower()
    if "ics" in text or "файл" in text or ".ics" in text:
        await send_schedule_files(update, context)
    else:
        await send_weekly_text(update, context)


async def handle_group_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отвечает на упоминания или запросы в групповых чатах."""

    if not update.message or not update.message.text:
        return
    text = update.message.text.lower()
    bot_username = (context.bot.username or "").lower()
    if "распис" in text or (bot_username and f"@{bot_username}" in text):
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


def resolve_scheduled_args(args: List[str]) -> Tuple[str, str]:
    """Определяет URL и группу для запланированной отправки."""

    url_default, group_default = get_default_params()
    if not args:
        return url_default, group_default
    if len(args) == 1:
        return args[0], group_default
    return args[0], args[1]


def parse_schedule_datetime(date_arg: str, time_arg: str) -> dt.datetime | None:
    """Парсит дату и время (МСК) из аргументов команды."""

    try:
        date_part = dt.datetime.strptime(date_arg, "%Y-%m-%d").date()
        time_part = dt.datetime.strptime(time_arg, "%H:%M").time()
    except ValueError:
        return None
    return dt.datetime.combine(date_part, time_part, tzinfo=MOSCOW_TZ)


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
    application.add_handler(CommandHandler(["schedule_plan", "plan"], plan_scheduled_text))
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            handle_private_text,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND,
            handle_group_text,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT
            & filters.Regex(
                f"^({BUTTON_TEXT_WEEKLY}|{BUTTON_TEXT_ICS}|{BUTTON_TEXT_PLAN})$"
            ),
            handle_menu_buttons,
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

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        application.run_polling()
    finally:
        loop.close()


if __name__ == "__main__":
    main()
