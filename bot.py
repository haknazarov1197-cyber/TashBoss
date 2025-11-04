import json
import os
import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

TOKEN = os.getenv("BOT_TOKEN") or "СЮДА_ВСТАВЬ_СВОЙ_ТОКЕН"

DATA_FILE = "players.json"

# Список отраслей
INDUSTRIES = {
    "transport": {"name": "🚗 Транспорт", "base_income": 1, "base_cost": 10},
    "tourism": {"name": "🏨 Туризм", "base_income": 2, "base_cost": 20},
    "ecology": {"name": "🌿 Экология", "base_income": 3, "base_cost": 40},
    "infrastructure": {"name": "🏗 Инфраструктура", "base_income": 5, "base_cost": 100},
    "international": {"name": "🌍 Международное сотрудничество", "base_income": 10, "base_cost": 200},
    "air_quality": {"name": "💨 Качество воздуха", "base_income": 15, "base_cost": 400}
}


def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}


def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)


def get_player(data, user_id):
    if str(user_id) not in data:
        data[str(user_id)] = {
            "balance": 100,
            "industries": {k: {"level": 1, "last_collect": 0} for k in INDUSTRIES}
        }
    return data[str(user_id)]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = load_data()
    player = get_player(data, user.id)
    save_data(data)

    keyboard = [
        [InlineKeyboardButton(ind["name"], callback_data=f"industry_{key}")]
        for key, ind in INDUSTRIES.items()
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"👋 Добро пожаловать в *TashBoss*, {user.first_name}!\n\n"
        f"💰 Ваш баланс: {player['balance']} BSS\n\n"
        f"Выберите отрасль для управления 👇",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )


async def handle_industry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = load_data()
    player = get_player(data, user.id)

    key = query.data.split("_")[1]
    industry = player["industries"][key]
    config = INDUSTRIES[key]

    # Расчёт дохода
    elapsed = time.time() - industry["last_collect"]
    income = config["base_income"] * industry["level"] * int(elapsed // 10)  # каждые 10 сек

    text = (
        f"{config['name']}\n\n"
        f"🏗 Уровень: {industry['level']}\n"
        f"💵 Потенциальный доход: {income} BSS\n"
        f"💰 Ваш баланс: {player['balance']} BSS\n"
    )

    keyboard = [
        [InlineKeyboardButton("📥 Собрать доход", callback_data=f"collect_{key}")],
        [InlineKeyboardButton("⚙ Улучшить", callback_data=f"upgrade_{key}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_main")]
    ]

    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))


async def collect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = load_data()
    player = get_player(data, user.id)

    key = query.data.split("_")[1]
    industry = player["industries"][key]
    config = INDUSTRIES[key]

    elapsed = time.time() - industry["last_collect"]
    income = config["base_income"] * industry["level"] * int(elapsed // 10)
    if income > 0:
        player["balance"] += income
        industry["last_collect"] = time.time()
        save_data(data)
        await query.answer(f"Вы собрали {income} BSS 💰")
    else:
        await query.answer("Пока нечего собирать 😅", show_alert=True)

    await handle_industry(update, context)


async def upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = load_data()
    player = get_player(data, user.id)

    key = query.data.split("_")[1]
    industry = player["industries"][key]
    config = INDUSTRIES[key]

    cost = config["base_cost"] * industry["level"]
    if player["balance"] >= cost:
        player["balance"] -= cost
        industry["level"] += 1
        save_data(data)
        await query.answer(f"✅ Уровень повышен! Теперь {industry['level']} уровень 🚀")
    else:
        await query.answer("Недостаточно BSS 😔", show_alert=True)

    await handle_industry(update, context)


async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = load_data()
    player = get_player(data, user.id)
    save_data(data)

    keyboard = [
        [InlineKeyboardButton(ind["name"], callback_data=f"industry_{key}")]
        for key, ind in INDUSTRIES.items()
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        f"🏙 Главное меню\n\n"
        f"💰 Баланс: {player['balance']} BSS\n"
        f"Выберите отрасль для управления 👇",
        reply_markup=reply_markup
    )


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_industry, pattern="^industry_"))
    app.add_handler(CallbackQueryHandler(collect, pattern="^collect_"))
    app.add_handler(CallbackQueryHandler(upgrade, pattern="^upgrade_"))
    app.add_handler(CallbackQueryHandler(back_main, pattern="^back_main"))

    app.run_polling()


if __name__ == "__main__":
    main()
