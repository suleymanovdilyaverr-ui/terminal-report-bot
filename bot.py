import asyncio
import os
import re
import sqlite3
from datetime import datetime, timedelta

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from aiogram.client.default import DefaultBotProperties


BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))
REPORT_CHAT_ID = os.getenv("REPORT_CHAT_ID", "").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()

ADMIN_IDS = set()
if ADMIN_IDS_RAW:
    ADMIN_IDS = {int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit()}

DB_PATH = "reports.db"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")


bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()


class ReportForm(StatesGroup):
    terminal = State()
    total_amount = State()
    change_100_before = State()
    change_100_added = State()
    change_1000_before = State()
    change_1000_added = State()
    salary = State()
    additional = State()


STEPS = [
    "terminal",
    "total_amount",
    "change_100_before",
    "change_100_added",
    "change_1000_before",
    "change_1000_added",
    "salary",
    "additional",
]

STATE_BY_STEP = {
    "terminal": ReportForm.terminal,
    "total_amount": ReportForm.total_amount,
    "change_100_before": ReportForm.change_100_before,
    "change_100_added": ReportForm.change_100_added,
    "change_1000_before": ReportForm.change_1000_before,
    "change_1000_added": ReportForm.change_1000_added,
    "salary": ReportForm.salary,
    "additional": ReportForm.additional,
}

QUESTION_BY_STEP = {
    "terminal": "🏧 Введите название терминала:\nНапример: <b>Т-15</b>",
    "total_amount": "💰 Введите общую сумму:\nНапример: <b>150000</b>",
    "change_100_before": "💵 Сдача по 100 ₽\n\nСколько было?\nНапример: <b>5000</b>",
    "change_100_added": "💵 Сдача по 100 ₽\n\nСколько добавили?\nНапример: <b>1000</b>",
    "change_1000_before": "💸 Сдача по 1000 ₽\n\nСколько было?\nНапример: <b>10000</b>",
    "change_1000_added": "💸 Сдача по 1000 ₽\n\nСколько добавили?\nНапример: <b>3000</b>",
    "salary": "👤 ЗП себе:\nНапример: <b>5000</b>",
    "additional": (
        "📝 Дополнительная информация:\n\n"
        "Например:\n<b>продавцу 4000, чеки 3000</b>\n\n"
        "Если ничего нет — напишите <b>нет</b>"
    ),
}


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            terminal TEXT NOT NULL,
            total_amount INTEGER NOT NULL,
            change_100_before INTEGER NOT NULL,
            change_100_added INTEGER NOT NULL,
            change_100_after INTEGER NOT NULL,
            change_1000_before INTEGER NOT NULL,
            change_1000_added INTEGER NOT NULL,
            change_1000_after INTEGER NOT NULL,
            salary INTEGER NOT NULL,
            additional_text TEXT,
            additional_total INTEGER NOT NULL,
            withheld_total INTEGER NOT NULL,
            final_amount INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            user_name TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="❌ Отмена")]
        ],
        resize_keyboard=True
    )


def start_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Новый отчет")],
            [KeyboardButton(text="👨‍💼 Админ панель")]
        ],
        resize_keyboard=True
    )


def admin_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📅 Сегодня", callback_data="admin_today"),
                InlineKeyboardButton(text="📆 Неделя", callback_data="admin_week"),
            ],
            [
                InlineKeyboardButton(text="🗓 Месяц", callback_data="admin_month"),
                InlineKeyboardButton(text="📋 Последние 10", callback_data="admin_last10"),
            ],
        ]
    )


def parse_money(text: str) -> int:
    cleaned = re.sub(r"[^\d]", "", text or "")
    if not cleaned:
        raise ValueError("Сумма не найдена")
    return int(cleaned)


def format_money(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " ₽"


def parse_additional(text: str):
    text = (text or "").strip()

    if text.lower() in ["нет", "no", "-", "0", "ничего"]:
        return [], 0, "• Нет"

    parts = re.split(r"[,;\n]+", text)
    result = []

    for part in parts:
        part = part.strip()
        if not part:
            continue

        match = re.search(r"(.+?)[\s:—-]+([\d\s.,]+)\s*₽?$", part)
        if not match:
            continue

        name = match.group(1).strip().capitalize()
        amount = parse_money(match.group(2))
        result.append((name, amount))

    total = sum(amount for _, amount in result)

    if not result:
        return [], 0, "• Не удалось распознать"

    text_result = "\n".join(
        f"• {name}: {format_money(amount)}"
        for name, amount in result
    )

    return result, total, text_result


def build_report(data: dict, sender_name: str) -> tuple[str, dict]:
    terminal = data["terminal"]
    total_amount = data["total_amount"]

    change_100_before = data["change_100_before"]
    change_100_added = data["change_100_added"]
    change_100_after = change_100_before + change_100_added

    change_1000_before = data["change_1000_before"]
    change_1000_added = data["change_1000_added"]
    change_1000_after = change_1000_before + change_1000_added

    salary = data["salary"]

    additional_items, additional_total, additional_text = parse_additional(data.get("additional", "нет"))

    withheld_total = change_100_added + change_1000_added + salary + additional_total
    final_amount = total_amount - withheld_total

    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    report = f"""
📊 <b>ОТЧЕТ ПО ТЕРМИНАЛУ</b>

🏧 <b>Терминал:</b> {terminal}

💰 <b>Общая сумма:</b>
{format_money(total_amount)}

💵 <b>Сдача по 100 ₽</b>
Было: {format_money(change_100_before)}
Добавлено: {format_money(change_100_added)}
Стало: {format_money(change_100_after)}

💸 <b>Сдача по 1000 ₽</b>
Было: {format_money(change_1000_before)}
Добавлено: {format_money(change_1000_added)}
Стало: {format_money(change_1000_after)}

👤 <b>ЗП себе:</b>
{format_money(salary)}

📝 <b>Дополнительно:</b>
{additional_text}

━━━━━━━━━━━━━━

📉 <b>Удержано:</b>
• Сдача 100 ₽: {format_money(change_100_added)}
• Сдача 1000 ₽: {format_money(change_1000_added)}
• ЗП: {format_money(salary)}
• Доп. расходы: {format_money(additional_total)}

✅ <b>ИТОГО К СДАЧЕ:</b>
<b>{format_money(final_amount)}</b>

👤 <b>Отчет отправил:</b> {sender_name}
🕒 {now}
""".strip()

    db_data = {
        "terminal": terminal,
        "total_amount": total_amount,
        "change_100_before": change_100_before,
        "change_100_added": change_100_added,
        "change_100_after": change_100_after,
        "change_1000_before": change_1000_before,
        "change_1000_added": change_1000_added,
        "change_1000_after": change_1000_after,
        "salary": salary,
        "additional_text": additional_text,
        "additional_total": additional_total,
        "withheld_total": withheld_total,
        "final_amount": final_amount,
        "created_at": created_at,
    }

    return report, db_data


def save_report(db_data: dict, user_id: int, user_name: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO reports (
            terminal,
            total_amount,
            change_100_before,
            change_100_added,
            change_100_after,
            change_1000_before,
            change_1000_added,
            change_1000_after,
            salary,
            additional_text,
            additional_total,
            withheld_total,
            final_amount,
            user_id,
            user_name,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        db_data["terminal"],
        db_data["total_amount"],
        db_data["change_100_before"],
        db_data["change_100_added"],
        db_data["change_100_after"],
        db_data["change_1000_before"],
        db_data["change_1000_added"],
        db_data["change_1000_after"],
        db_data["salary"],
        db_data["additional_text"],
        db_data["additional_total"],
        db_data["withheld_total"],
        db_data["final_amount"],
        user_id,
        user_name,
        db_data["created_at"],
    ))

    conn.commit()
    conn.close()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def get_period_start(period: str) -> datetime:
    now = datetime.now()

    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    if period == "week":
        start = now - timedelta(days=now.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0)

    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def build_admin_summary(period: str) -> str:
    start = get_period_start(period)
    start_text = start.strftime("%Y-%m-%d %H:%M:%S")

    title_by_period = {
        "today": "📅 ОТЧЕТЫ ЗА СЕГОДНЯ",
        "week": "📆 ОТЧЕТЫ ЗА НЕДЕЛЮ",
        "month": "🗓 ОТЧЕТЫ ЗА МЕСЯЦ",
    }

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT 
            COUNT(id),
            COALESCE(SUM(total_amount), 0),
            COALESCE(SUM(withheld_total), 0),
            COALESCE(SUM(final_amount), 0)
        FROM reports
        WHERE created_at >= ?
    """, (start_text,))
    count, total_sum, withheld_sum, final_sum = cur.fetchone()

    cur.execute("""
        SELECT terminal, COALESCE(SUM(final_amount), 0)
        FROM reports
        WHERE created_at >= ?
        GROUP BY terminal
        ORDER BY SUM(final_amount) DESC
        LIMIT 10
    """, (start_text,))
    terminals = cur.fetchall()

    cur.execute("""
        SELECT user_name, COALESCE(SUM(final_amount), 0)
        FROM reports
        WHERE created_at >= ?
        GROUP BY user_name
        ORDER BY SUM(final_amount) DESC
        LIMIT 10
    """, (start_text,))
    users = cur.fetchall()

    conn.close()

    terminals_text = "\n".join(
        f"• {terminal}: {format_money(amount)}"
        for terminal, amount in terminals
    ) or "• Нет данных"

    users_text = "\n".join(
        f"• {user_name}: {format_money(amount)}"
        for user_name, amount in users
    ) or "• Нет данных"

    return f"""
<b>{title_by_period[period]}</b>

📌 Количество отчетов: <b>{count}</b>

💰 Общая сумма:
<b>{format_money(total_sum)}</b>

📉 Всего удержано:
<b>{format_money(withheld_sum)}</b>

✅ Итого к сдаче:
<b>{format_money(final_sum)}</b>

━━━━━━━━━━━━━━

🏧 <b>По терминалам:</b>
{terminals_text}

👤 <b>По сотрудникам:</b>
{users_text}
""".strip()


def build_last10_reports() -> str:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT terminal, final_amount, user_name, created_at
        FROM reports
        ORDER BY id DESC
        LIMIT 10
    """)

    rows = cur.fetchall()
    conn.close()

    if not rows:
        return "📋 Последних отчетов пока нет."

    text = "📋 <b>ПОСЛЕДНИЕ 10 ОТЧЕТОВ</b>\n\n"

    for terminal, final_amount, user_name, created_at in rows:
        text += (
            f"🏧 {terminal} — <b>{format_money(final_amount)}</b>\n"
            f"👤 {user_name}\n"
            f"🕒 {created_at}\n\n"
        )

    return text.strip()


async def ask_step(message: Message, state: FSMContext, step: str):
    await state.update_data(current_step=step)
    await state.set_state(STATE_BY_STEP[step])
    await message.answer(QUESTION_BY_STEP[step], reply_markup=main_keyboard())


async def go_back(message: Message, state: FSMContext):
    data = await state.get_data()
    current_step = data.get("current_step", "terminal")

    current_index = STEPS.index(current_step)

    if current_index == 0:
        await message.answer("Вы уже на первом шаге.", reply_markup=main_keyboard())
        return

    previous_step = STEPS[current_index - 1]
    await ask_step(message, state, previous_step)


async def process_step(message: Message, state: FSMContext, step: str, value):
    await state.update_data(**{step: value})

    current_index = STEPS.index(step)

    if current_index + 1 >= len(STEPS):
        data = await state.get_data()

        user_name = message.from_user.full_name or message.from_user.username or "Неизвестно"
        user_id = message.from_user.id

        report, db_data = build_report(data, user_name)
        save_report(db_data, user_id, user_name)

        target_chat_id = int(REPORT_CHAT_ID) if REPORT_CHAT_ID else message.chat.id
        await bot.send_message(chat_id=target_chat_id, text=report)

        if REPORT_CHAT_ID:
            await message.answer("✅ Отчет отправлен в группу.", reply_markup=start_keyboard())
        else:
            await message.answer("✅ Отчет сохранен.", reply_markup=start_keyboard())

        await state.clear()
        return

    next_step = STEPS[current_index + 1]
    await ask_step(message, state, next_step)


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "✅ Бот работает.\n\nВыберите действие:",
        reply_markup=start_keyboard()
    )


@dp.message(Command("myid"))
async def myid(message: Message):
    await message.answer(f"Ваш Telegram ID:\n<code>{message.from_user.id}</code>")


@dp.message(Command("chatid"))
async def chatid(message: Message):
    await message.answer(f"ID этого чата:\n<code>{message.chat.id}</code>")


@dp.message(Command("admin"))
async def admin_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к админ-панели.\n\nУзнайте свой ID через /myid и добавьте его в ADMIN_IDS.")
        return

    await message.answer("👨‍💼 <b>Админ панель</b>\n\nВыберите период:", reply_markup=admin_keyboard())


@dp.message(F.text == "👨‍💼 Админ панель")
async def admin_button(message: Message):
    await admin_command(message)


@dp.message(F.text == "📊 Новый отчет")
async def new_report(message: Message, state: FSMContext):
    await state.clear()
    await ask_step(message, state, "terminal")


@dp.message(F.text == "❌ Отмена")
async def cancel_report(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отчет отменен.", reply_markup=start_keyboard())


@dp.message(F.text == "⬅️ Назад")
async def back_button(message: Message, state: FSMContext):
    await go_back(message, state)


@dp.callback_query(F.data.startswith("admin_"))
async def admin_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    action = callback.data.replace("admin_", "")

    if action in ["today", "week", "month"]:
        text = build_admin_summary(action)
        await callback.message.edit_text(text, reply_markup=admin_keyboard())
        await callback.answer()
        return

    if action == "last10":
        text = build_last10_reports()
        await callback.message.edit_text(text, reply_markup=admin_keyboard())
        await callback.answer()
        return


@dp.message(ReportForm.terminal)
async def terminal_step(message: Message, state: FSMContext):
    value = message.text.strip()
    await process_step(message, state, "terminal", value)


@dp.message(ReportForm.total_amount)
async def total_amount_step(message: Message, state: FSMContext):
    try:
        value = parse_money(message.text)
    except ValueError:
        await message.answer("Введите сумму цифрами. Например: <b>150000</b>")
        return

    await process_step(message, state, "total_amount", value)


@dp.message(ReportForm.change_100_before)
async def change_100_before_step(message: Message, state: FSMContext):
    try:
        value = parse_money(message.text)
    except ValueError:
        await message.answer("Введите сумму цифрами. Например: <b>5000</b>")
        return

    await process_step(message, state, "change_100_before", value)


@dp.message(ReportForm.change_100_added)
async def change_100_added_step(message: Message, state: FSMContext):
    try:
        value = parse_money(message.text)
    except ValueError:
        await message.answer("Введите сумму цифрами. Например: <b>1000</b>")
        return

    await process_step(message, state, "change_100_added", value)


@dp.message(ReportForm.change_1000_before)
async def change_1000_before_step(message: Message, state: FSMContext):
    try:
        value = parse_money(message.text)
    except ValueError:
        await message.answer("Введите сумму цифрами. Например: <b>10000</b>")
        return

    await process_step(message, state, "change_1000_before", value)


@dp.message(ReportForm.change_1000_added)
async def change_1000_added_step(message: Message, state: FSMContext):
    try:
        value = parse_money(message.text)
    except ValueError:
        await message.answer("Введите сумму цифрами. Например: <b>3000</b>")
        return

    await process_step(message, state, "change_1000_added", value)


@dp.message(ReportForm.salary)
async def salary_step(message: Message, state: FSMContext):
    try:
        value = parse_money(message.text)
    except ValueError:
        await message.answer("Введите сумму цифрами. Например: <b>5000</b>")
        return

    await process_step(message, state, "salary", value)


@dp.message(ReportForm.additional)
async def additional_step(message: Message, state: FSMContext):
    value = message.text.strip()
    await process_step(message, state, "additional", value)


async def health_check(request):
    return web.Response(text="Bot is running")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    print(f"web server started on port {PORT}")


async def main():
    init_db()
    await start_web_server()
    print("telegram bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
