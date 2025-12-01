"""Telegram-бот для выгрузки расписания и .ics файлов."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import re
import tempfile
from io import BytesIO
from pathlib import Path
from typing import List, Tuple

import requests
from telegram import BotCommand, KeyboardButton, ReplyKeyboardMarkup, Update
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
TRIGGER_KEYWORDS = (
    "бот, кинь расписание",
    "бот кинь расписание",
    "бот, дай расписание",
    "бот дай расписание",
    "бот покажи расписание",
)
REPLY_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BUTTON_TEXT_WEEKLY), KeyboardButton(BUTTON_TEXT_ICS)],
        [KeyboardButton(BUTTON_TEXT_PLAN)],
    ],
    resize_keyboard=True,
)


def load_env_file(env_path: Path = Path(".env")) -> None:
    """Загружает пары ключ=значение из локального .env без дополнительных зависимостей."""

    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            logging.debug("Пропускаем строку без '=' в .env: %s", stripped)
            continue
        key, value = stripped.split("=", maxsplit=1)
        os.environ.setdefault(key.strip(), value.strip())


def get_default_params() -> Tuple[str, str]:
    """Возвращает URL и группу из переменных окружения или значений по умолчанию."""

    url = os.getenv(DEFAULT_URL_ENV, DEFAULT_URL)
    group = os.getenv(DEFAULT_GROUP_ENV, DEFAULT_GROUP)
    return url, group


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Приветственное сообщение с подсказками по командам."""

    if not update.message:
        return
    try:
        url, group = get_default_params()
        await update.message.reply_text(
            "Привет! Я бот для расписания. Доступные команды:\n"
            "• /week [url] [group] — показать расписание недели текстом."
            "\n• /ics [url] [group] — отправить .ics файлы (мобильный и Google)."
            "\n• /plan <YYYY-MM-DD> <HH:MM> [url] [group] — запланировать отправку текста."
            "\n• В группе можно написать: 'Бот, кинь расписание'."
            f"\nТекущие значения по умолчанию: URL={url}, группа={group}",
            reply_markup=REPLY_KEYBOARD,
        )
        logging.info("Пользователь запустил /start")
    except Exception as exc:
        logging.error("Ошибка при выполнении команды /start: %s", exc)


async def send_schedule_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Строит и отправляет .ics файлы для мобильного и Google календаря."""

    if not update.message:
        return
    url, group = resolve_args(context)
    try:
        events = await fetch_events_async(url, group)
    except Exception as exc:
        logging.error("Ошибка загрузки событий: %s", exc)
        await update.message.reply_text(
            "Ошибка загрузки расписания. Попробуйте позже."
        )
        return
    
    if not events:
        await update.message.reply_text(
            "Не удалось найти занятия для указанной группы. Проверьте URL и код группы."
        )
        return

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            mobile_path = Path(tmpdir) / f"schedule_{slugify_group_name(group)}.ics"
            google_path = Path(tmpdir) / f"schedule_{slugify_group_name(group)}_google.ics"
            build_ics(events, mobile_path, target="mobile")
            build_ics(events, google_path, target="google")

            # Читаем файлы в памяти перед удалением временной директории
            with mobile_path.open("rb") as f:
                mobile_data = f.read()
            with google_path.open("rb") as f:
                google_data = f.read()

        # Отправляем после закрытия temp директории
        from io import BytesIO
        await update.message.reply_document(
            document=BytesIO(mobile_data),
            filename=mobile_path.name,
        )
        await update.message.reply_document(
            document=BytesIO(google_data),
            filename=google_path.name,
        )
        logging.info("Файлы расписания отправлены для группы %s", group)
    except Exception as exc:
        logging.error("Ошибка при отправке файлов: %s", exc)
        await update.message.reply_text(
            "Ошибка при отправке файлов. Попробуйте позже."
        )


async def send_weekly_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет расписание недели текстом."""

    if not update.message:
        return
    try:
        url, group = resolve_args(context)
        events = await fetch_events_async(url, group)
        if not events:
            text = "На эту неделю занятий не найдено или расписание недоступно."
        else:
            text = format_weekly_schedule(events)
        await update.message.reply_text(text)
        logging.info("Расписание недели отправлено для группы %s", group)
    except Exception as exc:
        logging.error("Ошибка при отправке расписания: %s", exc)
        await update.message.reply_text(
            "Ошибка при загрузке расписания. Попробуйте позже."
        )


async def plan_scheduled_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Планирует отправку расписания на заданные дату и время (МСК)."""

    if not update.message:
        return
    if not context.args or len(context.args) < 2:
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
    chat_id = update.effective_chat.id if update.effective_chat else None
    
    if not chat_id:
        await update.message.reply_text(
            "Не удалось определить chat_id.",
            reply_markup=REPLY_KEYBOARD,
        )
        return
    
    try:
        job = context.job_queue.run_once(
            send_scheduled_text,
            when=run_at,
            chat_id=chat_id,
            data={"url": url, "group": group, "reference_date": run_at.date()},
            name=f"schedule-{chat_id}",
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
                f"для группы {group}."
            ),
            reply_markup=REPLY_KEYBOARD,
        )
        logging.info("Запланирована отправка для %s на %s", chat_id, run_at)
    except Exception as exc:
        logging.error("Ошибка при планировании отправки: %s", exc)
        await update.message.reply_text(
            "Ошибка при планировании отправки. Попробуйте позже.",
            reply_markup=REPLY_KEYBOARD,
        )


async def send_scheduled_text(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Колбек для отложенной отправки текстового расписания."""

    job = context.job
    if not job or job.chat_id is None:
        logging.warning("send_scheduled_text: job или chat_id не определены")
        return
    
    try:
        url = job.data.get("url") if job.data else DEFAULT_URL
        group = job.data.get("group") if job.data else DEFAULT_GROUP
        reference_date = job.data.get("reference_date") if job.data else None
        
        events = await fetch_events_async(url, group)
        text = format_weekly_schedule(events, reference_date=reference_date)
        await context.bot.send_message(chat_id=job.chat_id, text=text)
        logging.info("Плановое расписание отправлено в %s", job.chat_id)
    except Exception as exc:
        logging.error("Ошибка при отправке плановой рассылки: %s", exc)
        try:
            await context.bot.send_message(
                chat_id=job.chat_id,
                text="Ошибка при загрузке расписания. Попробуйте позже."
            )
        except Exception as err:
            logging.error("Ошибка при отправке сообщения об ошибке: %s", err)


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


def is_schedule_request(text: str, bot_username: str | None) -> bool:
    """Проверяет, содержит ли сообщение запрос расписания."""

    normalized = text.lower()
    if bot_username and f"@{bot_username.lower()}" in normalized:
        return True
    if "распис" in normalized:
        return True
    return any(keyword in normalized for keyword in TRIGGER_KEYWORDS)


async def setup_bot_commands(application: Application) -> None:
    """Регистрирует команды в меню бота."""

    commands = [
        BotCommand("start", "Вступление и примеры команд"),
        BotCommand("week", "Расписание недели текстом"),
        BotCommand("ics", "Скачать .ics файлы"),
        BotCommand("plan", "Запланировать отправку расписания"),
    ]
    await application.bot.set_my_commands(commands)


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
    if is_schedule_request(text, bot_username):
        await send_weekly_text(update, context)


def resolve_args(context: ContextTypes.DEFAULT_TYPE) -> Tuple[str, str]:
    """Определяет URL и код группы из аргументов команды или окружения."""

    url_default, group_default = get_default_params()
    args = context.args or []
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

    if not url or not group:
        logging.warning("fetch_events_async: пустые url или group")
        return []
    
    def _load() -> List[ScheduleEvent]:
        try:
            with requests.Session() as session:
                return fetch_events(url, group, session)
        except Exception as exc:
            logging.error("Ошибка в потоке загрузки: %s", exc)
            return []

    try:
        return await asyncio.to_thread(_load)
    except Exception as exc:
        logging.error("Не удалось загрузить расписание: %s", exc)
        return []


def build_application(token: str) -> Application:
    """Создаёт экземпляр Application с зарегистрированными хендлерами."""

    application = Application.builder().token(token).post_init(setup_bot_commands).build()
    
    # Регистрируем хендлеры команд
    application.add_handler(CommandHandler(["start", "help"], start))
    application.add_handler(CommandHandler(["schedule_files", "ics"], send_schedule_files))
    application.add_handler(CommandHandler(["schedule_text", "week"], send_weekly_text))
    application.add_handler(CommandHandler(["schedule_plan", "plan"], plan_scheduled_text))
    
    # Хендлер для кнопок меню
    application.add_handler(
        MessageHandler(
            filters.TEXT
            & filters.Regex(
                f"^({re.escape(BUTTON_TEXT_WEEKLY)}|{re.escape(BUTTON_TEXT_ICS)}|{re.escape(BUTTON_TEXT_PLAN)})$"
            ),
            handle_menu_buttons,
        )
    )
    
    # Хендлер для приватных сообщений
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            handle_private_text,
        )
    )
    
    # Хендлер для групповых сообщений
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND,
            handle_group_text,
        )
    )
    
    return application


async def main() -> None:
    """Точка входа для запуска бота."""

    load_env_file()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    token = os.getenv(TELEGRAM_TOKEN_ENV)
    if not token:
        msg = (
            f"Не задан токен. Установите переменную {TELEGRAM_TOKEN_ENV}="
            "<telegram_bot_token>"
        )
        logging.error(msg)
        raise SystemExit(msg)

    logging.info("Запуск Telegram-бота...")
    try:
        application = build_application(token)
        await application.initialize()
        await application.start()
        await application.bot.delete_webhook(drop_pending_updates=True)
        await application.updater.start_polling()
        await application.updater.wait_for_stop()
        await application.stop()
        await application.shutdown()
    except KeyboardInterrupt:
        logging.info("Бот остановлен пользователем")
    except Exception as exc:
        logging.error("Критическая ошибка: %s", exc)
        raise


if __name__ == "__main__":
    asyncio.run(main())
