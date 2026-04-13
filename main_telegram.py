import asyncio
import os
import signal
import sys
from typing import Optional, Tuple

os.environ["DUBOT_RUNTIME"] = "telegram"

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from commands.shared import _chunk_message
from config import get_config, get_wake_word, is_bot_awake, set_bot_awake
from conversations import conversation_manager
from integrations import TELEGRAM_BOT_TOKEN
from services.news_service import (
    CATEGORY_EMOJIS,
    clear_quiet_time,
    format_minutes_as_clock,
    get_daily_quiet_schedule,
    get_user_topics,
    news_manager,
    parse_time_of_day,
    record_feedback,
    set_daily_quiet_schedule,
    subscribe_user,
    unsubscribe_user,
    user_in_quiet_window,
)
from services.reminder_service import reminder_manager
from utils.llm_service import ask_llm, initialize_command_database

from whitelist import get_user_permission

initialize_command_database()

BOT_ID: Optional[int] = None
BOT_USERNAME: str = ""


def _parse_news_action(text: str) -> Tuple[str, str]:
    if not text:
        return "subscribe", ""
    parts = text.strip().split(maxsplit=1)
    first = parts[0].lower()
    if first in {"subscribe", "unsubscribe", "unsubscribe_all"}:
        return first, (parts[1] if len(parts) > 1 else "")
    return "subscribe", text


def _parse_topics(topics_text: str):
    return [t.strip().lower() for t in (topics_text or "").split(",") if t.strip()]


def _parse_news_time_args(args) -> Tuple[Optional[str], Optional[str], bool]:
    cancel = False
    resume = None
    pause = None
    positional = []

    for arg in args:
        lower = arg.lower().strip()
        if lower in {"cancel", "cancel=yes", "cancel:true"}:
            cancel = True
            continue
        if "=" in arg:
            key, value = arg.split("=", 1)
            key = key.strip().lower()
            value = value.strip()
            if key == "resume":
                resume = value
            elif key == "pause":
                pause = value
            continue
        positional.append(arg)

    if not resume and positional:
        resume = positional[0]
    if not pause and len(positional) > 1:
        pause = positional[1]

    return resume, pause, cancel


def _is_reply_to_bot(update: Update) -> bool:
    if not update.message or not update.message.reply_to_message:
        return False
    author = update.message.reply_to_message.from_user
    return bool(author and BOT_ID and author.id == BOT_ID)


def _extract_clean_content(update: Update) -> Optional[str]:
    message = update.message
    if not message or not message.text:
        return None

    text = message.text.strip()
    if not text:
        return None

    wake_word = get_wake_word().lower().strip()
    lower = text.lower()
    is_private = message.chat.type == ChatType.PRIVATE
    is_mention = bool(BOT_USERNAME and f"@{BOT_USERNAME.lower()}" in lower)
    is_wake_word = lower == wake_word or lower.startswith(wake_word + " ")
    is_reply = _is_reply_to_bot(update)

    if not (is_private or is_mention or is_wake_word or is_reply):
        return None

    if is_wake_word:
        cleaned = text[len(wake_word) :].strip()
    elif is_mention and BOT_USERNAME:
        cleaned = text.replace(f"@{BOT_USERNAME}", "").strip()
    else:
        cleaned = text

    return cleaned or None


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        "DuBot Telegram mode is online.\n"
        "Use /news to manage subscriptions and /news_time to manage quiet hours.\n"
        "In groups, mention me or use the wake word to chat."
    )


async def sleep_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not get_user_permission(update.effective_user.id):
        await update.message.reply_text("Denied.")
        return
    set_bot_awake(False)
    await update.message.reply_text("😴 Going offline. Use /wake to bring me online.")


async def wake_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not get_user_permission(update.effective_user.id):
        await update.message.reply_text("Denied.")
        return
    set_bot_awake(True)
    await update.message.reply_text("✅ Awake and back online.")


async def news_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    user_id = update.effective_user.id
    if not get_user_permission(user_id):
        await update.message.reply_text("Denied.")
        return

    text = " ".join(context.args).strip()
    action, raw_topics = _parse_news_action(text)

    if action == "subscribe" and not raw_topics:
        current = get_user_topics(user_id)
        if not current:
            known = ", ".join(sorted(CATEGORY_EMOJIS.keys()))
            await update.message.reply_text(
                "You are not following any topics yet.\n"
                f"Try /news subscribe tech, ai, finland\n"
                f"Known topics: {known}"
            )
            return
        topic_lines = [f"{CATEGORY_EMOJIS.get(t, '📰')} {t}" for t in current]
        await update.message.reply_text(
            "Your followed topics:\n"
            + "\n".join(topic_lines)
            + "\n\nUse /news unsubscribe topic1,topic2 or /news unsubscribe_all"
        )
        return

    if action == "unsubscribe_all":
        unsubscribe_user(user_id)
        await update.message.reply_text("Unsubscribed from all news topics.")
        return

    topics = _parse_topics(raw_topics)
    if not topics:
        await update.message.reply_text("Please provide at least one topic (comma-separated).")
        return

    if action == "unsubscribe":
        remaining = unsubscribe_user(user_id, topics)
        if remaining:
            await update.message.reply_text(
                f"Removed: {', '.join(topics)}\nRemaining: {', '.join(remaining)}"
            )
        else:
            await update.message.reply_text(f"Removed: {', '.join(topics)}\nNo remaining subscriptions.")
        return

    all_topics = subscribe_user(user_id, topics)
    await update.message.reply_text(
        f"Added topics: {', '.join(topics)}\n"
        f"Current topics: {', '.join(all_topics)}\n"
        "News updates will be sent here."
    )


async def news_time_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    user_id = update.effective_user.id
    if not get_user_permission(user_id):
        await update.message.reply_text("Denied.")
        return

    resume, pause, cancel = _parse_news_time_args(context.args)

    if cancel:
        clear_quiet_time(user_id)
        await update.message.reply_text("Daily quiet hours removed.")
        return

    if not resume and not pause:
        sched = get_daily_quiet_schedule(user_id)
        if not sched:
            await update.message.reply_text(
                "No quiet hours set.\nUse /news_time resume=9.00 pause=1.00"
            )
            return
        pause_m, resume_m = sched
        status = "in quiet hours" if user_in_quiet_window(user_id) else "notifications active"
        await update.message.reply_text(
            f"Quiet hours (server time): {format_minutes_as_clock(pause_m)} -> {format_minutes_as_clock(resume_m)}\n"
            f"Right now: {status}\n"
            "Use /news_time cancel to clear."
        )
        return

    if not resume or not pause:
        await update.message.reply_text("Set both values, e.g. /news_time resume=9.00 pause=1.00")
        return

    resume_min = parse_time_of_day(resume)
    pause_min = parse_time_of_day(pause)
    if resume_min is None or pause_min is None:
        await update.message.reply_text("Invalid time format. Use 9.00, 9:00, 21:30, etc.")
        return
    if resume_min == pause_min:
        await update.message.reply_text("resume and pause must be different.")
        return

    set_daily_quiet_schedule(user_id, resume_min, pause_min)
    await update.message.reply_text(
        f"Saved: notifications off {format_minutes_as_clock(pause_min)} -> {format_minutes_as_clock(resume_min)} (server time)."
    )


async def news_feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return

    data = query.data or ""
    parts = data.split(":", 3)
    if len(parts) != 4 or parts[0] != "news":
        await query.answer()
        return

    _, feedback_type, article_hash, topic = parts
    record_feedback(user.id, article_hash, feedback_type, topic)
    ack = {
        "slop": "Got it. I will send less like this.",
        "more": "Noted. I will find more like this.",
        "not_critical": "Understood. I will keep this type shorter.",
        "critical": "Marked. I will include more detail for this type.",
    }.get(feedback_type, "Feedback recorded.")
    await query.answer(ack)


async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not message or not user:
        return

    if user.is_bot:
        return
    if get_user_permission(user.id) is None:
        return
    if not is_bot_awake():
        return

    clean_content = _extract_clean_content(update)
    if not clean_content:
        return

    is_continuation = _is_reply_to_bot(update)
    username = user.username or user.full_name or str(user.id)

    await context.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    answer = await ask_llm(
        user.id,
        message.chat.id,
        clean_content,
        username,
        is_continuation=is_continuation,
        platform="telegram",
    )

    chunks = _chunk_message(answer, max_length=3500)
    if not chunks:
        return

    first = await message.reply_text(chunks[0])
    conversation_manager.set_last_bot_message(message.chat.id, first.message_id)
    for chunk in chunks[1:]:
        sent = await context.bot.send_message(chat_id=message.chat.id, text=chunk)
        conversation_manager.set_last_bot_message(message.chat.id, sent.message_id)
        await asyncio.sleep(0.3)


async def post_init(application: Application) -> None:
    global BOT_ID, BOT_USERNAME
    me = await application.bot.get_me()
    BOT_ID = me.id
    BOT_USERNAME = me.username or ""
    reminder_manager.set_telegram_bot(application.bot)
    news_manager.set_telegram_bot(application.bot)


async def post_shutdown(application: Application) -> None:
    conversation_manager.save()
    reminder_manager.stop()
    news_manager.stop()


def _ensure_ollama_running():
    """If config says start_ollama_on_startup and Ollama is not responding, start ollama serve."""
    try:
        if not get_config().get("start_ollama_on_startup"):
            return
        from utils.ollama import check_ollama_running, start_ollama

        if check_ollama_running():
            return
        start_ollama()
    except Exception:
        pass


def signal_handler(signum, frame):
    conversation_manager.save()
    reminder_manager.stop()
    news_manager.stop()
    sys.exit(0)


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN is missing. Add it to .env and restart.")
        sys.exit(1)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    for d in ("data", "data/files", "assets", "web"):
        os.makedirs(d, exist_ok=True)

    try:
        from services.status_server import start_status_server

        start_status_server()
    except Exception as e:
        print(f"Status server failed: {e}", flush=True)

    _ensure_ollama_running()
    reminder_manager.start()
    news_manager.start()

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("sleep", sleep_command))
    app.add_handler(CommandHandler("wake", wake_command))
    app.add_handler(CommandHandler("news", news_command))
    app.add_handler(CommandHandler("news_time", news_time_command))
    app.add_handler(CallbackQueryHandler(news_feedback_callback, pattern=r"^news:"))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_message_handler))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
