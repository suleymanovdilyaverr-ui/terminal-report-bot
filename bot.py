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
    ADMIN_IDS = {
        int(x.strip())
        for x in ADMIN_IDS_RAW.split(",")
        if x.strip().isdigit()
    }

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

FIELD_NAMES = {
    "terminal": "🏧 Терминал",
    "total_amount": "💰 Общая сумма",
    "change_100_before": "💵 100 ₽ было",
    "change_100_added": "💵 100 ₽ добавили",
    "change_1000_before": "💸 1000 ₽ было",
    "change_1000_added": "💸 1000 ₽ добавили",
    "salary": "👤 ЗП себе",
    "additional": "📝 Дополнительно",
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
            created_at TEXT NOT NULL,
            deleted INTEGER DEFAULT 0
        )
    """)

    try:
        cur.execute("ALTER TABLE reports ADD COLUMN deleted INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

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


def confirm_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Отправить", callback_data="confirm_send"),
                InlineKeyboardButton(text="✏️ Исправить", callback_data="confirm_edit"),
            ],
            [
                InlineKeyboardButton(text="❌ Полностью отменить", callback_data="confirm_cancel")
            ],
        ]
    )


def edit_fields_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🏧 Терминал", callback_data="edit_terminal"),
                InlineKeyboardButton(text="💰 Общая сумма", callback_data="edit_total_amount"),
            ],
            [
                InlineKeyboardButton(text="💵 100 было", callback_data="edit_change_100_before"),
                InlineKeyboardButton(text="💵 100 добавили", callback_data="edit_change_100_added"),
            ],
            [
                InlineKeyboardButton(text="💸 1000 было", callback_data="edit_change_1000_before"),
                InlineKeyboardButton(text="💸 1000 добавили", callback_data="edit_change_1000_added"),
            ],
            [
                InlineKeyboardButton(text="👤 ЗП", callback_data="edit_salary"),
                InlineKeyboardButton(text="📝 Дополнительно", callback_data="edit_additional"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад к проверке", callback_data="back_to_preview")
            ],
        ]
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
            [
                InlineKeyboardButton(text="🏧 По терминалу", callback_data="admin_terminal_help"),
            ],
        ]
    )


def report_delete_keyboard(report_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Удалить отчет",
                    callback_data=f"delete_report_{report_id}"
                )
            ]
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


def calculate_report(data: dict) -> dict:
    terminal = data["terminal"]
    total_amount = data["total_amount"]

    change_100_before = data["change_100_before"]
    change_100_added = data["change_100_added"]
    change_100_after = change_100_before + change_100_added

    change_1000_before = data["change_1000_before"]
    change_1000_added = data["change_1000_added"]
    change_1000_after = change_1000_before + change_1000_added

    salary = data["salary"]

    _, additional_total, additional_text = parse_additional(
        data.get("additional", "нет")
    )

    withheld_total = (
        change_100_added
        + change_1000_added
        + salary
        + additional_total
    )

    final_amount = total_amount - withheld_total

    return {
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
    }


def build_report_text(calc: dict, sender_name: str, report_id: int | None = None) -> str:
    now = datetime.now().strftime("%d.%m.%Y %H:%M")

    report_number = f"📄 <b>Отчет №{report_id}</b>\n\n" if report_id else ""

    return f"""
{report_number}📊 <b>ОТЧЕТ ПО ТЕРМИНАЛУ</b>

🏧 <b>Терминал:</b> {calc["terminal"]}

💰 <b>Общая сумма:</b>
{format_money(calc["total_amount"])}

💵 <b>Сдача по 100 ₽</b>
Было: {format_money(calc["change_100_before"])}
Добавлено: {format_money(calc["change_100_added"])}
Стало: {format_money(calc["change_100_after"])}

💸 <b>Сдача по 1000 ₽</b>
Было: {format_money(calc["change_1000_before"])}
Добавлено: {format_money(calc["change_1000_added"])}
Стало: {format_money(calc["change_1000_after"])}

👤 <b>ЗП себе:</b>
{format_money(calc["salary"])}

📝 <b>Дополнительно:</b>
{calc["additional_text"]}

━━━━━━━━━━━━━━

📉 <b>Удержано:</b>
• Сдача 100 ₽: {format_money(calc["change_100_added"])}
• Сдача 1000 ₽: {format_money(calc["change_1000_added"])}
• ЗП: {format_money(calc["salary"])}
• Доп. расходы: {format_money(calc["additional_total"])}

💵 <b>НА РУКАХ:</b>
<b>{format_money(calc["final_amount"])}</b>

👤 <b>Отчет отправил:</b> {sender_name}
🕒 {now}
""".strip()


def save_report(calc: dict, user_id: int, user_name: str) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
            created_at,
            deleted
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
    """, (
        calc["terminal"],
        calc["total_amount"],
        calc["change_100_before"],
        calc["change_100_added"],
        calc["change_100_after"],
        calc["change_1000_before"],
        calc["change_1000_added"],
        calc["change_1000_after"],
        calc["salary"],
        calc["additional_text"],
        calc["additional_total"],
        calc["withheld_total"],
        calc["final_amount"],
        user_id,
        user_name,
        created_at,
    ))

    report_id = cur.lastrowid

    conn.commit()
    conn.close()

    return report_id


def mark_report_deleted(report_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        "UPDATE reports SET deleted = 1 WHERE id = ?",
        (report_id,)
    )

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
        WHERE created_at >= ? AND deleted = 0
    """, (start_text,))

    count, total_sum, withheld_sum, final_sum = cur.fetchone()

    cur.execute("""
        SELECT terminal, COALESCE(SUM(final_amount), 0)
        FROM reports
        WHERE created_at >= ? AND deleted = 0
        GROUP BY terminal
        ORDER BY SUM(final_amount) DESC
        LIMIT 10
    """, (start_text,))

    terminals = cur.fetchall()

    cur.execute("""
        SELECT user_name, COALESCE(SUM(final_amount), 0)
        FROM reports
        WHERE created_at >= ? AND deleted = 0
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

💵 <b>НА РУКАХ:</b>
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
        SELECT id, terminal, final_amount, user_name, created_at
        FROM reports
        WHERE deleted = 0
        ORDER BY id DESC
        LIMIT 10
    """)

    rows = cur.fetchall()
    conn.close()

    if not rows:
        return "📋 Последних отчетов пока нет."

    text = "📋 <b>ПОСЛЕДНИЕ 10 ОТЧЕТОВ</b>\n\n"

    for report_id, terminal, final_amount, user_name, created_at in rows:
        text += (
            f"📄 №{report_id}\n"
            f"🏧 {terminal} — <b>{format_money(final_amount)}</b>\n"
            f"👤 {user_name}\n"
            f"🕒 {created_at}\n\n"
        )

    return text.strip()


def build_terminal_summary(terminal: str) -> str:
    start = get_period_start("month")
    start_text = start.strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT 
            COUNT(id),
            COALESCE(SUM(total_amount), 0),
            COALESCE(SUM(withheld_total), 0),
            COALESCE(SUM(final_amount), 0)
        FROM reports
        WHERE terminal = ? AND created_at >= ? AND deleted = 0
    """, (terminal, start_text))

    count, total_sum, withheld_sum, final_sum = cur.fetchone()

    cur.execute("""
        SELECT id, final_amount, user_name, created_at
        FROM reports
        WHERE terminal = ? AND created_at >= ? AND deleted = 0
        ORDER BY id DESC
        LIMIT 10
    """, (terminal, start_text))

    rows = cur.fetchall()

    conn.close()

    last_text = "\n".join(
        f"• №{report_id} — {format_money(final_amount)} — {user_name} — {created_at}"
        for report_id, final_amount, user_name, created_at in rows
    ) or "• Нет отчетов"

    return f"""
🏧 <b>ОТЧЕТ ПО ТЕРМИНАЛУ: {terminal}</b>

Период: текущий месяц

📌 Количество отчетов: <b>{count}</b>

💰 Общая сумма:
<b>{format_money(total_sum)}</b>

📉 Всего удержано:
<b>{format_money(withheld_sum)}</b>

💵 <b>НА РУКАХ:</b>
<b>{format_money(final_sum)}</b>

━━━━━━━━━━━━━━

📋 <b>Последние отчеты:</b>
{last_text}
""".strip()


def get_report_by_id(report_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT 
            id,
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
            user_name,
            created_at,
            deleted
        FROM reports
        WHERE id = ?
    """, (report_id,))

    row = cur.fetchone()
    conn.close()

    return row


def build_report_from_db(report_id: int) -> str:
    row = get_report_by_id(report_id)

    if not row:
        return "❌ Отчет не найден."

    (
        rid,
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
        user_name,
        created_at,
        deleted,
    ) = row

    if deleted:
        return f"🗑 Отчет №{rid} удален."

    return f"""
📄 <b>ОТЧЕТ №{rid}</b>

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

💵 <b>НА РУКАХ:</b>
<b>{format_money(final_amount)}</b>

👤 <b>Отчет отправил:</b> {user_name}
🕒 {created_at}
""".strip()


async def ask_step(message: Message, state: FSMContext, step: str):
    await state.update_data(current_step=step)
    await state.set_state(STATE_BY_STEP[step])
    await message.answer(QUESTION_BY_STEP[step], reply_markup=main_keyboard())


async def go_back(message: Message, state: FSMContext):
    data = await state.get_data()
    current_step = data.get("current_step")

    if not current_step:
        await message.answer(
            "Сейчас нет активного отчета. Нажмите «📊 Новый отчет».",
            reply_markup=start_keyboard()
        )
        return

    current_index = STEPS.index(current_step)

    if current_index == 0:
        await message.answer(
            "Вы уже на первом вопросе.",
            reply_markup=main_keyboard()
        )
        return

    previous_step = STEPS[current_index - 1]
    await ask_step(message, state, previous_step)


async def show_preview(message: Message, state: FSMContext):
    data = await state.get_data()

    user_name = (
        message.from_user.full_name
        or message.from_user.username
        or "Неизвестно"
    )

    calc = calculate_report(data)
    preview = build_report_text(calc, user_name, report_id=None)

    await message.answer(
        "📋 <b>Проверьте отчет перед отправкой:</b>\n\n" + preview,
        reply_markup=confirm_keyboard()
    )


async def process_step(message: Message, state: FSMContext, step: str, value):
    await state.update_data(**{step: value})

    data = await state.get_data()

    if data.get("edit_mode"):
        await state.update_data(edit_mode=False, editing_field=None)
        await show_preview(message, state)
        return

    current_index = STEPS.index(step)

    if current_index + 1 >= len(STEPS):
        await show_preview(message, state)
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
    await message.answer(
        f"Ваш Telegram ID:\n<code>{message.from_user.id}</code>"
    )


@dp.message(Command("chatid"))
async def chatid(message: Message):
    await message.answer(
        f"ID этого чата:\n<code>{message.chat.id}</code>"
    )


@dp.message(Command("cancel"))
async def full_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "❌ Отчет полностью отменен.",
        reply_markup=start_keyboard()
    )


@dp.message(Command("admin"))
async def admin_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(
            "⛔ У вас нет доступа к админ-панели.\n\n"
            "Узнайте свой ID через /myid и добавьте его в ADMIN_IDS."
        )
        return

    await message.answer(
        "👨‍💼 <b>Админ панель</b>\n\nВыберите период:",
        reply_markup=admin_keyboard()
    )


@dp.message(Command("terminal"))
async def terminal_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return

    parts = message.text.split(maxsplit=1)

    if len(parts) < 2:
        await message.answer(
            "Напишите так:\n<code>/terminal Т-15</code>"
        )
        return

    terminal = parts[1].strip()
    await message.answer(build_terminal_summary(terminal))


@dp.message(Command("report"))
async def report_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return

    parts = message.text.split(maxsplit=1)

    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer(
            "Напишите так:\n<code>/report 154</code>"
        )
        return

    report_id = int(parts[1].strip())
    await message.answer(build_report_from_db(report_id))


@dp.message(F.text == "👨‍💼 Админ панель")
async def admin_button(message: Message):
    await admin_command(message)


@dp.message(F.text == "📊 Новый отчет")
async def new_report(message: Message, state: FSMContext):
    await state.clear()
    await ask_step(message, state, "terminal")


@dp.message(F.text == "❌ Отмена")
async def cancel_button_go_back(message: Message, state: FSMContext):
    await go_back(message, state)


@dp.message(F.text == "⬅️ Назад")
async def back_button(message: Message, state: FSMContext):
    await go_back(message, state)


@dp.callback_query(F.data == "confirm_send")
async def confirm_send(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()

    user_name = (
        callback.from_user.full_name
        or callback.from_user.username
        or "Неизвестно"
    )
    user_id = callback.from_user.id

    calc = calculate_report(data)
    report_id = save_report(calc, user_id, user_name)

    report_text = build_report_text(calc, user_name, report_id=report_id)

    target_chat_id = int(REPORT_CHAT_ID) if REPORT_CHAT_ID else callback.message.chat.id

    await bot.send_message(
        chat_id=target_chat_id,
        text=report_text,
        reply_markup=report_delete_keyboard(report_id)
    )

    await callback.message.answer(
        f"✅ Отчет №{report_id} отправлен.",
        reply_markup=start_keyboard()
    )

    await state.clear()
    await callback.answer()


@dp.callback_query(F.data == "confirm_edit")
async def confirm_edit(callback: CallbackQuery):
    await callback.message.edit_text(
        "✏️ <b>Что нужно исправить?</b>",
        reply_markup=edit_fields_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data == "back_to_preview")
async def back_to_preview(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()

    user_name = (
        callback.from_user.full_name
        or callback.from_user.username
        or "Неизвестно"
    )

    calc = calculate_report(data)
    preview = build_report_text(calc, user_name, report_id=None)

    await callback.message.edit_text(
        "📋 <b>Проверьте отчет перед отправкой:</b>\n\n" + preview,
        reply_markup=confirm_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data == "confirm_cancel")
async def confirm_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer(
        "❌ Отчет полностью отменен.",
        reply_markup=start_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("edit_"))
async def edit_field_callback(callback: CallbackQuery, state: FSMContext):
    field = callback.data.replace("edit_", "")

    if field not in STATE_BY_STEP:
        await callback.answer("Неизвестное поле", show_alert=True)
        return

    await state.update_data(
        current_step=field,
        edit_mode=True,
        editing_field=field
    )

    await state.set_state(STATE_BY_STEP[field])

    await callback.message.answer(
        f"✏️ Исправляем: <b>{FIELD_NAMES[field]}</b>\n\n"
        f"{QUESTION_BY_STEP[field]}",
        reply_markup=main_keyboard()
    )

    await callback.answer()


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

    if action == "terminal_help":
        await callback.message.edit_text(
            "🏧 <b>Отчет по терминалу</b>\n\n"
            "Напишите команду:\n"
            "<code>/terminal Т-15</code>\n\n"
            "Пример:\n"
            "<code>/terminal Т-15</code>",
            reply_markup=admin_keyboard()
        )
        await callback.answer()
        return


@dp.callback_query(F.data.startswith("delete_report_"))
async def delete_report_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Удалять отчеты может только админ.", show_alert=True)
        return

    report_id_text = callback.data.replace("delete_report_", "")

    if not report_id_text.isdigit():
        await callback.answer("Ошибка номера отчета.", show_alert=True)
        return

    report_id = int(report_id_text)
    mark_report_deleted(report_id)

    try:
        await callback.message.edit_text(
            f"🗑 <b>Отчет №{report_id} удален администратором.</b>\n\n"
            f"👤 Удалил: {callback.from_user.full_name}"
        )
    except Exception:
        pass

    await callback.answer("Отчет удален.")


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
