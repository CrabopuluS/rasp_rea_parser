from __future__ import annotations

import logging
from datetime import datetime
from io import BytesIO
from typing import Iterable, Tuple
from urllib.parse import urlparse

from telegram import InputFile, ReplyKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CallbackContext,
    CommandHandler,
    ContextTypes,
    JobQueue,
    MessageHandler,
    filters,
)

from config import get_settings
from ics_builder import build_ics
from models import Lesson, WeekSchedule
from schedule_client import ScheduleClient
from time_utils import get_moscow_tz, now_moscow

LOGGER = logging.getLogger(__name__)

BUTTON_WEEK = "📅 Расписание недели"
BUTTON_ICS = "📂 Получить .ics"
BUTTON_PLAN = "⏰ Запланировать отправку"
REPLY_KEYBOARD = ReplyKeyboardMarkup(
    [[BUTTON_WEEK], [BUTTON_ICS], [BUTTON_PLAN]], resize_keyboard=True
)


async def post_init(application: Application) -> None:
    """Register bot commands and cache username for mention handling."""

    me = await application.bot.get_me()
    application.bot_data["username"] = me.username or ""
    await application.bot.set_my_commands(
        [
            ("start", "Показать справку"),
            ("week", "Расписание недели"),
            ("ics", "Получить .ics"),
            ("plan", "Запланировать отправку расписания"),
        ]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        settings = get_settings()
        message = (
            "Привет! Я помогу с расписанием РЭУ.\n"
            "Команды: /week, /ics, /plan <YYYY-MM-DD> <HH:MM>.\n"
            f"Значения по умолчанию: URL={settings.schedule_url}, группа={settings.schedule_group}."
        )
        await _safe_reply(update, context, message, reply_markup=REPLY_KEYBOARD)
    except Exception as exc:
        LOGGER.exception("start handler failed: %s", exc)
        await _safe_reply(update, context, "Не удалось показать приветствие, попробуйте позже.")


async def week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_week(update, context, context.args)


async def ics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_ics(update, context, context.args)


async def plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        args = context.args
        if len(args) < 2:
            await _safe_reply(
                update,
                context,
                "Используйте: /plan <YYYY-MM-DD> <HH:MM> [url] [group]",
            )
            return

        date_str, time_str, *tail = args
        schedule_dt = _parse_datetime(date_str, time_str)
        if schedule_dt <= now_moscow():
            await _safe_reply(update, context, "Укажите время в будущем (МСК).")
            return

        schedule_url, group = resolve_args(tail, context.application.bot_data)
        job_data = {"schedule_url": schedule_url, "group": group}
        job_queue: JobQueue = context.job_queue
        job_queue.run_once(
            callback=send_planned_schedule,
            when=schedule_dt,
            data=job_data,
            name=f"plan-{update.effective_chat.id}-{schedule_dt.isoformat()}",
            chat_id=update.effective_chat.id if update.effective_chat else None,
        )
        await _safe_reply(
            update,
            context,
            f"Запланировано на {schedule_dt.strftime('%d.%m.%Y %H:%M %Z')} для группы {group}.",
        )
    except Exception as exc:
        LOGGER.exception("plan handler failed: %s", exc)
        await _safe_reply(update, context, "Не удалось создать напоминание.")


async def send_planned_schedule(context: CallbackContext) -> None:
    job = context.job
    if job is None:
        return
    chat_id = job.chat_id
    data = job.data or {}
    schedule_url = str(data.get("schedule_url"))
    group = str(data.get("group"))
    await _dispatch_week_message(context, chat_id=chat_id, schedule_url=schedule_url, group=group)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        message = update.effective_message
        if message is None or not message.text:
            return
        text = message.text.strip()
        chat = update.effective_chat
        bot_username = str(context.application.bot_data.get("username", "")).lower()

        if chat and chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
            if bot_username and f"@{bot_username}" not in text.lower():
                return
            await _handle_week(update, context, [text])
            return

        if text == BUTTON_WEEK:
            await _handle_week(update, context, [])
        elif text == BUTTON_ICS:
            await _handle_ics(update, context, [])
        elif text == BUTTON_PLAN:
            await _safe_reply(
                update, context, "Используйте /plan <YYYY-MM-DD> <HH:MM> [url] [group]."
            )
        else:
            await _handle_week(update, context, [text])
    except Exception as exc:
        LOGGER.exception("text handler failed: %s", exc)
        await _safe_reply(update, context, "Не удалось обработать запрос.")


async def _handle_week(
    update: Update, context: ContextTypes.DEFAULT_TYPE, args: Iterable[str]
) -> None:
    try:
        schedule_url, group = resolve_args(args, context.application.bot_data)
        await _dispatch_week_message(
            context,
            chat_id=update.effective_chat.id if update.effective_chat else None,
            schedule_url=schedule_url,
            group=group,
            reply_markup=REPLY_KEYBOARD,
        )
    except Exception as exc:
        LOGGER.exception("week handler failed: %s", exc)
        await _safe_reply(update, context, "Не удалось получить расписание.")


async def _dispatch_week_message(
    context: CallbackContext,
    chat_id: int | None,
    schedule_url: str,
    group: str,
    reply_markup=None,
) -> None:
    if chat_id is None:
        return
    try:
        client = ScheduleClient(schedule_url)
        try:
            schedule = client.fetch_week_schedule(group)
        finally:
            client.close()
    except Exception as exc:
        LOGGER.error("Failed to fetch schedule: %s", exc)
        await context.bot.send_message(chat_id=chat_id, text="Ошибка при получении расписания.")
        return
    try:
        if not schedule.lessons:
            text = "Не удалось найти занятия по указанным параметрам."
        else:
            text = format_week_message(schedule)
        await context.bot.send_message(
            chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=ParseMode.HTML
        )
    except Exception as exc:
        LOGGER.error("Failed to send week message: %s", exc)
        await context.bot.send_message(chat_id=chat_id, text="Произошла ошибка при отправке расписания.")


async def _handle_ics(
    update: Update, context: ContextTypes.DEFAULT_TYPE, args: Iterable[str]
) -> None:
    try:
        schedule_url, group = resolve_args(args, context.application.bot_data)
        client = ScheduleClient(schedule_url)
        try:
            schedule = client.fetch_week_schedule(group)
        finally:
            client.close()

        if not schedule.lessons:
            await _safe_reply(update, context, "Не удалось построить .ics: расписание пусто.")
            return

        calendars = build_ics(schedule)
        await _send_ics_documents(update, calendars, schedule.group)
    except Exception as exc:
        LOGGER.exception("ics handler failed: %s", exc)
        await _safe_reply(update, context, "Не удалось подготовить .ics файлы.")


def resolve_args(args: Iterable[str], bot_data: dict) -> Tuple[str, str]:
    settings = get_settings()
    schedule_url = str(bot_data.get("default_url", settings.schedule_url))
    group = str(bot_data.get("default_group", settings.schedule_group))
    for token in args:
        token = token.strip()
        if not token:
            continue
        if _looks_like_url(token):
            schedule_url = token
        else:
            group = token
    return schedule_url, group


def _looks_like_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return bool(parsed.scheme and parsed.netloc)
    except Exception:
        return False


def _parse_datetime(date_str: str, time_str: str) -> datetime:
    tz = get_moscow_tz()
    combined = f"{date_str} {time_str}"
    for fmt in ("%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M"):
        try:
            naive = datetime.strptime(combined, fmt)
            return naive.replace(tzinfo=tz)
        except ValueError:
            continue
    raise ValueError("Неверный формат даты или времени")


def format_week_message(schedule: WeekSchedule) -> str:
    lines = [f"<b>Группа:</b> {schedule.group}"]
    for day, lessons in schedule.grouped_by_day().items():
        lines.append(f"\n<b>{day}</b>")
        for lesson in lessons:
            lines.append(_format_lesson(lesson))
    return "\n".join(lines)


def _format_lesson(lesson: Lesson) -> str:
    start_local = lesson.start.astimezone(get_moscow_tz())
    end_local = lesson.end.astimezone(get_moscow_tz())
    teacher = f", {lesson.teacher}" if lesson.teacher else ""
    room = f" ({lesson.room})" if lesson.room else ""
    return (
        f"• {start_local:%H:%M}-{end_local:%H:%M}: {lesson.title} "
        f"({lesson.lesson_type}{teacher}{room})"
    )


async def _send_ics_documents(update: Update, calendars: dict, group: str) -> None:
    message = update.effective_message
    if message is None:
        return
    mobile_bytes = calendars.get("mobile")
    google_bytes = calendars.get("google")
    if not mobile_bytes or not google_bytes:
        await message.reply_text("Не удалось сформировать файлы календаря.")
        return
    await message.reply_document(
        document=InputFile(BytesIO(mobile_bytes), filename=f"{group}-mobile.ics"),
        caption="Мобильный календарь (локальное время)",
    )
    await message.reply_document(
        document=InputFile(BytesIO(google_bytes), filename=f"{group}-google.ics"),
        caption="Google Calendar (UTC)",
    )


async def _safe_reply(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs
) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(text, **kwargs)
    elif update.effective_chat:
        await context.bot.send_message(update.effective_chat.id, text, **kwargs)


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    application = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .rate_limiter(AIORateLimiter())
        .post_init(post_init)
        .build()
    )
    application.bot_data["default_url"] = str(settings.schedule_url)
    application.bot_data["default_group"] = settings.schedule_group

    application.add_handler(CommandHandler(["start", "help"], start))
    application.add_handler(CommandHandler("week", week))
    application.add_handler(CommandHandler("ics", ics))
    application.add_handler(CommandHandler("plan", plan))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    application.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
