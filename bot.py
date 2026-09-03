import asyncio
import json
import logging
import os
import random
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, Router, F
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

POLL_LIMIT = 2
POLL_WINDOW = timedelta(seconds=5)
STICKER_LIMIT = 2
STICKER_WINDOW = timedelta(seconds=10)
MEDIA_LIMIT = 5
MEDIA_WINDOW = timedelta(minutes=1)
TEXT_LIMIT = 3
TEXT_WINDOW = timedelta(seconds=10)
MESSAGE_LIMIT = 8
MESSAGE_WINDOW = timedelta(seconds=10)
MEDIA_MESSAGE_LIMIT = 4
MEDIA_MESSAGE_WINDOW = timedelta(seconds=20)
RANDOM_REPLY_PROBABILITY = 0.01
KICK_LIMIT = 3
KICK_WINDOW = timedelta(seconds=30)
JOIN_LIMIT = 5
JOIN_WINDOW = timedelta(seconds=60)
RAID_RESTRICTION = timedelta(minutes=5)
CAPTCHA_RESTRICTION = timedelta(minutes=5)
EMERGENCY_RESTRICTION = timedelta(minutes=5)
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
CHAT_STORE = Path(os.getenv("CHAT_STORE", "known_chats.json"))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ActiveMute:
    chat_id: int
    user_id: int
    display_name: str
    until: datetime


poll_events: dict[tuple[int, int], deque[tuple[datetime, int]]] = defaultdict(deque)
sticker_events: dict[tuple[int, int], deque[datetime]] = defaultdict(deque)
media_events: dict[tuple[int, int], deque[datetime]] = defaultdict(deque)
message_events: dict[tuple[int, int], deque[datetime]] = defaultdict(deque)
text_events: dict[tuple[int, int], deque[tuple[datetime, str]]] = defaultdict(deque)
media_message_events: dict[tuple[int, int], deque[datetime]] = defaultdict(deque)
active_mutes: dict[tuple[int, int], ActiveMute] = {}
violation_counts: dict[tuple[int, int], int] = defaultdict(int)
admin_kick_events: dict[tuple[int, int], deque[datetime]] = defaultdict(deque)
join_events: dict[int, deque[tuple[datetime, int]]] = defaultdict(deque)
raid_until: dict[int, datetime] = {}
raid_alert_sent: dict[int, datetime] = {}
emergency_until: dict[int, datetime] = {}
emergency_tasks: dict[int, asyncio.Task] = {}
pending_captcha: dict[tuple[int, int], datetime] = {}
known_chats: set[int] = set()

try:
    known_chats.update(int(chat_id) for chat_id in json.loads(CHAT_STORE.read_text(encoding="utf-8")))
except (FileNotFoundError, OSError, TypeError, ValueError):
    pass

router = Router()

WELCOME_MESSAGES = (
    "Добро пожаловать, {name}! Надеюсь, вы тут надолго и вам у нас понравится.",
    "Рады видеть вас, {name}! Осваивайтесь и хорошего общения в нашей группе.",
    "{name}, добро пожаловать! Пусть время здесь будет приятным, а компания хорошей.",
    "Приветствуем, {name}! Надеюсь, вы нашли здесь то, что искали, и останетесь надолго.",
)

RANDOM_REPLY_OPENERS = (
    "Я прочитал это и решил, что", "Мой внутренний модератор сообщает, что",
    "После совещания с самим собой выяснилось, что", "Срочная аналитика чата показывает, что",
    "Не хочу прерывать этот поток мысли, но", "Телеграм уже пожалел, что доставил мне это, потому что",
    "По данным сверхсекретной комиссии,", "Я бы ответил культурно, однако",
    "Мозг бота сделал перерыв и постановил, что",
)
RANDOM_REPLY_MIDDLES = (
    "это какой-то лютый", "сообщение выглядит как прекрасный", "в чате обнаружен подозрительный",
    "тут происходит обычный", "это уверенный", "перед нами очередной",
    "данный текст породил огромный", "это настолько странный", "зафиксирован максимально нелепый",
    "в эфир ворвался легендарный",
)
RANDOM_REPLY_QUALIFIERS = (
    "внезапный", "дежурный", "эпичный", "необъяснимый", "матерный",
    "сомнительный", "грандиозный", "локальный", "безнадёжный", "вопиюще наглый",
)
RANDOM_REPLY_MIDDLES = tuple(
    f"{middle} {qualifier}"
    for middle in RANDOM_REPLY_MIDDLES
    for qualifier in RANDOM_REPLY_QUALIFIERS
)
RANDOM_REPLY_ENDINGS = (
    "бардак, но мне нравится", "капец", "цирк с конями", "пиздец", "суетной кошмар",
    "маразм", "фейерверк ерунды", "разговорный провал", "хаос", "кринж",
    "праздник дурных решений", "шумный бред", "эксперимент над здравым смыслом",
    "компот из букв", "парад самоуверенности", "фокус без фокуса", "провал века",
    "балаган", "бардак на максималках", "сюрприз для админов", "мут на ножках",
    "приступ словесной жести", "пирожок с матом", "странный квест", "облом",
    "запах приключений", "громкий пшик", "текстовый апокалипсис", "мелкий беспредел",
    "вопль клавиатуры", "бытовой ад", "неудачный перформанс", "пыльный мем",
    "самоуверенный треш", "драма из ничего", "случайный позор", "мыльная опера",
    "шедевр бездарности", "разговорный кульбит", "бюджетный хаос", "крик души",
    "странный поворот", "приключение без смысла", "разнос", "сбой матрицы",
    "поток сознания", "веселый кошмар", "непредвиденный цирк", "провал логики",
    "парадокс", "халтура", "непрошеный стендап", "взрыв тупняка", "нежданный трэш",
    "сезонный бардак", "дешевый боевик", "каша", "бессмысленная драма", "мутная история",
    "веский повод помолчать", "ржака", "глупость", "каприз", "позорный финал",
    "сам себе анекдот", "жесткий оффтоп", "кривой спектакль", "комедия ошибок",
    "плохая импровизация", "переоцененный шум", "словесная авария", "бесполезная сенсация",
    "странная затея", "помойка аргументов", "неудачный заход", "рандомный угар",
    "пыльный спор", "громкая ошибка", "мелкая пакость", "непонятный выкрутас",
    "вялый скандал", "лишняя сущность", "чистый абсурд", "кривой фокус", "бредовый финт",
    "микро-катастрофа", "смешной тупик", "сомнительный шедевр", "бардак", "шум", "хрень",
    "ерунда", "полнейшая чушь", "матерный сюрприз", "финальный пиздец",
)


def random_reply() -> str:
    return f"{random.choice(RANDOM_REPLY_OPENERS)} {random.choice(RANDOM_REPLY_MIDDLES)} {random.choice(RANDOM_REPLY_ENDINGS)}."

SUSPICIOUS_LINK_PATTERN = re.compile(
    r"(?:https?://|www\.)[^\s]+|(?:t\.me|telegram\.me|telegram\.dog)/[A-Za-z0-9_+/?=-]+",
    re.IGNORECASE,
)
RAID_KEYWORD_PATTERN = re.compile(
    r"\b(?:рейд|raid|массовый\s+вход|атака|боты|бот-атака|заспамили|налёт|налет)\b",
    re.IGNORECASE,
)
RANDOM_LETTER_SPAM_PATTERN = re.compile(r"^[а-яёa-z]+$", re.IGNORECASE)
NUMBER_SPAM_PATTERN = re.compile(r"^[0-9\s.,+\-]+$")


def is_random_letter_spam(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text.casefold())
    if len(normalized) < 12 or not RANDOM_LETTER_SPAM_PATTERN.fullmatch(normalized):
        return False
    return len(set(normalized)) <= max(4, len(normalized) // 5)


def is_number_spam(text: str) -> bool:
    tokens = re.findall(r"\d+", text)
    normalized = "".join(tokens)
    if len(normalized) < 11 or not NUMBER_SPAM_PATTERN.fullmatch(text):
        return False
    if len(tokens) >= 5:
        values = [int(token) for token in tokens]
        if all(values[index + 1] - values[index] in {-1, 1} for index in range(len(values) - 1)):
            return True
    if len(tokens) == 1:
        return True
    if len(set(normalized)) <= 3:
        return True
    digits = [int(digit) for digit in normalized]
    return all(
        digits[index + 1] - digits[index] in {-1, 1}
        for index in range(len(digits) - 1)
    )


def is_group(message: Message) -> bool:
    return message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}


def display_name(message: Message) -> str:
    user = message.from_user
    if user is None:
        return "Пользователь"
    return f"@{user.username}" if user.username else user.full_name


def member_display_name(member: ChatMemberUpdated) -> str:
    user = member.new_chat_member.user
    return f"@{user.username}" if user.username else user.full_name


def actor_display_name(event: ChatMemberUpdated) -> str:
    user = event.from_user
    return f"@{user.username}" if user.username else user.full_name


async def is_moderator_or_creator(bot: Bot, message: Message) -> bool:
    if message.from_user is None:
        return True
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    except (TelegramBadRequest, TelegramForbiddenError) as error:
        logger.warning("Could not inspect member %s in chat %s: %s", message.from_user.id, message.chat.id, error)
        return True
    return member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}


async def bot_can_restrict_members(bot: Bot, chat_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, bot.id)
    except (TelegramBadRequest, TelegramForbiddenError) as error:
        logger.warning("Could not inspect bot permissions in chat %s: %s", chat_id, error)
        return False
    return (
        member.status == ChatMemberStatus.CREATOR
        or (member.status == ChatMemberStatus.ADMINISTRATOR and bool(member.can_restrict_members))
    )


async def mute_user(bot: Bot, message: Message, minutes: int, reason: str, message_ids: list[int] | None = None) -> bool:
    if message.from_user is None or not await bot_can_restrict_members(bot, message.chat.id):
        logger.warning("Bot cannot restrict members in chat %s", message.chat.id)
        return False

    until = utc_now() + timedelta(minutes=minutes)
    try:
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until,
        )
        active_mutes[(message.chat.id, message.from_user.id)] = ActiveMute(
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            display_name=display_name(message),
            until=until,
        )
        for message_id in message_ids or [message.message_id]:
            try:
                await bot.delete_message(message.chat.id, message_id)
            except (TelegramBadRequest, TelegramForbiddenError):
                logger.info("Could not delete message %s in chat %s", message_id, message.chat.id)
        await message.answer(f"{display_name(message)} {reason} и отправлен в мут на {minutes} минут.")
        return True
    except (TelegramBadRequest, TelegramForbiddenError) as error:
        logger.warning("Could not mute user %s: %s", message.from_user.id, error)
        return False


async def punish_violation(bot: Bot, message: Message, reason: str) -> None:
    key = (message.chat.id, message.from_user.id)
    violation_counts[key] += 1
    if violation_counts[key] >= 3:
        try:
            await bot.ban_chat_member(message.chat.id, message.from_user.id)
            await message.answer(f"{display_name(message)} заблокирован за повторные нарушения.")
            violation_counts.pop(key, None)
        except (TelegramBadRequest, TelegramForbiddenError) as error:
            logger.warning("Could not ban repeat offender %s: %s", message.from_user.id, error)
        return
    await mute_user(bot, message, 10, reason)


def full_chat_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=True,
        can_send_audios=True,
        can_send_documents=True,
        can_send_photos=True,
        can_send_videos=True,
        can_send_video_notes=True,
        can_send_voice_notes=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
        can_react_to_messages=True,
        can_invite_users=True,
    )


async def extend_captcha_for_raid(bot: Bot, chat_id: int, raid_end: datetime) -> None:
    captcha_end = raid_end + CAPTCHA_RESTRICTION
    for (pending_chat_id, user_id), expires_at in list(pending_captcha.items()):
        if pending_chat_id != chat_id:
            continue
        new_expiry = max(expires_at, captcha_end)
        pending_captcha[(chat_id, user_id)] = new_expiry
        try:
            await bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=new_expiry,
            )
        except (TelegramBadRequest, TelegramForbiddenError) as error:
            logger.warning("Could not extend captcha for member %s: %s", user_id, error)


async def finish_emergency_lockdown(bot: Bot, chat_id: int, expected_until: datetime | None = None) -> None:
    until = emergency_until.get(chat_id)
    if until is None or (expected_until is not None and until != expected_until):
        return
    emergency_until.pop(chat_id, None)
    emergency_tasks.pop(chat_id, None)
    try:
        await bot.set_chat_permissions(chat_id, full_chat_permissions())
        await bot.send_message(chat_id, "Экстренный режим завершён. CAPTCHA у новых участников всё ещё обязательна.")
    except (TelegramBadRequest, TelegramForbiddenError) as error:
        logger.warning("Could not finish emergency lockdown in chat %s: %s", chat_id, error)


async def release_emergency_lockdown(bot: Bot, chat_id: int, until: datetime) -> None:
    delay = max(0, (until - utc_now()).total_seconds())
    await asyncio.sleep(delay)
    await finish_emergency_lockdown(bot, chat_id, until)


async def activate_emergency_lockdown(bot: Bot) -> int:
    now = utc_now()
    until = now + EMERGENCY_RESTRICTION
    activated = 0
    for chat_id in sorted(known_chats):
        try:
            await bot.set_chat_permissions(
                chat_id,
                ChatPermissions(can_send_messages=False),
            )
            raid_until[chat_id] = max(raid_until.get(chat_id, now), until)
            emergency_until[chat_id] = until
            await extend_captcha_for_raid(bot, chat_id, until)
            await bot.send_message(
                chat_id,
                "Включён экстренный режим Anti-graviti на 5 минут. Новые участники смогут пройти CAPTCHA после его завершения.",
            )
            old_task = emergency_tasks.get(chat_id)
            if old_task:
                old_task.cancel()
            emergency_tasks[chat_id] = asyncio.create_task(release_emergency_lockdown(bot, chat_id, until))
            activated += 1
        except (TelegramBadRequest, TelegramForbiddenError) as error:
            logger.warning("Could not activate emergency lockdown in chat %s: %s", chat_id, error)
    return activated


def owner_control_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Опубликовать обновление", callback_data="publish_update_07")],
            [InlineKeyboardButton(text="Включить экстренный режим", callback_data="emergency_on")],
            [InlineKeyboardButton(text="Выключить экстренный режим", callback_data="emergency_off")],
        ]
    )


def remember_chat(chat_id: int) -> None:
    if chat_id in known_chats:
        return
    known_chats.add(chat_id)
    try:
        CHAT_STORE.write_text(json.dumps(sorted(known_chats)), encoding="utf-8")
    except OSError as error:
        logger.warning("Could not save known chat %s: %s", chat_id, error)


UPDATE_07_TEXT = (
    "Обновление Anti-graviti 0.7\n\n"
    "Что нового:\n"
    "- исправлена работа списка мутов и CAPTCHA;\n"
    "- добавлена кнопка снятия ограничения;\n"
    "- для новых участников добавлена понятная CAPTCHA;\n"
    "- усилена защита от ссылок, повторяющихся сообщений и флуда;\n"
    "- добавлено обнаружение массового входа по ключевым словам;\n"
    "- добавлена защита от массового входа и массовых киков;\n"
    "- добавлен экстренный режим управления из личных сообщений владельца;\n"
    "- добавлены команды /help, /chatid, /send и /sendhere.\n\n"
    "Anti-graviti продолжает следить за порядком."
)


@router.message(Command("start"))
async def start_handler(message: Message) -> None:
    reply_markup = owner_control_keyboard() if message.from_user and message.from_user.id == OWNER_ID else None
    await message.answer(
        "Anti-graviti активен. Используйте /help, чтобы посмотреть команды.",
        reply_markup=reply_markup,
    )


@router.message(Command("help", "помощь"))
async def help_handler(message: Message) -> None:
    reply_markup = owner_control_keyboard() if message.from_user and message.from_user.id == OWNER_ID else None
    await message.answer(
        "Anti-graviti\n\n"
        "/start - запустить бота\n"
        "/help - список команд\n"
        "/тестприветствие - проверить приветствие в группе\n"
        "/баненые - список активных мутов\n\n"
        "В личных сообщениях владельцу доступна команда /emergency для экстренной блокировки групп.\n\n"
        "Автоматически: приветствует новых участников, защищает от флуда "
        "стикерами, медиа и массовыми киками. Новым участникам нужно пройти CAPTCHA.",
        reply_markup=reply_markup,
    )


@router.message(Command("emergency"))
async def emergency_command_handler(message: Message, bot: Bot) -> None:
    if message.chat.type != ChatType.PRIVATE or message.from_user is None or message.from_user.id != OWNER_ID:
        return
    await message.answer(
        "Экстренное управление группами:",
        reply_markup=owner_control_keyboard(),
    )


@router.callback_query(F.data.in_({"emergency_on", "emergency_off"}))
async def emergency_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    if callback.from_user is None or callback.from_user.id != OWNER_ID:
        await callback.answer("Кнопка доступна только владельцу.", show_alert=True)
        return
    if callback.data == "emergency_on":
        count = await activate_emergency_lockdown(bot)
        await callback.answer("Экстренный режим включён.")
        if callback.message:
            await callback.message.answer(f"Экстренный режим включён в доступных чатах: {count}.")
        return
    now = utc_now()
    for chat_id, until in list(emergency_until.items()):
        emergency_until[chat_id] = now
        task = emergency_tasks.get(chat_id)
        if task:
            task.cancel()
        await finish_emergency_lockdown(bot, chat_id, now)
    await callback.answer("Экстренный режим выключен.")


@router.callback_query(F.data == "show_update_07")
async def update_07_handler(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.from_user.id != OWNER_ID:
        await callback.answer("Кнопка доступна только владельцу.", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.answer(UPDATE_07_TEXT)


@router.callback_query(F.data == "publish_update_07")
async def publish_update_07_handler(callback: CallbackQuery, bot: Bot) -> None:
    if callback.from_user is None or callback.from_user.id != OWNER_ID:
        await callback.answer("Кнопка доступна только владельцу.", show_alert=True)
        return

    await callback.answer("Начинаю публикацию.")
    sent_count = 0
    failed_chats = []
    for chat_id in sorted(known_chats):
        try:
            await bot.send_message(chat_id, UPDATE_07_TEXT)
            sent_count += 1
        except (TelegramBadRequest, TelegramForbiddenError) as error:
            failed_chats.append(chat_id)
            logger.warning("Could not publish update to %s: %s", chat_id, error)

    if callback.message:
        result = f"Обновление опубликовано в чатах: {sent_count}."
        if failed_chats:
            result += f" Недоступных чатов: {len(failed_chats)}."
        await callback.message.answer(result)


@router.message(Command("chatid", "айди"))
async def chat_id_handler(message: Message) -> None:
    if is_group(message) and message.from_user and message.from_user.id == OWNER_ID:
        await message.answer(f"ID этого чата: {message.chat.id}")


@router.message(Command("send"))
async def send_command_handler(message: Message, bot: Bot) -> None:
    if message.from_user is None or message.from_user.id != OWNER_ID:
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Формат: /send chat_id текст")
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await message.answer("chat_id должен быть числом.")
        return
    try:
        await bot.send_message(chat_id, parts[2])
        await message.answer("Сообщение отправлено.")
    except (TelegramBadRequest, TelegramForbiddenError) as error:
        logger.warning("Could not send owner message to %s: %s", chat_id, error)
        await message.answer("Не удалось отправить сообщение. Проверьте ID и права бота.")


@router.message(Command("sendhere"))
async def send_here_command_handler(message: Message, bot: Bot) -> None:
    if not is_group(message) or message.from_user is None or message.from_user.id != OWNER_ID:
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /sendhere текст")
        return
    try:
        await bot.send_message(message.chat.id, parts[1])
    except (TelegramBadRequest, TelegramForbiddenError) as error:
        logger.warning("Could not send owner message to %s: %s", message.chat.id, error)


@router.chat_member()
async def welcome_new_member(event: ChatMemberUpdated, bot: Bot) -> None:
    if event.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return
    if event.old_chat_member.status not in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}:
        return
    if event.new_chat_member.status not in {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR,
    }:
        return
    remember_chat(event.chat.id)

    name = member_display_name(event)
    now = utc_now()
    joins = join_events[event.chat.id]
    while joins and now - joins[0][0] > JOIN_WINDOW:
        joins.popleft()
    joins.append((now, event.new_chat_member.user.id))
    unique_joiners = {user_id for _, user_id in joins}
    raid_active = raid_until.get(event.chat.id, datetime.min.replace(tzinfo=timezone.utc)) > now

    if event.new_chat_member.status == ChatMemberStatus.MEMBER:
        raid_end = raid_until.get(event.chat.id, now)
        captcha_expiry = max(now + CAPTCHA_RESTRICTION, raid_end + CAPTCHA_RESTRICTION if raid_active else now)
        pending_captcha[(event.chat.id, event.new_chat_member.user.id)] = captcha_expiry
        try:
            await bot.restrict_chat_member(
                chat_id=event.chat.id,
                user_id=event.new_chat_member.user.id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=captcha_expiry,
            )
            await bot.send_message(
                event.chat.id,
                (
                    f"{name}, вы вошли в группу во время режима защиты. Сейчас писать нельзя. "
                    "После его завершения нажмите кнопку «Я не бот» ниже, чтобы пройти CAPTCHA. "
                    "Проверка действует ещё 5 минут."
                    if raid_active
                    else f"{name}, подтвердите, что вы человек, нажав кнопку «Я не бот» ниже. "
                    "До прохождения CAPTCHA отправка сообщений ограничена. Проверка действует 5 минут."
                ),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[
                        InlineKeyboardButton(
                            text="Я не бот",
                            callback_data=f"captcha:{event.chat.id}:{event.new_chat_member.user.id}",
                        )
                    ]]
                ),
            )
        except (TelegramBadRequest, TelegramForbiddenError) as error:
            logger.warning("Could not start captcha for member %s: %s", event.new_chat_member.user.id, error)

    greeting = random.choice(WELCOME_MESSAGES).format(name=name)
    try:
        if not raid_active:
            await bot.send_message(event.chat.id, f"{greeting}\n\nby Anti-graviti")
    except (TelegramBadRequest, TelegramForbiddenError) as error:
        logger.warning("Could not welcome member %s: %s", event.new_chat_member.user.id, error)
        return

    if len(unique_joiners) >= JOIN_LIMIT and not raid_active:
        raid_until[event.chat.id] = now + RAID_RESTRICTION
        raid_alert_sent[event.chat.id] = now
        alert = "Anti-graviti: обнаружен возможный рейд, за минуту вошло 5 участников."
        await bot.send_message(event.chat.id, alert)
        if OWNER_ID:
            await bot.send_message(
                OWNER_ID,
                f"Группа: {event.chat.title or event.chat.id}\n{alert}",
            )

    if raid_until.get(event.chat.id, datetime.min.replace(tzinfo=timezone.utc)) <= now:
        raid_until.pop(event.chat.id, None)
        raid_alert_sent.pop(event.chat.id, None)
    elif raid_until[event.chat.id] > now:
        try:
            await bot.restrict_chat_member(
                chat_id=event.chat.id,
                user_id=event.new_chat_member.user.id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=raid_until[event.chat.id],
            )
            await bot.send_message(
                event.chat.id,
                f"Anti-graviti: {name} временно ограничен на 5 минут из-за режима защиты от рейда.",
            )
        except (TelegramBadRequest, TelegramForbiddenError) as error:
            logger.warning("Could not restrict new member %s: %s", event.new_chat_member.user.id, error)


@router.callback_query(F.data.startswith("captcha:"))
async def captcha_handler(callback: CallbackQuery, bot: Bot) -> None:
    if callback.message is None or callback.from_user is None:
        return
    try:
        _, chat_id_text, user_id_text = callback.data.split(":")
        chat_id = int(chat_id_text)
        user_id = int(user_id_text)
    except (AttributeError, ValueError):
        await callback.answer("Некорректная проверка.", show_alert=True)
        return

    if callback.from_user.id != user_id:
        await callback.answer("Эта кнопка предназначена для другого участника.", show_alert=True)
        return
    raid_end = raid_until.get(chat_id, datetime.min.replace(tzinfo=timezone.utc))
    if raid_end > utc_now():
        await callback.answer("Режим защиты ещё активен. Попробуйте после его завершения.", show_alert=True)
        return
    expires_at = pending_captcha.get((chat_id, user_id))
    if expires_at is None or expires_at <= utc_now():
        pending_captcha.pop((chat_id, user_id), None)
        await callback.answer("Проверка истекла. Войдите в группу заново.", show_alert=True)
        return

    try:
        await bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
                can_react_to_messages=True,
                can_invite_users=True,
            ),
        )
        pending_captcha.pop((chat_id, user_id), None)
        await callback.message.edit_text("Проверка пройдена. Добро пожаловать в Anti-graviti!")
        await callback.answer("Готово!")
    except (TelegramBadRequest, TelegramForbiddenError) as error:
        logger.warning("Could not complete captcha for member %s: %s", user_id, error)
        await callback.answer("Не удалось завершить проверку.", show_alert=True)


@router.message(Command("тестприветствие", "testwelcome"))
async def test_welcome_handler(message: Message, bot: Bot) -> None:
    if not is_group(message) or not await is_moderator_or_creator(bot, message):
        return
    name = display_name(message)
    greeting = random.choice(WELCOME_MESSAGES).format(name=name)
    await message.answer(f"{greeting}\n\nby Anti-graviti")


@router.chat_member()
async def detect_admin_kick(event: ChatMemberUpdated, bot: Bot) -> None:
    if event.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return
    if event.new_chat_member.status != ChatMemberStatus.KICKED:
        return
    if event.from_user.id == event.new_chat_member.user.id:
        return

    try:
        actor = await bot.get_chat_member(event.chat.id, event.from_user.id)
    except (TelegramBadRequest, TelegramForbiddenError) as error:
        logger.warning("Could not inspect kick actor %s: %s", event.from_user.id, error)
        return
    if actor.status not in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}:
        return
    if actor.status == ChatMemberStatus.CREATOR:
        return

    key = (event.chat.id, event.from_user.id)
    now = utc_now()
    events = admin_kick_events[key]
    while events and now - events[0] > KICK_WINDOW:
        events.popleft()
    events.append(now)
    if len(events) < KICK_LIMIT:
        return
    events.clear()

    actor_name = actor_display_name(event)
    target_name = member_display_name(event)
    alert = (
        f"Анти-рейд: {actor_name} удалил пользователя {target_name} "
        f"{KICK_LIMIT} раза за {int(KICK_WINDOW.total_seconds())} секунд."
    )
    await bot.send_message(event.chat.id, alert)
    if OWNER_ID:
        await bot.send_message(OWNER_ID, f"Группа: {event.chat.title or event.chat.id}\n{alert}")

    try:
        await bot.promote_chat_member(
            chat_id=event.chat.id,
            user_id=event.from_user.id,
            can_manage_chat=False,
            can_delete_messages=False,
            can_manage_video_chats=False,
            can_restrict_members=False,
            can_promote_members=False,
            can_change_info=False,
            can_invite_users=False,
            can_post_stories=False,
            can_edit_stories=False,
            can_delete_stories=False,
            can_post_messages=False,
            can_edit_messages=False,
            can_pin_messages=False,
            can_manage_topics=False,
        )
        await bot.ban_chat_member(event.chat.id, event.from_user.id)
    except (TelegramBadRequest, TelegramForbiddenError) as error:
        logger.warning("Could not punish admin %s in chat %s: %s", event.from_user.id, event.chat.id, error)


@router.message(Command("баненые"))
async def muted_list_handler(message: Message, bot: Bot) -> None:
    if not is_group(message) or not await is_moderator_or_creator(bot, message):
        return
    now = utc_now()
    expired = [key for key, mute in active_mutes.items() if mute.until <= now]
    for key in expired:
        active_mutes.pop(key, None)
    expired_captcha = [key for key, expires_at in pending_captcha.items() if expires_at <= now]
    for key in expired_captcha:
        pending_captcha.pop(key, None)
    chat_mutes = [mute for mute in active_mutes.values() if mute.chat_id == message.chat.id]
    captcha_users = [key for key in pending_captcha if key[0] == message.chat.id]
    if not chat_mutes and not captcha_users:
        await message.answer("Сейчас активных мутов нет.")
        return
    lines = ["Активные муты:"]
    buttons = []
    for mute in sorted(chat_mutes, key=lambda item: item.until):
        end_time = mute.until.astimezone().strftime("%d.%m.%Y %H:%M:%S")
        lines.append(f"- {mute.display_name} до {end_time}")
        buttons.append([
            InlineKeyboardButton(
                text=f"Размутить {mute.display_name}",
                callback_data=f"unmute:{mute.chat_id}:{mute.user_id}",
            )
        ])
    for chat_id, user_id in captcha_users:
        lines.append(f"- Пользователь {user_id}: ожидает CAPTCHA")
        buttons.append([
            InlineKeyboardButton(
                text=f"Снять ограничение {user_id}",
                callback_data=f"unmute:{chat_id}:{user_id}",
            )
        ])
    await message.answer(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("unmute:"))
async def unmute_handler(callback: CallbackQuery, bot: Bot) -> None:
    if callback.message is None or callback.from_user is None:
        return
    try:
        _, chat_id_text, user_id_text = callback.data.split(":")
        chat_id = int(chat_id_text)
        user_id = int(user_id_text)
    except (AttributeError, ValueError):
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return

    if callback.message.chat.id != chat_id:
        await callback.answer("Кнопка относится к другой группе.", show_alert=True)
        return
    try:
        member = await bot.get_chat_member(chat_id, callback.from_user.id)
    except (TelegramBadRequest, TelegramForbiddenError) as error:
        logger.warning("Could not inspect unmute actor %s: %s", callback.from_user.id, error)
        await callback.answer("Не удалось проверить права.", show_alert=True)
        return
    if member.status not in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}:
        await callback.answer("Размутить может только администратор.", show_alert=True)
        return

    if (chat_id, user_id) not in active_mutes and (chat_id, user_id) not in pending_captcha:
        await callback.answer("Этот мут уже снят или истёк.", show_alert=True)
        return
    try:
        await bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=full_chat_permissions(),
        )
        active_mutes.pop((chat_id, user_id), None)
        pending_captcha.pop((chat_id, user_id), None)
        await callback.message.edit_text("Мут снят администратором.")
        await callback.answer("Пользователь размучен.")
    except (TelegramBadRequest, TelegramForbiddenError) as error:
        logger.warning("Could not unmute user %s in chat %s: %s", user_id, chat_id, error)
        await callback.answer("Не удалось снять мут.", show_alert=True)


async def terminal_sender(bot: Bot) -> None:
    print("Консольная отправка: введите chat_id|сообщение (Ctrl+C для выхода).")
    while True:
        try:
            command = await asyncio.to_thread(input, "> ")
        except (EOFError, KeyboardInterrupt):
            return
        if "|" not in command:
            print("Формат: chat_id|сообщение")
            continue
        chat_id_text, text = command.split("|", 1)
        try:
            chat_id = int(chat_id_text.strip())
        except ValueError:
            print("chat_id должен быть числом.")
            continue
        text = text.strip()
        if not text:
            print("Сообщение не может быть пустым.")
            continue
        try:
            await bot.send_message(chat_id, text)
            print("Сообщение отправлено.")
        except (TelegramBadRequest, TelegramForbiddenError) as error:
            logger.warning("Could not send terminal message to %s: %s", chat_id, error)
            print("Не удалось отправить сообщение: проверьте chat_id и права бота.")


@router.message(F.text)
async def bot_insult_handler(message: Message) -> None:
    if message.text.strip().casefold() == "бот хуесос":
        await message.answer("да мой господин я тут")


@router.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def content_handler(message: Message, bot: Bot) -> None:
    if message.from_user is None:
        return
    remember_chat(message.chat.id)

    now = utc_now()
    key = (message.chat.id, message.from_user.id)

    if message.text and RAID_KEYWORD_PATTERN.search(message.text):
        raid_end = now + RAID_RESTRICTION
        already_active = raid_until.get(message.chat.id, datetime.min.replace(tzinfo=timezone.utc)) > now
        raid_until[message.chat.id] = max(raid_until.get(message.chat.id, now), raid_end)
        await extend_captcha_for_raid(bot, message.chat.id, raid_until[message.chat.id])
        if not already_active:
            raid_alert_sent[message.chat.id] = now
            alert = "Anti-graviti: обнаружен тревожный признак рейда. Новые участники временно ограничены."
            await message.answer(alert)
            if OWNER_ID:
                await bot.send_message(OWNER_ID, f"Группа: {message.chat.title or message.chat.id}\n{alert}")

    if message.text and is_random_letter_spam(message.text):
        await mute_user(bot, message, 2, "отправлял бессмысленный спам из случайных букв")
        return

    if message.text and is_number_spam(message.text):
        await mute_user(bot, message, 2, "отправлял числовой спам")
        return

    message_rate = message_events[key]
    while message_rate and now - message_rate[0] > MESSAGE_WINDOW:
        message_rate.popleft()
    message_rate.append(now)
    if len(message_rate) > MESSAGE_LIMIT:
        message_rate.clear()
        await mute_user(bot, message, 2, "слишком часто отправлял сообщения")
        return

    if message.text and SUSPICIOUS_LINK_PATTERN.search(message.text):
        try:
            await bot.delete_message(message.chat.id, message.message_id)
        except (TelegramBadRequest, TelegramForbiddenError) as error:
            logger.info("Could not delete suspicious link message %s: %s", message.message_id, error)
        await punish_violation(bot, message, "отправил ссылку без разрешения")
        return

    if message.text:
        events = text_events[key]
        while events and now - events[0][0] > TEXT_WINDOW:
            events.popleft()
        events.append((now, message.text.strip().casefold()))
        recent_texts = [text for _, text in list(events)[-TEXT_LIMIT:]]
        if len(recent_texts) == TEXT_LIMIT and len(set(recent_texts)) == 1:
            events.clear()
            await mute_user(bot, message, 5, "повторял одно и то же сообщение")
            return

    if message.text and random.random() < RANDOM_REPLY_PROBABILITY:
        await message.reply(random_reply())

    if message.poll is not None and message.poll.type == "quiz":
        sticker_events.pop(key, None)
        events = poll_events[key]
        while events and now - events[0][0] > POLL_WINDOW:
            events.popleft()
        events.append((now, message.message_id))
        if len(events) > POLL_LIMIT:
            ids = [event[1] for event in events]
            events.clear()
            await mute_user(bot, message, 5, "превысил лимит викторин", ids)
        return

    if message.sticker is not None:
        poll_events.pop(key, None)
        events = sticker_events[key]
        while events and now - events[0] > STICKER_WINDOW:
            events.popleft()
        events.append(now)
        if len(events) > STICKER_LIMIT:
            events.clear()
            await punish_violation(bot, message, "саси бибу by DmTeam")
        return

    poll_events.pop(key, None)
    sticker_events.pop(key, None)
    is_media = bool(message.photo or message.video or message.animation or message.voice)
    if is_media:
        events = media_events[key]
        while events and now - events[0] > MEDIA_WINDOW:
            events.popleft()
        events.append(now)
        if len(events) > MEDIA_LIMIT:
            events.clear()
            await punish_violation(bot, message, "превысил лимит медиафайлов")

        media_messages = media_message_events[key]
        while media_messages and now - media_messages[0] > MEDIA_MESSAGE_WINDOW:
            media_messages.popleft()
        media_messages.append(now)
        if len(media_messages) > MEDIA_MESSAGE_LIMIT:
            media_messages.clear()
            await punish_violation(bot, message, "массово отправлял медиафайлы")


async def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Переменная окружения BOT_TOKEN не задана")

    bot = Bot(token=token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    terminal_task = asyncio.create_task(terminal_sender(bot))
    try:
        await dispatcher.start_polling(bot)
    finally:
        terminal_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
