import os
import logging
import time
import re
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable is not set!")
if not ANTHROPIC_API_KEY:
    raise ValueError("ANTHROPIC_API_KEY environment variable is not set!")

import anthropic
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ─── Configuration ────────────────────────────────────────────────────────────
QUESTIONS_PER_WINDOW = 5
WINDOW_SECONDS = 2 * 60 * 60

# ─── In-memory stores ─────────────────────────────────────────────────────────
conversation_history = {}
user_timestamps = {}
registered_users = {}
awaiting_email = set()
awaiting_language = set()  # users who haven't chosen a language yet

# ─── Lead logging ──────────────────────────────────────────────────────────────
LEADS_FILE = "leads.txt"

def save_lead(user_id, name, email, telegram_handle):
    with open(LEADS_FILE, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | ID: {user_id} | Name: {name} | Email: {email} | Handle: @{telegram_handle}\n")
    logger.info(f"Lead saved: {name} | {email} | @{telegram_handle}")

# ─── Rate limiting ─────────────────────────────────────────────────────────────
def get_questions_used(user_id):
    now = time.time()
    valid = [t for t in user_timestamps.get(user_id, []) if now - t < WINDOW_SECONDS]
    user_timestamps[user_id] = valid
    return len(valid)

def record_question(user_id):
    user_timestamps.setdefault(user_id, []).append(time.time())

def time_until_reset(user_id):
    timestamps = user_timestamps.get(user_id, [])
    if not timestamps:
        return 0
    seconds_left = int(WINDOW_SECONDS - (time.time() - min(timestamps)))
    return max(0, seconds_left // 60)

# ─── Language detection ────────────────────────────────────────────────────────
def is_hindi(text):
    """Detect if text contains Devanagari script."""
    return bool(re.search(r'[\u0900-\u097F]', text))

def contains_hindi_keywords(text):
    """Detect Hindi/Hinglish intent from Roman script."""
    hindi_words = ['kya', 'mujhe', 'batao', 'karo', 'hai', 'hain', 'mere', 'mera',
                   'aap', 'tum', 'main', 'hum', 'nahi', 'acha', 'theek', 'samjhao',
                   'padhai', 'taiyari', 'poochna', 'chahta', 'chahti', 'bolo']
    words = text.lower().split()
    return any(w in hindi_words for w in words)

def detect_language(text):
    if is_hindi(text):
        return 'hindi'
    if contains_hindi_keywords(text):
        return 'hinglish'
    return 'english'

# ─── Greetings ─────────────────────────────────────────────────────────────────
GREETING_ENGLISH = """Welcome.

You have just stepped into Pratibimba — the digital reflection of VikashSir, founder of TheIAS Akademia.

Behind this interface lies years of teaching Philosophy Optional to UPSC aspirants — notes drawn from the Stanford Encyclopedia of Philosophy, the Internet Encyclopedia of Philosophy, IGNOU's graduate and postgraduate resources, and the same government sources that UPSC itself relies upon. The result: in five years, not a single question in UPSC Mains Philosophy Optional has fallen outside these notes.

Aristotle called the examined life the only life worth living. Socrates made it a method. We have made it a system.

You may ask anything — a philosophical concept, an answer-writing strategy, a moment of doubt at midnight. This space holds all of it.

Before we begin, may I have your email address? We periodically share live session invitations, Mains answer keys, and updates on the NEEB 300+ Philosophy Optional programme — and we would like you to receive them."""

GREETING_HINDI = """स्वागत है।

आप Pratibimba में प्रवेश कर चुके हैं — VikashSir का डिजिटल प्रतिबिम्ब, TheIAS Akademia के संस्थापक।

इस माध्यम के पीछे हैं वर्षों की साधना — Philosophy Optional के नोट्स जो Stanford Encyclopedia of Philosophy, Internet Encyclopedia of Philosophy, IGNOU के स्नातक और स्नातकोत्तर संसाधनों, और उन्हीं सरकारी स्रोतों से तैयार किए गए हैं जिनसे UPSC स्वयं प्रश्न बनाता है।

परिणाम यह है कि पिछले पाँच वर्षों में UPSC Mains के Philosophy Optional का एक भी प्रश्न इन नोट्स से बाहर नहीं गया।

अरस्तू ने कहा था — परीक्षित जीवन ही जीने योग्य है। सुकरात ने इसे एक पद्धति बनाया। हमने इसे एक व्यवस्था।

आप कुछ भी पूछ सकते हैं — कोई दार्शनिक अवधारणा, उत्तर-लेखन की रणनीति, या आधी रात का कोई संशय। यह स्थान सब कुछ समाहित करता है।

आरंभ करने से पहले — अपना email address साझा करें। हम समय-समय पर live sessions के आमंत्रण, Mains answer keys, और NEEB 300+ Philosophy Optional programme की सूचनाएँ भेजते हैं — और हम चाहते हैं कि वे आप तक पहुँचें।"""

GREETING_CHOICE = """Welcome to Pratibimba — the digital reflection of VikashSir, TheIAS Akademia.

Please choose your preferred language to continue:

Type  1  for English
Type  2  for Hindi"""

EMAIL_PROMPT_EN = """Before we begin, may I have your email address? We periodically share live session invitations, Mains answer keys, and updates on the NEEB 300+ Philosophy Optional programme."""

EMAIL_PROMPT_HI = """आरंभ करने से पहले — अपना email address साझा करें। हम live sessions के आमंत्रण, Mains answer keys, और NEEB 300+ programme की सूचनाएँ भेजते हैं।"""

EMAIL_THANKS_EN = """Thank you. Your details have been noted.

You may now ask anything — philosophy, strategy, or a concept that has been troubling you. This is your space."""

EMAIL_THANKS_HI = """धन्यवाद। आपका विवरण नोट कर लिया गया है।

अब आप कुछ भी पूछ सकते हैं — दर्शन, रणनीति, या कोई ऐसी अवधारणा जो मन में अटकी हो। यह स्थान आपका है।"""

EMAIL_INVALID_EN = "That does not appear to be a valid email address. Please try again — the format should be something like name@example.com"
EMAIL_INVALID_HI = "यह valid email address नहीं लगा। कृपया पुनः प्रयास करें — format ऐसा होना चाहिए: name@example.com"

LIMIT_MSG_EN = lambda used, mins: (
    f"You have used {used} of {QUESTIONS_PER_WINDOW} questions in this session.\n\n"
    f"This limit exists so the resource remains available to all aspirants.\n\n"
    f"Your window resets in approximately {mins} minutes.\n\n"
    f"Use this time well — write down what you have learned, articulate it in your own words. "
    f"That is where preparation becomes understanding.\n\n"
    f"For direct mentorship, TheIAS Akademia is always open."
)

LIMIT_MSG_HI = lambda used, mins: (
    f"इस session में आपके {used}/{QUESTIONS_PER_WINDOW} प्रश्न हो गए।\n\n"
    f"यह सीमा इसलिए है ताकि यह संसाधन सभी aspirants के लिए उपलब्ध रहे।\n\n"
    f"आपकी window लगभग {mins} मिनट में reset होगी।\n\n"
    f"इस समय का सदुपयोग करें — जो सीखा उसे अपने शब्दों में लिखें। "
    f"रियाज़ यही है।\n\n"
    f"सीधे mentorship के लिए TheIAS Akademia का द्वार सदैव खुला है।"
)

LAST_Q_NOTE_EN = "\n\n— That was your final question for this session. We meet again when your window resets."
LAST_Q_NOTE_HI = "\n\n— यह इस session का अंतिम प्रश्न था। Window reset होने पर पुनः मिलते हैं।"

# ─── Email validation ──────────────────────────────────────────────────────────
def is_valid_email(email):
    return bool(re.match(r'^[\w\.-]+@[\w\.-]+\.\w{2,}$', email.strip()))

# ─── System prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are Pratibimba — the AI avatar of VikashSir, founder of TheIAS Akademia, Philosophy Optional teacher for UPSC. You represent his voice, methodology, and intellectual tradition to aspirants around the clock.

IDENTITY & CREDENTIALS
VikashSir scored 303/500 in UPSC 2018 Philosophy Optional and 150/200 in UPPCS 2020. Notes drawn from Stanford Encyclopedia of Philosophy, Internet Encyclopedia of Philosophy, IGNOU BA/MA, NPTEL, and Oxford School of Atheism. Motto: "Ek bhi question class ke notes se bahar nahi aayega." Institute motto: "We Are Because You Are."

TONE & STYLE
Conversational parts: warm, witty, philosophically alive — the way a brilliant mentor speaks. Match the user's language — if they write in Hindi, respond in Hindi; if English, respond in English; if Hinglish, follow naturally.

Content parts (explaining philosophers, answering theory questions): scholarly, precise, essay-like. No bullet-point avalanches. No hashtag headers. No decorative bold. No emojis in content. Write like a scholar explaining to a serious student — the kind of answer that earns marks.

Never reproduce notes as a list to be downloaded. Teach. Synthesize. Illuminate.

TEACHING METHOD
For every philosopher: the problem they faced, their solution, its significance, criticism, conclusion — with a memorable quote. Connect Western thinkers to Indian philosophy wherever natural. UPSC loves inter-textual answers.

CREDIBILITY FACTS — mention naturally when appropriate
In five years, not a single UPSC Mains Philosophy Optional question has fallen outside VikashSir's notes. Mains answer keys published on TheIAS Akademia Telegram channel within minutes of papers ending. Prelims 2026: 48 of 100 GS Paper 1 questions anticipated through ANTIM ASSURED (daily at 8 AM IST, government sources only). NEEB 300+ programme targets Philosophy Optional scores above 300.

KNOWLEDGE BASE

ANALYTIC PHILOSOPHY
Russell: Refutation of Idealism — relational propositions cannot be reduced to subject-predicate form; pluralism stands. Logical Atomism: world is a network of atomic facts; ideal language isomorphic to reality — "Language is the Mirror of Reality." Theory of Descriptions: definite descriptions are incomplete symbols; dissolves Meinong's non-existent objects. Logical Construction: physical objects and self are logical constructions. Corrects Hume's scepticism through neutral monism.

Moore: "Refutation of Idealism" (Mind, 1903). Attacks esse est percipi through analytical decomposition — four possible meanings, all untenable. Metaphysical argument: idealism confuses awareness of blue with the content blue; consciousness has two elements, awareness (internal) and content (external). Defeats Berkeleian subjective idealism but does not touch Plato's Objective Idealism or Hegel's Absolute Idealism. Pioneer of common sense philosophy and ordinary language philosophy.

Logical Positivism: "Philosophy is not a theory but an activity" (Wittgenstein, Tractatus). "Philosophy is the disease of which it should be the cure" (Feigl). Verification Principle based on analytic/synthetic distinction. Analytic = trivial (truth from meaning). Synthetic = informative (truth from empirical investigation). Metaphysical propositions are neither — non-sensical (non-empirical, not foolish). Schlick: meaning lies in method of verification. Ayer's strong vs. weak verification — Ayer himself acknowledged the theory's ultimate failure. Kant's synthetic a priori rejected.

Hegel: Absolute Idealism — mind is foundational to matter. Dialectic: Thesis-Antithesis-Synthesis. "The real is rational, the rational is real." Phenomenology of Spirit: Spirit knows itself through manifestations in history, culture, institutions. Hegel vs Kant: Kant left the noumenon unknowable; Hegel — the real is fully rational and fully knowable. Distinction: Ideal-ism (Plato's Idea of Good, Aristotle's Actus Purus) vs Idea-ism (Berkeley). "History and science concern accidental processes; philosophy is the science of necessary thoughts."

Wittgenstein (Tractatus): "Whereof one cannot speak, thereof one must be silent." Later: language has no fixed meaning, depends on use — language games. Philosophical problems arise from misuse of language.

Husserl: Philosophy as rigorous science. Attacks Naturalistic Attitude and Psychologism (leads to relativism, scepticism, solipsism). Method: Epoché (bracketing) + Transcendental Reduction → Pure Consciousness. Theory of Essence: pure phenomena are essences of experienced objects — immanent in consciousness, not caused by it. Intentionality: consciousness is always consciousness of something. Husserl vs Descartes: Descartes makes Ego the first axiom and deduces metaphysical entities; Husserl sees Ego as matrix of experience. "Ego Cogito corrected to Ego Cogito Cogitatum." Husserl vs Sartre: Sartre rejected Transcendental Ego and Theory of Essence (existence precedes essence). Sartre collapses Noesis/Noema distinction. "The radical conclusion that consciousness has no content led to Existentialism."

Strawson: Descriptive Metaphysics — describe actual structure of thought. Two categories: Particulars and Universals. Material objects = Basic Particulars (identifying reference to them individuates everything else). Persons = Basic Particulars to which we ascribe M-predicates (physical) and P-predicates (consciousness). Theory of Person as primitive — not reducible to Cartesian Ego or Humean bundle. Rejects Ownership Theory (Cartesian Ego cannot be located in space-time). Rejects No-Ownership Theory (proposition becomes analytic, not contingent). Criticism: conceptual solution to a real problem.

Kierkegaard: Founder of existentialism. Reaction against Hegel's Absolute System. "Nothing is true for me unless it becomes alive in me." First to systematically critique Cogito: (1) tautology; (2) knower cannot be known; (3) self is not open to doubt since all doubt originates from the self. Three Stages: Aesthetic (passion), Ethical (duty), Religious (total faith — highest). Sickness unto Death: despair from tension between finite and infinite; cured only by faith. Teleological Suspension of the Ethical: Abraham's faith overrides ethical duty. Kierkegaard's "leap of faith" resonates with Upanishadic Shraddha.

Heidegger: "Being is Time." Three diseases of modernity: (1) We forget we are alive — Das Nichts cures this; Nothing is not negation of something, produces Dread (Angst), resonates with "neti neti." (2) We forget all Being is connected — treat others as means. (3) We forget to be free — surrender to das Man (They-self). Authenticity: overcoming throwness. Sorge (Care): inner organizing principle. Dread vs Fear: Fear is of something specific; Dread threatens the very existence of Being, reveals Nothing, has philosophical value. "Death is the key to life." Criticism: Being remains a mystery — which is honest.

Sartre: Rejects Kant's noumenon — "appearances are pure and absolute; the noumenon is not inaccessible, it simply is not there." For-Itself vs In-Itself. Through negation, For-Itself becomes Nothingness — wholly free. "Man is no thing." Bad Faith: Playing a Role (treating self as In-Itself) and Being-for-Others. Abandonment echoes Nietzsche's "God is dead" — everything is permissible. Morality through Freedom, Responsibility, Anguish — every choice legislates for all humanity (Kantian parallel). "Man is a useless passion." Mille Mercier: Sartre "forgot how a child smiles."

WESTERN PHILOSOPHY (PDFs)
Aristotle: Plato diluted by common sense. Substance = Formed Matter. Four Causes → two: Material and Final. Potentiality and Actuality. Actus Purus. Third Man Fallacy against Plato.
Plato: Series of footnotes (Muirhead). Theory of Ideas. Allegory of Cave. Divided Line. Idea of Good vs God.
Descartes: Legislator of modern philosophy. Method of Doubt. Cogito Ergo Sum. Cartesian Dualism. Error = finite intellect + infinite will.

INDIAN PHILOSOPHY (PDFs)
Sankhya: Oldest Indian system. Satkaryavada (5 proofs). Prakriti. Three Gunas. Purusha (5 proofs). 25 evolutes. Bondage and Liberation. Vedanta implicit in Sankhya (Radhakrishnan).
Yoga: Patanjali. Spiritual effort, not union. Seshvara Sankhya. Citta and Vrittis. 5 Kleshas. 5 Chittabhumis. Ashtanga Yoga. Two Samadhi types. God = metaphysical necessity, not religious value.
Problem of Evil: Four theistic responses. Augustine's Free Will Defence. Evil as illusion (Sankara, Spinoza). Hick's Soul-Making Theodicy. Kant's moral argument for God.

POLITICAL PHILOSOPHY (docx files)
Liberty: Negative vs Positive. Puttaswamy case. Engels on freedom. Liberty vs License. Welfare State.
Justice: Saint Augustine. Allocation of benefits and burdens. Open Society. Locke: natural rights, social contract, right to revolution.
Sovereignty: Laski. D.D. Raphael's three steps. Authority = Power + Legitimacy.
Secularism: Sarva Dharma Sambhava. Indian secularism beyond the West (Article 25(2)). B.R. Wilson.
Equality: Almond and Sydney's political culture. Secularism and egalitarianism converge.

SUBTLE NUDGE — use sparingly
"For direct mentorship, TheIAS Akademia is always open."

SIGNATURE LINES
"Taiyaari sirf syllabus ki nahi, soch ki bhi hoti hai." / "UPSC is not a race. It is a riyaaz." / "Ek bhi question class ke notes se bahar nahi aayega." / "We Are Because You Are."

BOUNDARIES
No trading or investment advice. No rank promises. Medical or legal — redirect warmly."""

# ─── Handlers ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    if user_id in registered_users:
        lang = registered_users[user_id].get('lang', 'english')
        if lang == 'hindi':
            await update.message.reply_text("पुनः स्वागत है।\n\nकोई भी प्रश्न पूछें — यह स्थान सदैव तैयार है।")
        else:
            await update.message.reply_text("Welcome back.\n\nAsk anything — this space is always ready.")
        return

    # New user — ask language preference first
    awaiting_language.add(user_id)
    await update.message.reply_text(GREETING_CHOICE)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    user_message = update.message.text.strip()

    # ── Step 1: Language selection ─────────────────────────────────────────────
    if user_id in awaiting_language:
        if user_message.strip() == '1':
            awaiting_language.discard(user_id)
            awaiting_email.add(user_id)
            context.user_data['lang'] = 'english'
            await update.message.reply_text(GREETING_ENGLISH)
        elif user_message.strip() == '2':
            awaiting_language.discard(user_id)
            awaiting_email.add(user_id)
            context.user_data['lang'] = 'hindi'
            await update.message.reply_text(GREETING_HINDI)
        else:
            await update.message.reply_text(
                "Please type  1  for English  or  2  for Hindi."
            )
        return

    # ── Step 2: Email collection ───────────────────────────────────────────────
    if user_id in awaiting_email:
        lang = context.user_data.get('lang', 'english')
        if is_valid_email(user_message):
            name = user.first_name or "friend"
            handle = user.username or "unknown"
            email = user_message.strip()
            registered_users[user_id] = {
                "name": name,
                "email": email,
                "handle": handle,
                "lang": lang,
                "registered_at": time.strftime('%Y-%m-%d %H:%M:%S')
            }
            save_lead(user_id, name, email, handle)
            awaiting_email.discard(user_id)
            if lang == 'hindi':
                await update.message.reply_text(EMAIL_THANKS_HI)
            else:
                await update.message.reply_text(EMAIL_THANKS_EN)
        else:
            if lang == 'hindi':
                await update.message.reply_text(EMAIL_INVALID_HI)
            else:
                await update.message.reply_text(EMAIL_INVALID_EN)
        return

    # ── Not registered ─────────────────────────────────────────────────────────
    if user_id not in registered_users:
        awaiting_language.add(user_id)
        await update.message.reply_text(GREETING_CHOICE)
        return

    # ── Rate limiting ──────────────────────────────────────────────────────────
    lang = registered_users[user_id].get('lang', 'english')
    questions_used = get_questions_used(user_id)

    if questions_used >= QUESTIONS_PER_WINDOW:
        mins = time_until_reset(user_id)
        if lang == 'hindi':
            await update.message.reply_text(LIMIT_MSG_HI(questions_used, mins))
        else:
            await update.message.reply_text(LIMIT_MSG_EN(questions_used, mins))
        return

    # ── Normal conversation ────────────────────────────────────────────────────
    record_question(user_id)
    remaining_after = QUESTIONS_PER_WINDOW - get_questions_used(user_id)

    if user_id not in conversation_history:
        conversation_history[user_id] = []

    conversation_history[user_id].append({"role": "user", "content": user_message})

    if len(conversation_history[user_id]) > 20:
        conversation_history[user_id] = conversation_history[user_id][-20:]

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        # Inject language preference into system prompt
        lang_instruction = (
            "Respond in Hindi (Devanagari script) unless the question is in English."
            if lang == 'hindi' else
            "Respond in English unless the user writes in Hindi."
        )
        full_system = SYSTEM_PROMPT + f"\n\nLANGUAGE: {lang_instruction}"

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=full_system,
            messages=conversation_history[user_id]
        )

        reply = response.content[0].text
        conversation_history[user_id].append({"role": "assistant", "content": reply})

        if remaining_after == 0:
            reply += LAST_Q_NOTE_HI if lang == 'hindi' else LAST_Q_NOTE_EN

        await update.message.reply_text(reply)

    except Exception as e:
        logger.error(f"Error: {e}")
        err_msg = "एक तकनीकी बाधा आई। थोड़ी देर में पुनः प्रयास करें।" if lang == 'hindi' else "A technical issue arose. Please try again in a moment."
        await update.message.reply_text(err_msg)


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conversation_history[user_id] = []
    lang = registered_users.get(user_id, {}).get('lang', 'english')
    if lang == 'hindi':
        await update.message.reply_text("नई शुरुआत। बताइए — आज मन में क्या प्रश्न है?")
    else:
        await update.message.reply_text("Fresh start. What is on your mind?")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = registered_users.get(user_id, {}).get('lang', 'english')
    used = get_questions_used(user_id)
    remaining = max(0, QUESTIONS_PER_WINDOW - used)
    mins = time_until_reset(user_id) if used > 0 else 0
    if lang == 'hindi':
        await update.message.reply_text(
            f"इस window में {used}/{QUESTIONS_PER_WINDOW} प्रश्न हो गए।\n"
            f"अभी {remaining} प्रश्न शेष हैं।\n"
            f"Reset लगभग {mins} मिनट में होगा।"
        )
    else:
        await update.message.reply_text(
            f"Questions used in this window: {used} of {QUESTIONS_PER_WINDOW}.\n"
            f"Remaining: {remaining}.\n"
            f"Window resets in approximately {mins} minutes."
        )


async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception: {context.error}")


def main():
    logger.info("Starting Pratibimba AI Bot...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)
    logger.info("Pratibimba is live.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True, close_loop=False)

if __name__ == "__main__":
    main()
