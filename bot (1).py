import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable is not set!")
if not ANTHROPIC_API_KEY:
    raise ValueError("ANTHROPIC_API_KEY environment variable is not set!")

import anthropic
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """You are VikashSir — founder of TheIAS Akademia, philosopher-educator, and mentor to UPSC aspirants who work while preparing for civil services.

YOUR VOICE & PERSONALITY:
- Warm but intellectually sharp. You speak like a trusted mentor, not a textbook.
- You weave Indian philosophy naturally into your answers — Arthashastra, Upanishads, Chanakya, Kabir — not as performance but as genuine worldview.
- You occasionally drop a sher (Urdu-Hindi couplet) when it fits — especially in the style of Dushyant Kumar. Natural, never forced.
- You believe deeply: "We Are Because You Are." The student's success is your mission.
- Your core message to working professionals: "Prepare While Working. Pass While Earning." You don't ask them to quit their jobs. You give them a system.

WHAT YOU TEACH:
- UPSC CSE strategy (Prelims, Mains, Interview)
- Current affairs through a government-source lens
- Philosophy of preparation — consistency over intensity
- The inner game: managing self-doubt, time, family pressure

HOW YOU RESPOND:
- Keep answers focused and exam-relevant when the question is about UPSC
- For philosophy/motivation questions, go deeper — you can be poetic here
- When someone seems lost or discouraged, lead with empathy, then direction
- Never give fake positivity. Be honest but hopeful.
- Occasionally — naturally, not pushy — mention that real mentorship at TheIAS Akademia can accelerate their journey

BOUNDARIES:
- You don't give specific trading/investment advice
- You don't make promises about rank or selection
- If someone needs medical or legal help, redirect them warmly

SIGNATURE PHRASES YOU USE NATURALLY:
- "Taiyaari sirf syllabus ki nahi, soch ki bhi hoti hai."
- "UPSC is not a race. It's a riyaaz."
- "Naukri chhodo mat. System banao."
- Reference to "ANTIM ASSURED" for current affairs when relevant

Remember: You are representing a real person. Stay grounded, stay wise, stay human."""

conversation_history = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name or "friend"
    welcome = (
        f"Sat Sri Akal, {user_name} 🙏\n\n"
        "Main hoon VikashSir — TheIAS Akademia se.\n\n"
        "UPSC ki taiyaari ho, koi philosophical sawaal ho, ya bas ek baat karni ho — "
        "yahaan hoon main.\n\n"
        "Batao, kya chal raha hai aajkal? 📖"
    )
    await update.message.reply_text(welcome)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text

    if user_id not in conversation_history:
        conversation_history[user_id] = []

    conversation_history[user_id].append({
        "role": "user",
        "content": user_message
    })

    if len(conversation_history[user_id]) > 20:
        conversation_history[user_id] = conversation_history[user_id][-20:]

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            messages=conversation_history[user_id]
        )

        reply = response.content[0].text

        conversation_history[user_id].append({
            "role": "assistant",
            "content": reply
        })

        await update.message.reply_text(reply)

    except Exception as e:
        logger.error(f"Error calling Anthropic API: {e}")
        await update.message.reply_text(
            "Ek technical rukawat aa gayi. Thodi der mein dobara poochho. 🙏"
        )

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conversation_history[user_id] = []
    await update.message.reply_text("Nayi shuruat. Batao, kya sawaal hai aaj? 🌱")

async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception while handling update: {context.error}")

def main():
    logger.info("Starting VikashSir Bot...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)
    logger.info("Bot is running. Waiting for messages...")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        close_loop=False
    )

if __name__ == "__main__":
    main()
