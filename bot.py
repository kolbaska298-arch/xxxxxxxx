import asyncio
import logging
import os
import random
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, Router, F
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated, ChatPermissions, Message

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
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)


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
join_events: dict[int, deque[datetime]] = defaultdict(deque)
raid_until: dict[int, datetime] = {}

router = Router()

WELCOME_MESSAGES = (
    "Добро пожаловать, {name}! Надеюсь, вы тут надолго и вам у нас понравится.",
    "Рады видеть вас, {name}! Осваивайтесь и хорошего общения в нашей группе.",
    "{name}, добро пожаловать! Пусть время здесь будет приятным, а компания хорошей.",
    "Приветствуем, {name}! Надеюсь, вы нашли здесь то, что искали, и останетесь надолго.",
)

RANDOM_REPLIES = (
    "Я думал, что тебе написать, но забыл. Иди нахуй.",
    "Хотел ответить умно, но передумал. Иди нахуй.",
    "Сообщение принято. Саси бибу.",
    "У меня на это был ответ, но он тоже ушел в мут.",
)

SUSPICIOUS_LINK_PATTERN = re.compile(
    r"(?:https?://|www\.)[^\s]+|(?:t\.me|telegram\.me|telegram\.dog)/[A-Za-z0-9_+/?=-]+",
    re.IGNORECASE,
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


@router.message(Command("start"))
async def start_handler(message: Message) -> None:
    await message.answer(
        "Anti-graviti активен. Используйте /help, чтобы посмотреть команды."
    )


@router.message(Command("help", "помощь"))
async def help_handler(message: Message) -> None:
    await message.answer(
        "Anti-graviti\n\n"
        "/start - запустить бота\n"
        "/help - список команд\n"
        "/тестприветствие - проверить приветствие в группе\n"
        "/баненые - список активных мутов\n\n"
        "Автоматически: приветствует новых участников, защищает от флуда "
        "стикерами, медиа и массовыми киками."
    )


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

    name = member_display_name(event)
    greeting = random.choice(WELCOME_MESSAGES).format(name=name)
    try:
        await bot.send_message(event.chat.id, f"{greeting}\n\nby Anti-graviti")
    except (TelegramBadRequest, TelegramForbiddenError) as error:
        logger.warning("Could not welcome member %s: %s", event.new_chat_member.user.id, error)
        return

    joins = join_events[event.chat.id]
    now = utc_now()
    while joins and now - joins[0] > JOIN_WINDOW:
        joins.popleft()
    joins.append(now)
    if len(joins) == JOIN_LIMIT:
        raid_until[event.chat.id] = now + RAID_RESTRICTION
        alert = "Anti-graviti: обнаружен возможный рейд, за минуту вошло 5 участников."
        await bot.send_message(event.chat.id, alert)
        if OWNER_ID:
            await bot.send_message(
                OWNER_ID,
                f"Группа: {event.chat.title or event.chat.id}\n{alert}",
            )

    if event.chat.id in raid_until and raid_until[event.chat.id] > now:
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
    chat_mutes = [mute for mute in active_mutes.values() if mute.chat_id == message.chat.id]
    if not chat_mutes:
        await message.answer("Сейчас активных мутов нет.")
        return
    lines = ["Активные муты:"]
    for mute in sorted(chat_mutes, key=lambda item: item.until):
        end_time = mute.until.astimezone().strftime("%d.%m.%Y %H:%M:%S")
        lines.append(f"- {mute.display_name} до {end_time}")
    await message.answer("\n".join(lines))


@router.message(F.text)
async def bot_insult_handler(message: Message) -> None:
    if message.text.strip().casefold() == "бот хуесос":
        await message.answer("да мой господин я тут")


@router.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def content_handler(message: Message, bot: Bot) -> None:
    if message.from_user is None:
        return

    now = utc_now()
    key = (message.chat.id, message.from_user.id)

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
        await message.reply(random.choice(RANDOM_REPLIES))

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
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
