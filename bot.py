import asyncio
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = (
    os.getenv("BOT_TOKEN")
    or os.getenv("TELEGRAM_BOT_TOKEN")
    or ""
).strip()
ADMIN_IDS = {
    int(value.strip())
    for value in (os.getenv("ADMIN_IDS") or os.getenv("TELEGRAM_ADMIN_IDS") or "").split(",")
    if value.strip().isdigit()
}
DB_PATH = BASE_DIR / os.getenv("DATABASE_PATH", "applications.db")
DMTEAM_INVITE_LINK = "https://t.me/+5nF4AI5VUUphZGMy"
RULES_LINK = "https://t.me/wwerytq/91"
BOT_TOKEN_PATTERN = re.compile(r"^\d{8,12}:[A-Za-z0-9_-]{30,}$")


class ApplicationForm(StatesGroup):
    name = State()
    age = State()
    skin_photo = State()


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT NOT NULL,
                age INTEGER NOT NULL,
                skin_file_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                blocked INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(applications)").fetchall()
        }
        if "blocked" not in columns:
            connection.execute(
                "ALTER TABLE applications ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0"
            )


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_blocked(user_id: int) -> bool:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM applications WHERE user_id = ? AND blocked = 1 LIMIT 1",
            (user_id,),
        ).fetchone()
    return row is not None


def moderation_keyboard(application_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Принять", callback_data=f"application:accept:{application_id}"),
                InlineKeyboardButton(text="Отклонить", callback_data=f"application:reject:{application_id}"),
            ],
            [
                InlineKeyboardButton(
                    text="Отклонить и заблокировать",
                    callback_data=f"application:reject_block:{application_id}",
                ),
                InlineKeyboardButton(text="Заблокировать", callback_data=f"application:block:{application_id}"),
            ],
        ]
    )


def application_start_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Заполнить анкету")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def rules_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть правила", url=RULES_LINK)],
        ]
    )


def admin_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Проверить анкеты")],
            [KeyboardButton(text="Заблокированные")],
        ],
        resize_keyboard=True,
    )


def view_keyboard(application_id: int, status: str) -> InlineKeyboardMarkup | None:
    if status != "pending":
        return None
    return moderation_keyboard(application_id)


def format_application(application: sqlite3.Row) -> str:
    username = f"@{application['username']}" if application["username"] else "не указан"
    return (
        f"Анкета #{application['id']}\n"
        f"Имя: {application['full_name']}\n"
        f"Возраст: {application['age']}\n"
        f"Telegram: {username}\n"
        f"User ID: {application['user_id']}\n"
        f"Статус: {application['status']}\n"
        f"Блокировка: {'да' if application['blocked'] else 'нет'}\n"
        f"Дата: {application['created_at']}"
    )


async def send_application_to_admins(bot: Bot, application: sqlite3.Row) -> None:
    caption = "Новая анкета Void legion\n\n" + format_application(application)
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_photo(
                chat_id=admin_id,
                photo=application["skin_file_id"],
                caption=caption,
                reply_markup=moderation_keyboard(application["id"]),
            )
        except Exception:
            logging.exception("Не удалось отправить анкету админу %s", admin_id)


router = Dispatcher()


@router.message(CommandStart())
async def command_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    if is_admin(message.from_user.id):
        await message.answer(
            "Ya nasral v code\n"
            "Панель доступна только администраторам бота.\n"
            "Пример анкеты для участника:\n"
            "1) Имя\n"
            "2) Возраст\n"
            "3) Фото скина\n\n"
            "Нажми кнопку, чтобы посмотреть ожидающие анкеты.\n"
            "Если обнаружишь баг или ошибку, пиши @Mroboting — кодер 24/7.",
            reply_markup=admin_keyboard(),
        )
        return

    if is_blocked(message.from_user.id):
        await message.answer(
            "Доступ к боту заблокирован.\n"
            "Если это ошибка, напиши администратору Void legion."
        )
        return

    await message.answer(
        "Перед заполнением анкеты ознакомься с правилами Void legion:\n"
        f"{RULES_LINK}",
        reply_markup=rules_keyboard(),
    )
    await message.answer(
        "Привет! Это бот приёма анкет в Void legion.\n\n"
        "Нажми кнопку ниже, чтобы заполнить анкету.\n\n"
        "1) Имя\n"
        "2) Возраст\n"
        "3) Фото скина",
        reply_markup=application_start_keyboard(),
    )


@router.message(F.text == "Заполнить анкету")
async def start_application(message: Message, state: FSMContext) -> None:
    if is_blocked(message.from_user.id):
        await message.answer("Доступ к боту заблокирован администратором.")
        return
    await state.clear()
    await state.set_state(ApplicationForm.name)
    await message.answer(
        "1) Имя\n\nНапиши своё имя:",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(Command("cancel"))
async def cancel_application(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Заполнение анкеты отменено. Для новой попытки нажми /start.")


@router.message(ApplicationForm.name, F.text)
async def process_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    if not 2 <= len(name) <= 80:
        await message.answer("Имя должно содержать от 2 до 80 символов. Попробуй ещё раз:")
        return

    await state.update_data(name=name)
    await state.set_state(ApplicationForm.age)
    await message.answer("2) Возраст\n\nНапиши возраст числом:")


@router.message(ApplicationForm.name)
async def invalid_name(message: Message) -> None:
    await message.answer("Пожалуйста, отправь имя текстом:")


@router.message(ApplicationForm.age, F.text)
async def process_age(message: Message, state: FSMContext) -> None:
    value = message.text.strip()
    if not value.isdigit() or not 10 <= int(value) <= 80:
        await message.answer("Укажи возраст целым числом от 10 до 80:")
        return

    await state.update_data(age=int(value))
    await state.set_state(ApplicationForm.skin_photo)
    await message.answer("3) Фото скина\n\nОтправь фото своего скина:")


@router.message(ApplicationForm.age)
async def invalid_age(message: Message) -> None:
    await message.answer("Пожалуйста, отправь возраст числом от 10 до 80:")


@router.message(ApplicationForm.skin_photo, F.photo)
async def process_skin_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    if is_blocked(message.from_user.id):
        await state.clear()
        await message.answer("Доступ к боту заблокирован администратором.")
        return
    data = await state.get_data()
    photo = message.photo[-1]
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO applications
                (user_id, username, full_name, age, skin_file_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                message.from_user.id,
                message.from_user.username,
                data["name"],
                data["age"],
                photo.file_id,
                created_at,
            ),
        )
        application_id = cursor.lastrowid
        application = connection.execute(
            "SELECT * FROM applications WHERE id = ?", (application_id,)
        ).fetchone()

    await state.clear()
    await message.answer(
        "Анкета отправлена админам Void legion.\n"
        "Ожидай решения, мы сообщим результат здесь."
    )
    await send_application_to_admins(bot, application)


@router.message(ApplicationForm.skin_photo)
async def invalid_skin_photo(message: Message) -> None:
    await message.answer("Нужно отправить именно изображение скина как фото:")


@router.message(Command("applications"))
async def list_applications(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Эта команда доступна только администраторам.")
        return

    with get_connection() as connection:
        applications = connection.execute(
            "SELECT * FROM applications WHERE status = 'pending' AND blocked = 0 ORDER BY id DESC LIMIT 50"
        ).fetchall()

    if not applications:
        await message.answer("Ожидающих анкет нет.")
        return

    await message.answer(f"Ожидающие анкеты: {len(applications)}")
    for application in applications:
        await message.answer_photo(
            photo=application["skin_file_id"],
            caption=format_application(application),
            reply_markup=view_keyboard(application["id"], application["status"]),
        )


@router.message(F.text == "Проверить анкеты")
async def check_applications_button(message: Message) -> None:
    await list_applications(message)


@router.message(F.text == "Заблокированные")
async def list_blocked_users(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Эта кнопка доступна только администраторам.")
        return

    with get_connection() as connection:
        applications = connection.execute(
            """
            SELECT * FROM applications
            WHERE blocked = 1 AND id IN (
                SELECT MAX(id) FROM applications WHERE blocked = 1 GROUP BY user_id
            )
            ORDER BY id DESC LIMIT 50
            """
        ).fetchall()

    if not applications:
        await message.answer("Заблокированных пользователей нет.")
        return

    await message.answer(f"Заблокированные пользователи: {len(applications)}")
    for application in applications:
        await message.answer(
            format_application(application),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(
                    text="Разблокировать",
                    callback_data=f"application:unblock:{application['user_id']}",
                )]]
            ),
        )


@router.callback_query(F.data.startswith("application:"))
async def moderate_application(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    callback_parts = callback.data.split(":")
    if len(callback_parts) != 3 or callback_parts[0] != "application":
        await callback.answer("Некорректное действие", show_alert=True)
        return
    _, action, target_text = callback_parts
    if not target_text.isdigit():
        await callback.answer("Некорректный идентификатор", show_alert=True)
        return
    target_id = int(target_text)
    if action == "unblock":
        with get_connection() as connection:
            connection.execute(
                "UPDATE applications SET blocked = 0 WHERE user_id = ?",
                (target_id,),
            )
        await callback.answer("Пользователь разблокирован")
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.answer(f"Пользователь #{target_id} разблокирован.")
        try:
            await bot.send_message(target_id, "Тебя разблокировали в боте Void legion.")
        except Exception:
            logging.exception("Не удалось уведомить пользователя %s", target_id)
        return

    application_id = target_id
    new_status = {"accept": "accepted", "reject": "rejected"}.get(action)
    if action == "block":
        new_status = "blocked"
    if action == "reject_block":
        new_status = "rejected"
    if new_status is None:
        await callback.answer("Неизвестное действие", show_alert=True)
        return

    with get_connection() as connection:
        application = connection.execute(
            "SELECT * FROM applications WHERE id = ?", (application_id,)
        ).fetchone()
        if application is None:
            await callback.answer("Анкета не найдена", show_alert=True)
            return
        if application["status"] != "pending" and action not in {"block"}:
            await callback.answer("Эта анкета уже обработана", show_alert=True)
            return
        if action == "reject_block":
            connection.execute(
                "UPDATE applications SET status = 'rejected', blocked = 1 WHERE user_id = ?",
                (application["user_id"],),
            )
        elif action == "reject":
            connection.execute(
                "UPDATE applications SET status = 'rejected' WHERE id = ?",
                (application_id,),
            )
        elif action == "block":
            connection.execute(
                "UPDATE applications SET blocked = 1 WHERE user_id = ?",
                (application["user_id"],),
            )
        else:
            connection.execute(
                "UPDATE applications SET status = ? WHERE id = ?",
                (new_status, application_id),
            )

    decision = {
        "accepted": "принята",
        "rejected": "отклонена",
        "blocked": "заблокирована без отказа",
    }[new_status]
    await callback.answer(f"Анкета {decision}")
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(f"Решение: анкета #{application_id} {decision}.")

    try:
        if new_status == "accepted":
            await bot.send_message(
                application["user_id"],
                "Твоя анкета Void legion принята!\n"
                f"Вступай по ссылке: {DMTEAM_INVITE_LINK}",
            )
        elif action == "reject_block":
            await bot.send_message(
                application["user_id"],
                "Твоя анкета Void legion отклонена. Доступ к боту заблокирован.",
            )
        elif action == "reject":
            await bot.send_message(
                application["user_id"],
                "Твоя анкета Void legion отклонена.",
            )
        else:
            await bot.send_message(
                application["user_id"],
                "Доступ к боту Void legion заблокирован администратором.",
            )
    except Exception:
        logging.exception("Не удалось уведомить пользователя %s", application["user_id"])


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError(
            "Не задан токен бота. Добавьте переменную окружения BOT_TOKEN "
            "в настройках контейнера или создайте файл .env рядом с bot.py."
        )
    if not BOT_TOKEN_PATTERN.fullmatch(BOT_TOKEN):
        raise RuntimeError(
            "BOT_TOKEN имеет неверный формат. Проверьте переменную окружения "
            "BOT_TOKEN и не выводите её значение в логи."
        )
    if not ADMIN_IDS:
        raise RuntimeError(
            "Не заданы ADMIN_IDS. Добавьте Telegram ID администраторов "
            "через запятую в настройках контейнера или в файле .env."
        )

    logging.basicConfig(level=logging.INFO)
    init_db()
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        await router.start_polling(bot)
    except TelegramUnauthorizedError:
        logging.error(
            "Telegram отклонил BOT_TOKEN. Отзовите старый токен через @BotFather "
            "и задайте новый в переменной окружения BOT_TOKEN."
        )
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
