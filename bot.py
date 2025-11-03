from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = "8339049510:AAGnMH4djhUXKznvLfd40k6GJ-Q8-AYDMkw"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🎮 Играть в TashBoss", web_app={"url": "https://tashboss.netlify.app"})]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Добро пожаловать в TashBoss!\n\nПострой свой Ташкент и стань самым влиятельным хакимом 💼",
        reply_markup=reply_markup
    )

app = Application.builder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))

print("✅ Бот запущен...")
app.run_polling()
