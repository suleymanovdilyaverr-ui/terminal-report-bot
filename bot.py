import asyncio
import os
import re
import sqlite3
from datetime import datetime, timedelta
from html import escape

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
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
)


# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", "10000"))

REPORT_CHAT_ID_RAW = os.getenv("REPORT_CHAT_ID", "").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()

DB_PATH = "reports.db"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не найден в Environment Variables")


def parse_admin_ids(raw: str) -> set[int]:
    result = set()

    for item in raw.split(","):
        item = item.strip()

        if item.lstrip("-").isdigit():
            result.add(int(item))

    return result


ADMIN_IDS = parse_admin_ids(ADMIN_IDS_RAW)

REPORT_CHAT_ID = None
if REPORT_CHAT_ID_RAW and REPORT_CHAT_ID_RAW.lstrip("-").isdigit():
    REPORT_CHAT_ID = int(REPORT_CHAT_ID_RAW)


bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

dp = Dispatcher()


TERMINALS = [
    "20-й",
    "Бирлога 1",
    "Тысячник",
    "Сидоровка",
]


# ============================================================
# СОСТОЯНИЯ
# ============================================================

class TerminalReportForm(StatesGroup):
    terminal = State()
    total_amount = State()
    change_100_before = State()
    change_100_added = State()
    change_1000_before = State()
    change_1000_added = State()
    transfer_answer = State()
    transfer_source = State()
    salary = State()
    additional = State()


class RentForm(StatesGroup):
    terminal = State()
    amount = State()
    rent_period = State()
    comment = State()


TERMINAL_STEPS = [
    "terminal",
    "total_amount",
    "change_100_before",
    "change_100_added",
    "change_1000_before",
    "change_1000_added",
    "transfer_answer",
    "transfer_source",
    "salary",
    "additional",
]

TERMINAL_STATE_BY_STEP = {
    "terminal": TerminalReportForm.terminal,
    "total_amount": TerminalReportForm.total_amount,
    "change_100_before": TerminalReportForm.change_100_before,
    "change_100_added": TerminalReportForm.change_100_added,
    "change_1000_before": TerminalReportForm.change_1000_before,
    "change_1000_added": TerminalReportForm.change_1000_added,
    "transfer_answer": TerminalReportForm.transfer_answer,
    "transfer_source": TerminalReportForm.transfer_source,
    "salary": TerminalReportForm.salary,
    "additional": TerminalReportForm.additional,
}

TERMINAL_FIELD_NAMES = {
    "terminal": "🏧 Терминал",
    "total_amount": "💰 Общая сумма",
    "change_100_before": "💵 Сдача 100 ₽ — было",
    "change_100_added": "💵 Сдача 100 ₽ — добавлено",
    "change_1000_before": "💸 Сдача 1000 ₽ — было",
    "change_1000_added": "💸 Сдача 1000 ₽ — добавлено",
    "transfer_answer": "🔄 Сдача с другого терминала",
    "transfer_source": "📤 Терминал-источник",
    "salary": "👤 ЗП себе",
    "additional": "📝 Дополнительно",
}

TERMINAL_QUESTIONS = {
    "terminal": (
        "🏧 <b>Выберите терминал:</b>\n\n"
        "1. 20-й\n"
        "2. Бирлога 1\n"
        "3. Тысячник\n"
        "4. Сидоровка"
    ),
    "total_amount": (
        "💰 Введите общую сумму:\n\n"
        "Например: <b>150000</b>"
    ),
    "change_100_before": (
        "💵 <b>Сдача по 100 ₽</b>\n\n"
        "Сколько было?\n"
        "Например: <b>5000</b>"
    ),
    "change_100_added": (
        "💵 <b>Сдача по 100 ₽</b>\n\n"
        "Сколько добавили?\n"
        "Например: <b>1000</b>"
    ),
    "change_1000_before": (
        "💸 <b>Сдача по 1000 ₽</b>\n\n"
        "Сколько было?\n"
        "Например: <b>10000</b>"
    ),
    "change_1000_added": (
        "💸 <b>Сдача по 1000 ₽</b>\n\n"
        "Сколько добавили?\n"
        "Например: <b>3000</b>"
    ),
    "transfer_answer": (
        "🔄 <b>Добавленную сдачу взяли с другого терминала?</b>\n\n"
        "Ответьте кнопкой ниже."
    ),
    "transfer_source": (
        "📤 <b>Выберите терминал, откуда взяли сдачу:</b>"
    ),
    "salary": (
        "👤 Введите сумму ЗП себе:\n\n"
        "Например: <b>5000</b>"
    ),
    "additional": (
        "📝 Введите дополнительные расходы:\n\n"
        "Например:\n"
        "<b>продавцу 4000, чеки 3000</b>\n\n"
        "Если дополнительных расходов нет, напишите:\n"
        "<b>нет</b>"
    ),
}


RENT_STEPS = [
    "terminal",
    "amount",
    "rent_period",
    "comment",
]

RENT_STATE_BY_STEP = {
    "terminal": RentForm.terminal,
    "amount": RentForm.amount,
    "rent_period": RentForm.rent_period,
    "comment": RentForm.comment,
}

RENT_FIELD_NAMES = {
    "terminal": "🏧 Терминал",
    "amount": "💰 Сумма аренды",
    "rent_period": "📅 Период аренды",
    "comment": "📝 Комментарий",
}

RENT_QUESTIONS = {
    "terminal": (
        "🏧 <b>Выберите терминал:</b>\n\n"
        "1. 20-й\n"
        "2. Бирлога 1\n"
        "3. Тысячник\n"
        "4. Сидоровка"
    ),
    "amount": (
        "💰 Введите сумму аренды:\n\n"
        "Например: <b>25000</b>"
    ),
    "rent_period": (
        "📅 За какой период оплачена аренда?\n\n"
        "Например: <b>Июнь 2026</b>"
    ),
    "comment": (
        "📝 Введите комментарий:\n\n"
        "Например: <b>Передано владельцу помещения</b>\n\n"
        "Если комментария нет, напишите:\n"
        "<b>нет</b>"
    ),
}


# ============================================================
# БАЗА ДАННЫХ
# ============================================================

def get_connection():
    return sqlite3.connect(DB_PATH)


def column_exists(
    cursor: sqlite3.Cursor,
    table_name: str,
    column_name: str,
) -> bool:
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = cursor.fetchall()

    return any(column[1] == column_name for column in columns)


def init_db():
    conn = get_connection()
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
            deleted INTEGER NOT NULL DEFAULT 0
        )
    """)

    if not column_exists(cur, "reports", "deleted"):
        cur.execute("""
            ALTER TABLE reports
            ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0
        """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS terminal_transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER,
            source_terminal TEXT NOT NULL,
            destination_terminal TEXT NOT NULL,
            amount_100 INTEGER NOT NULL DEFAULT 0,
            amount_1000 INTEGER NOT NULL DEFAULT 0,
            total_amount INTEGER NOT NULL DEFAULT 0,
            user_id INTEGER NOT NULL,
            user_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            deleted INTEGER NOT NULL DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS rent_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            terminal TEXT NOT NULL,
            amount INTEGER NOT NULL,
            rent_period TEXT NOT NULL,
            comment TEXT,
            user_id INTEGER NOT NULL,
            user_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            deleted INTEGER NOT NULL DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# КЛАВИАТУРЫ
# ============================================================

def start_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Новый отчет")],
            [KeyboardButton(text="🏠 Оплата аренды")],
            [KeyboardButton(text="👨‍💼 Админ панель")],
        ],
        resize_keyboard=True,
    )


def form_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="⬅️ Назад"),
                KeyboardButton(text="❌ Отменить полностью"),
            ]
        ],
        resize_keyboard=True,
    )


def terminal_choice_keyboard(
    exclude_terminal: str | None = None,
) -> ReplyKeyboardMarkup:
    available = [
        terminal
        for terminal in TERMINALS
        if not exclude_terminal
        or terminal.casefold() != exclude_terminal.casefold()
    ]

    rows = [
        [KeyboardButton(text=terminal)]
        for terminal in available
    ]
    rows.append([KeyboardButton(text="❌ Отменить полностью")])

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def transfer_answer_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="✅ Да, с другого терминала"),
                KeyboardButton(text="❌ Нет, внешнее пополнение"),
            ],
            [
                KeyboardButton(text="⬅️ Назад"),
                KeyboardButton(text="❌ Отменить полностью"),
            ],
        ],
        resize_keyboard=True,
    )


def terminal_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Отправить",
                    callback_data="terminal_confirm_send",
                ),
                InlineKeyboardButton(
                    text="✏️ Исправить",
                    callback_data="terminal_confirm_edit",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data="terminal_confirm_cancel",
                )
            ],
        ]
    )


def terminal_edit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏧 Терминал",
                    callback_data="terminal_edit_terminal",
                ),
                InlineKeyboardButton(
                    text="💰 Общая сумма",
                    callback_data="terminal_edit_total_amount",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💵 100 ₽ было",
                    callback_data="terminal_edit_change_100_before",
                ),
                InlineKeyboardButton(
                    text="💵 100 ₽ добавлено",
                    callback_data="terminal_edit_change_100_added",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💸 1000 ₽ было",
                    callback_data="terminal_edit_change_1000_before",
                ),
                InlineKeyboardButton(
                    text="💸 1000 ₽ добавлено",
                    callback_data="terminal_edit_change_1000_added",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Источник сдачи",
                    callback_data="terminal_edit_transfer_source",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👤 ЗП",
                    callback_data="terminal_edit_salary",
                ),
                InlineKeyboardButton(
                    text="📝 Дополнительно",
                    callback_data="terminal_edit_additional",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к проверке",
                    callback_data="terminal_back_to_preview",
                )
            ],
        ]
    )


def rent_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Отправить",
                    callback_data="rent_confirm_send",
                ),
                InlineKeyboardButton(
                    text="✏️ Исправить",
                    callback_data="rent_confirm_edit",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data="rent_confirm_cancel",
                )
            ],
        ]
    )


def rent_edit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏧 Терминал",
                    callback_data="rent_edit_terminal",
                ),
                InlineKeyboardButton(
                    text="💰 Сумма",
                    callback_data="rent_edit_amount",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📅 Период",
                    callback_data="rent_edit_rent_period",
                ),
                InlineKeyboardButton(
                    text="📝 Комментарий",
                    callback_data="rent_edit_comment",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к проверке",
                    callback_data="rent_back_to_preview",
                )
            ],
        ]
    )


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📅 Сегодня",
                    callback_data="admin_reports_today",
                ),
                InlineKeyboardButton(
                    text="📆 Неделя",
                    callback_data="admin_reports_week",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗓 Месяц",
                    callback_data="admin_reports_month",
                ),
                InlineKeyboardButton(
                    text="📚 Всё время",
                    callback_data="admin_reports_all",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📋 Последние 10",
                    callback_data="admin_reports_last10",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Аренда",
                    callback_data="admin_rent_menu",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏧 Терминалы за всё время",
                    callback_data="admin_terminals_all",
                )
            ],
        ]
    )


def admin_rent_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📅 Сегодня",
                    callback_data="admin_rent_today",
                ),
                InlineKeyboardButton(
                    text="📆 Неделя",
                    callback_data="admin_rent_week",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗓 Месяц",
                    callback_data="admin_rent_month",
                ),
                InlineKeyboardButton(
                    text="📚 Всё время",
                    callback_data="admin_rent_all",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📋 Последние 10",
                    callback_data="admin_rent_last10",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data="admin_main_menu",
                )
            ],
        ]
    )


def terminal_delete_keyboard(report_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Удалить отчет",
                    callback_data=f"delete_report_{report_id}",
                )
            ]
        ]
    )


def rent_delete_keyboard(rent_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Удалить запись аренды",
                    callback_data=f"delete_rent_{rent_id}",
                )
            ]
        ]
    )


# ============================================================
# ОБЩИЕ ФУНКЦИИ
# ============================================================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def get_user_name(message_or_callback) -> str:
    user = message_or_callback.from_user

    return (
        user.full_name
        or user.username
        or str(user.id)
    )


def format_money(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " ₽"


def parse_money(text: str) -> int:
    cleaned = re.sub(r"[^\d]", "", text or "")

    if not cleaned:
        raise ValueError("Сумма не найдена")

    return int(cleaned)


def parse_additional(text: str) -> tuple[int, str]:
    text = (text or "").strip()

    if text.lower() in {
        "нет",
        "ничего",
        "no",
        "-",
        "0",
    }:
        return 0, "• Нет"

    parts = re.split(r"[,;\n]+", text)
    items = []

    for part in parts:
        part = part.strip()

        if not part:
            continue

        match = re.search(
            r"(.+?)[\s:—-]+([\d\s.,]+)\s*₽?$",
            part,
        )

        if not match:
            continue

        name = match.group(1).strip().capitalize()
        amount = parse_money(match.group(2))

        items.append((name, amount))

    if not items:
        return 0, f"• {escape(text)}"

    total = sum(amount for _, amount in items)

    formatted = "\n".join(
        f"• {escape(name)}: {format_money(amount)}"
        for name, amount in items
    )

    return total, formatted


def get_period_start(period: str) -> datetime | None:
    now = datetime.now()

    if period == "today":
        return now.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

    if period == "week":
        start = now - timedelta(days=now.weekday())

        return start.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

    if period == "month":
        return now.replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

    if period == "all":
        return None

    return None


def period_title(period: str) -> str:
    titles = {
        "today": "ЗА СЕГОДНЯ",
        "week": "ЗА НЕДЕЛЮ",
        "month": "ЗА МЕСЯЦ",
        "all": "ЗА ВСЁ ВРЕМЯ",
    }

    return titles.get(period, "")


async def send_to_target_chat(
    text: str,
    source_chat_id: int,
    reply_markup: InlineKeyboardMarkup | None = None,
):
    target_chat_id = REPORT_CHAT_ID or source_chat_id

    await bot.send_message(
        chat_id=target_chat_id,
        text=text,
        reply_markup=reply_markup,
    )


# ============================================================
# ОБЫЧНЫЙ ОТЧЕТ ТЕРМИНАЛА
# ============================================================

def calculate_terminal_report(data: dict) -> dict:
    change_100_after = (
        data["change_100_before"]
        + data["change_100_added"]
    )

    change_1000_after = (
        data["change_1000_before"]
        + data["change_1000_added"]
    )

    additional_total, additional_text = parse_additional(
        data.get("additional", "нет")
    )

    withheld_total = (
        data["change_100_added"]
        + data["change_1000_added"]
        + data["salary"]
        + additional_total
    )

    final_amount = data["total_amount"] - withheld_total

    transfer_from_other = bool(data.get("transfer_from_other", False))
    transfer_source = (data.get("transfer_source") or "").strip()

    return {
        "terminal": data["terminal"],
        "total_amount": data["total_amount"],
        "change_100_before": data["change_100_before"],
        "change_100_added": data["change_100_added"],
        "change_100_after": change_100_after,
        "change_1000_before": data["change_1000_before"],
        "change_1000_added": data["change_1000_added"],
        "change_1000_after": change_1000_after,
        "salary": data["salary"],
        "additional_text": additional_text,
        "additional_total": additional_total,
        "withheld_total": withheld_total,
        "final_amount": final_amount,
        "transfer_from_other": transfer_from_other,
        "transfer_source": transfer_source,
        "transfer_total": (
            data["change_100_added"] + data["change_1000_added"]
            if transfer_from_other else 0
        ),
    }


def build_terminal_report_text(
    calc: dict,
    user_name: str,
    report_id: int | None = None,
    created_at: str | None = None,
) -> str:
    if created_at is None:
        created_at = datetime.now().strftime("%d.%m.%Y %H:%M")

    number_text = ""

    if report_id is not None:
        number_text = f"📄 <b>Отчет №{report_id}</b>\n\n"

    return f"""
{number_text}📊 <b>ОТЧЕТ ПО ТЕРМИНАЛУ</b>

🏧 <b>Терминал:</b> {escape(calc["terminal"])}

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

{(
    "🔄 <b>Сдача получена с другого терминала:</b>\n"
    f"📤 Откуда: {escape(calc['transfer_source'])}\n"
    f"• По 100 ₽: {format_money(calc['change_100_added'])}\n"
    f"• По 1000 ₽: {format_money(calc['change_1000_added'])}\n"
    f"• Всего: {format_money(calc['transfer_total'])}\n\n"
    if calc.get("transfer_from_other") and calc.get("transfer_source")
    else ""
)}━━━━━━━━━━━━━━

📉 <b>Удержано:</b>
• Сдача 100 ₽: {format_money(calc["change_100_added"])}
• Сдача 1000 ₽: {format_money(calc["change_1000_added"])}
• ЗП: {format_money(calc["salary"])}
• Доп. расходы: {format_money(calc["additional_total"])}

💵 <b>НА РУКАХ:</b>
<b>{format_money(calc["final_amount"])}</b>

👤 <b>Отчет отправил:</b> {escape(user_name)}
🕒 {created_at}
""".strip()


def save_terminal_report(
    calc: dict,
    user_id: int,
    user_name: str,
) -> int:
    conn = get_connection()
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
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
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

    return int(report_id)


def save_terminal_transfer(
    report_id: int,
    calc: dict,
    user_id: int,
    user_name: str,
) -> int | None:
    if not calc.get("transfer_from_other"):
        return None

    source_terminal = (calc.get("transfer_source") or "").strip()
    total_amount = int(calc.get("transfer_total", 0))

    if not source_terminal or total_amount <= 0:
        return None

    conn = get_connection()
    cur = conn.cursor()

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cur.execute("""
        INSERT INTO terminal_transfers (
            report_id,
            source_terminal,
            destination_terminal,
            amount_100,
            amount_1000,
            total_amount,
            user_id,
            user_name,
            created_at,
            deleted
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
    """, (
        report_id,
        source_terminal,
        calc["terminal"],
        calc["change_100_added"],
        calc["change_1000_added"],
        total_amount,
        user_id,
        user_name,
        created_at,
    ))

    transfer_id = cur.lastrowid
    conn.commit()
    conn.close()

    return int(transfer_id)


async def ask_terminal_step(
    message: Message,
    state: FSMContext,
    step: str,
):
    await state.update_data(
        flow_type="terminal",
        current_step=step,
    )

    await state.set_state(TERMINAL_STATE_BY_STEP[step])

    if step == "terminal":
        keyboard = terminal_choice_keyboard()
    elif step == "transfer_source":
        data = await state.get_data()
        keyboard = terminal_choice_keyboard(
            exclude_terminal=str(data.get("terminal", ""))
        )
    elif step == "transfer_answer":
        keyboard = transfer_answer_keyboard()
    else:
        keyboard = form_keyboard()

    await message.answer(
        TERMINAL_QUESTIONS[step],
        reply_markup=keyboard,
    )


async def show_terminal_preview(
    message: Message,
    state: FSMContext,
):
    data = await state.get_data()
    calc = calculate_terminal_report(data)
    user_name = get_user_name(message)

    if calc["final_amount"] < 0:
        await message.answer(
            "⚠️ <b>Ошибка:</b> сумма удержаний больше общей суммы.\n\n"
            "Исправьте данные перед отправкой.",
            reply_markup=terminal_edit_keyboard(),
        )
        return

    preview = build_terminal_report_text(
        calc=calc,
        user_name=user_name,
    )

    await message.answer(
        "📋 <b>ПРОВЕРЬТЕ ОТЧЕТ ПЕРЕД ОТПРАВКОЙ</b>\n\n"
        + preview,
        reply_markup=terminal_confirm_keyboard(),
    )


async def process_terminal_step(
    message: Message,
    state: FSMContext,
    step: str,
    value,
):
    await state.update_data(**{step: value})

    data = await state.get_data()

    if data.get("edit_mode"):
        await state.update_data(
            edit_mode=False,
            editing_field=None,
        )

        await show_terminal_preview(message, state)
        return

    index = TERMINAL_STEPS.index(step)

    if index == len(TERMINAL_STEPS) - 1:
        await show_terminal_preview(message, state)
        return

    next_step = TERMINAL_STEPS[index + 1]

    await ask_terminal_step(
        message=message,
        state=state,
        step=next_step,
    )


# ============================================================
# АРЕНДА
# ============================================================

def normalize_comment(text: str) -> str:
    text = (text or "").strip()

    if text.lower() in {
        "нет",
        "ничего",
        "no",
        "-",
    }:
        return "Нет"

    return text


def build_rent_text(
    data: dict,
    user_name: str,
    rent_id: int | None = None,
    created_at: str | None = None,
) -> str:
    if created_at is None:
        created_at = datetime.now().strftime("%d.%m.%Y %H:%M")

    number_text = ""

    if rent_id is not None:
        number_text = f"📄 <b>Запись аренды №{rent_id}</b>\n\n"

    return f"""
{number_text}🏠 <b>ОПЛАТА АРЕНДЫ</b>

🏧 <b>Терминал:</b> {escape(data["terminal"])}

💰 <b>Сумма аренды:</b>
{format_money(data["amount"])}

📅 <b>Период:</b>
{escape(data["rent_period"])}

📝 <b>Комментарий:</b>
{escape(normalize_comment(data["comment"]))}

👤 <b>Запись добавил:</b> {escape(user_name)}
🕒 {created_at}
""".strip()


def save_rent_payment(
    data: dict,
    user_id: int,
    user_name: str,
) -> int:
    conn = get_connection()
    cur = conn.cursor()

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cur.execute("""
        INSERT INTO rent_payments (
            terminal,
            amount,
            rent_period,
            comment,
            user_id,
            user_name,
            created_at,
            deleted
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 0)
    """, (
        data["terminal"],
        data["amount"],
        data["rent_period"],
        normalize_comment(data["comment"]),
        user_id,
        user_name,
        created_at,
    ))

    rent_id = cur.lastrowid

    conn.commit()
    conn.close()

    return int(rent_id)


async def ask_rent_step(
    message: Message,
    state: FSMContext,
    step: str,
):
    await state.update_data(
        flow_type="rent",
        current_step=step,
    )

    await state.set_state(RENT_STATE_BY_STEP[step])

    keyboard = (
        terminal_choice_keyboard()
        if step == "terminal"
        else form_keyboard()
    )

    await message.answer(
        RENT_QUESTIONS[step],
        reply_markup=keyboard,
    )


async def show_rent_preview(
    message: Message,
    state: FSMContext,
):
    data = await state.get_data()
    user_name = get_user_name(message)

    preview = build_rent_text(
        data=data,
        user_name=user_name,
    )

    await message.answer(
        "📋 <b>ПРОВЕРЬТЕ ЗАПИСЬ ПЕРЕД ОТПРАВКОЙ</b>\n\n"
        + preview,
        reply_markup=rent_confirm_keyboard(),
    )


async def process_rent_step(
    message: Message,
    state: FSMContext,
    step: str,
    value,
):
    await state.update_data(**{step: value})

    data = await state.get_data()

    if data.get("edit_mode"):
        await state.update_data(
            edit_mode=False,
            editing_field=None,
        )

        await show_rent_preview(message, state)
        return

    index = RENT_STEPS.index(step)

    if index == len(RENT_STEPS) - 1:
        await show_rent_preview(message, state)
        return

    next_step = RENT_STEPS[index + 1]

    await ask_rent_step(
        message=message,
        state=state,
        step=next_step,
    )


# ============================================================
# НАЗАД И ОТМЕНА
# ============================================================

async def go_back(
    message: Message,
    state: FSMContext,
):
    data = await state.get_data()

    flow_type = data.get("flow_type")
    current_step = data.get("current_step")

    if not flow_type or not current_step:
        await message.answer(
            "Сейчас нет активного заполнения.",
            reply_markup=start_keyboard(),
        )
        return

    if flow_type == "terminal":
        steps = TERMINAL_STEPS
        ask_function = ask_terminal_step

    elif flow_type == "rent":
        steps = RENT_STEPS
        ask_function = ask_rent_step

    else:
        await message.answer(
            "Не удалось определить текущий раздел.",
            reply_markup=start_keyboard(),
        )
        return

    if current_step not in steps:
        await message.answer(
            "Не удалось определить текущий вопрос.",
            reply_markup=start_keyboard(),
        )
        return

    index = steps.index(current_step)

    if index == 0:
        await message.answer(
            "Вы уже на первом вопросе.",
            reply_markup=form_keyboard(),
        )
        return

    previous_step = steps[index - 1]

    await ask_function(
        message=message,
        state=state,
        step=previous_step,
    )


# ============================================================
# АДМИН-ОТЧЕТЫ
# ============================================================

def build_reports_summary(period: str) -> str:
    start = get_period_start(period)

    conn = get_connection()
    cur = conn.cursor()

    if start is None:
        where = "WHERE deleted = 0"
        params = ()
    else:
        where = "WHERE deleted = 0 AND created_at >= ?"
        params = (
            start.strftime("%Y-%m-%d %H:%M:%S"),
        )

    cur.execute(f"""
        SELECT
            COUNT(id),
            COALESCE(SUM(total_amount), 0),
            COALESCE(SUM(withheld_total), 0),
            COALESCE(SUM(final_amount), 0)
        FROM reports
        {where}
    """, params)

    count, total_sum, withheld_sum, final_sum = cur.fetchone()

    cur.execute(f"""
        SELECT
            terminal,
            COUNT(id),
            COALESCE(SUM(final_amount), 0)
        FROM reports
        {where}
        GROUP BY terminal
        ORDER BY SUM(final_amount) DESC
    """, params)

    terminals = cur.fetchall()

    conn.close()

    terminals_text = "\n".join(
        f"• {escape(terminal)} — {count_reports} отч. — "
        f"{format_money(amount)}"
        for terminal, count_reports, amount in terminals
    )

    if not terminals_text:
        terminals_text = "• Нет данных"

    return f"""
📊 <b>ОТЧЕТЫ {period_title(period)}</b>

📌 Количество отчетов:
<b>{count}</b>

💰 Общая сумма:
<b>{format_money(total_sum)}</b>

📉 Всего удержано:
<b>{format_money(withheld_sum)}</b>

💵 <b>НА РУКАХ:</b>
<b>{format_money(final_sum)}</b>

━━━━━━━━━━━━━━

🏧 <b>По терминалам:</b>
{terminals_text}
""".strip()


def build_last_reports(limit: int = 10) -> str:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            terminal,
            final_amount,
            user_name,
            created_at
        FROM reports
        WHERE deleted = 0
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))

    rows = cur.fetchall()
    conn.close()

    if not rows:
        return "📋 Отчетов пока нет."

    text = "📋 <b>ПОСЛЕДНИЕ ОТЧЕТЫ</b>\n\n"

    for (
        report_id,
        terminal,
        final_amount,
        user_name,
        created_at,
    ) in rows:
        text += (
            f"📄 №{report_id}\n"
            f"🏧 {escape(terminal)}\n"
            f"💵 На руках: <b>{format_money(final_amount)}</b>\n"
            f"👤 {escape(user_name)}\n"
            f"🕒 {created_at}\n\n"
        )

    return text.strip()


def build_rent_summary(period: str) -> str:
    start = get_period_start(period)

    conn = get_connection()
    cur = conn.cursor()

    if start is None:
        where = "WHERE deleted = 0"
        params = ()
    else:
        where = "WHERE deleted = 0 AND created_at >= ?"
        params = (
            start.strftime("%Y-%m-%d %H:%M:%S"),
        )

    cur.execute(f"""
        SELECT
            COUNT(id),
            COALESCE(SUM(amount), 0)
        FROM rent_payments
        {where}
    """, params)

    count, total_amount = cur.fetchone()

    cur.execute(f"""
        SELECT
            id,
            terminal,
            amount,
            rent_period,
            created_at
        FROM rent_payments
        {where}
        ORDER BY id DESC
        LIMIT 30
    """, params)

    rows = cur.fetchall()
    conn.close()

    details = ""

    for (
        rent_id,
        terminal,
        amount,
        rent_period,
        created_at,
    ) in rows:
        details += (
            f"📄 №{rent_id} | 🏧 {escape(terminal)}\n"
            f"💰 {format_money(amount)}\n"
            f"📅 За период: {escape(rent_period)}\n"
            f"🕒 Оплачено: {created_at}\n\n"
        )

    if not details:
        details = "• Нет данных"

    return f"""
🏠 <b>АРЕНДА {period_title(period)}</b>

📌 Количество оплат:
<b>{count}</b>

💰 Всего отдали за аренду:
<b>{format_money(total_amount)}</b>

━━━━━━━━━━━━━━

{details}
""".strip()


def build_last_rent_payments(limit: int = 10) -> str:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            terminal,
            amount,
            rent_period,
            user_name,
            created_at
        FROM rent_payments
        WHERE deleted = 0
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))

    rows = cur.fetchall()
    conn.close()

    if not rows:
        return "🏠 Записей об аренде пока нет."

    text = "🏠 <b>ПОСЛЕДНИЕ ОПЛАТЫ АРЕНДЫ</b>\n\n"

    for (
        rent_id,
        terminal,
        amount,
        rent_period,
        user_name,
        created_at,
    ) in rows:
        text += (
            f"📄 №{rent_id}\n"
            f"🏧 {escape(terminal)}\n"
            f"💰 {format_money(amount)}\n"
            f"📅 {escape(rent_period)}\n"
            f"👤 {escape(user_name)}\n"
            f"🕒 {created_at}\n\n"
        )

    return text.strip()


def build_all_terminals_summary() -> str:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT DISTINCT terminal
        FROM (
            SELECT terminal
            FROM reports
            WHERE deleted = 0

            UNION

            SELECT terminal
            FROM rent_payments
            WHERE deleted = 0

            UNION

            SELECT source_terminal AS terminal
            FROM terminal_transfers
            WHERE deleted = 0

            UNION

            SELECT destination_terminal AS terminal
            FROM terminal_transfers
            WHERE deleted = 0
        )
        ORDER BY terminal
    """)

    terminals = [row[0] for row in cur.fetchall()]

    if not terminals:
        conn.close()

        return "🏧 Данных по терминалам пока нет."

    text = (
        "🏧 <b>СТАТИСТИКА ТЕРМИНАЛОВ ЗА ВСЁ ВРЕМЯ</b>\n\n"
    )

    grand_final = 0
    grand_rent = 0

    for terminal in terminals:
        cur.execute("""
            SELECT
                COUNT(id),
                COALESCE(SUM(total_amount), 0),
                COALESCE(SUM(withheld_total), 0),
                COALESCE(SUM(final_amount), 0)
            FROM reports
            WHERE terminal = ? AND deleted = 0
        """, (terminal,))

        (
            report_count,
            total_sum,
            withheld_sum,
            final_sum,
        ) = cur.fetchone()

        cur.execute("""
            SELECT
                COUNT(id),
                COALESCE(SUM(amount), 0)
            FROM rent_payments
            WHERE terminal = ? AND deleted = 0
        """, (terminal,))

        rent_count, rent_sum = cur.fetchone()

        cur.execute("""
            SELECT COALESCE(SUM(total_amount), 0)
            FROM terminal_transfers
            WHERE source_terminal = ? AND deleted = 0
        """, (terminal,))
        outgoing_transfers = cur.fetchone()[0]

        cur.execute("""
            SELECT COALESCE(SUM(total_amount), 0)
            FROM terminal_transfers
            WHERE destination_terminal = ? AND deleted = 0
        """, (terminal,))
        incoming_transfers = cur.fetchone()[0]

        adjusted_final = final_sum - outgoing_transfers

        grand_final += adjusted_final
        grand_rent += rent_sum

        text += (
            f"🏧 <b>{escape(terminal)}</b>\n"
            f"📊 Отчетов: {report_count}\n"
            f"💰 Общая сумма: {format_money(total_sum)}\n"
            f"📉 Удержано: {format_money(withheld_sum)}\n"
            f"💵 На руках по отчетам: <b>{format_money(final_sum)}</b>\n"
            f"📤 Передано другим: {format_money(outgoing_transfers)}\n"
            f"📥 Получено от других: {format_money(incoming_transfers)}\n"
            f"💵 Осталось с учетом переводов: <b>{format_money(adjusted_final)}</b>\n"
            f"🏠 Оплат аренды: {rent_count}\n"
            f"🏠 За аренду отдали: {format_money(rent_sum)}\n\n"
        )

    conn.close()

    text += (
        "━━━━━━━━━━━━━━\n\n"
        f"💵 <b>НА РУКАХ ПО ВСЕМ ТЕРМИНАЛАМ:</b>\n"
        f"<b>{format_money(grand_final)}</b>\n\n"
        f"🏠 <b>ВСЕГО ОТДАЛИ ЗА АРЕНДУ:</b>\n"
        f"<b>{format_money(grand_rent)}</b>"
    )

    return text.strip()


def build_terminal_all_time_summary(terminal: str) -> str:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            COUNT(id),
            COALESCE(SUM(total_amount), 0),
            COALESCE(SUM(withheld_total), 0),
            COALESCE(SUM(final_amount), 0)
        FROM reports
        WHERE terminal = ? AND deleted = 0
    """, (terminal,))

    (
        report_count,
        total_sum,
        withheld_sum,
        final_sum,
    ) = cur.fetchone()

    cur.execute("""
        SELECT
            COUNT(id),
            COALESCE(SUM(amount), 0)
        FROM rent_payments
        WHERE terminal = ? AND deleted = 0
    """, (terminal,))

    rent_count, rent_sum = cur.fetchone()

    cur.execute("""
        SELECT
            COALESCE(SUM(total_amount), 0)
        FROM terminal_transfers
        WHERE source_terminal = ? AND deleted = 0
    """, (terminal,))

    outgoing_transfers = cur.fetchone()[0]

    cur.execute("""
        SELECT
            COALESCE(SUM(total_amount), 0)
        FROM terminal_transfers
        WHERE destination_terminal = ? AND deleted = 0
    """, (terminal,))

    incoming_transfers = cur.fetchone()[0]

    adjusted_final = final_sum - outgoing_transfers

    cur.execute("""
        SELECT
            id,
            amount,
            rent_period,
            created_at
        FROM rent_payments
        WHERE terminal = ? AND deleted = 0
        ORDER BY id DESC
        LIMIT 10
    """, (terminal,))

    rent_rows = cur.fetchall()
    conn.close()

    rent_details = "\n".join(
        f"• №{rent_id} — {format_money(amount)} — "
        f"{escape(rent_period)} — {created_at}"
        for rent_id, amount, rent_period, created_at in rent_rows
    )

    if not rent_details:
        rent_details = "• Оплат аренды нет"

    return f"""
🏧 <b>ТЕРМИНАЛ: {escape(terminal)}</b>
📚 Период: всё время

📊 Количество отчетов:
<b>{report_count}</b>

💰 Общая сумма:
<b>{format_money(total_sum)}</b>

📉 Всего удержано:
<b>{format_money(withheld_sum)}</b>

💵 <b>НА РУКАХ ПО ОТЧЕТАМ:</b>
<b>{format_money(final_sum)}</b>

📤 Передано другим терминалам:
<b>{format_money(outgoing_transfers)}</b>

📥 Получено от других терминалов:
<b>{format_money(incoming_transfers)}</b>

💵 <b>ОСТАЛОСЬ С УЧЕТОМ ПЕРЕВОДОВ:</b>
<b>{format_money(adjusted_final)}</b>

🏠 Количество оплат аренды:
<b>{rent_count}</b>

🏠 За аренду отдали:
<b>{format_money(rent_sum)}</b>

━━━━━━━━━━━━━━

🏠 <b>Последние оплаты аренды:</b>
{rent_details}
""".strip()


# ============================================================
# ПОЛУЧЕНИЕ ОТЧЕТОВ ПО ID
# ============================================================

def get_report_text_by_id(report_id: int) -> str:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
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

    cur.execute("""
        SELECT source_terminal, amount_100, amount_1000, total_amount
        FROM terminal_transfers
        WHERE report_id = ? AND deleted = 0
        ORDER BY id DESC
        LIMIT 1
    """, (report_id,))
    transfer_row = cur.fetchone()

    conn.close()

    if not row:
        return "❌ Отчет не найден."

    (
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
        return f"🗑 Отчет №{report_id} удален."

    calc = {
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
        "transfer_from_other": bool(transfer_row),
        "transfer_source": transfer_row[0] if transfer_row else "",
        "transfer_total": transfer_row[3] if transfer_row else 0,
    }

    return build_terminal_report_text(
        calc=calc,
        user_name=user_name,
        report_id=report_id,
        created_at=created_at,
    )


def get_rent_text_by_id(rent_id: int) -> str:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            terminal,
            amount,
            rent_period,
            comment,
            user_name,
            created_at,
            deleted
        FROM rent_payments
        WHERE id = ?
    """, (rent_id,))

    row = cur.fetchone()
    conn.close()

    if not row:
        return "❌ Запись аренды не найдена."

    (
        terminal,
        amount,
        rent_period,
        comment,
        user_name,
        created_at,
        deleted,
    ) = row

    if deleted:
        return f"🗑 Запись аренды №{rent_id} удалена."

    data = {
        "terminal": terminal,
        "amount": amount,
        "rent_period": rent_period,
        "comment": comment,
    }

    return build_rent_text(
        data=data,
        user_name=user_name,
        rent_id=rent_id,
        created_at=created_at,
    )


# ============================================================
# ОСНОВНЫЕ КОМАНДЫ
# ============================================================

@dp.message(CommandStart())
async def start_handler(
    message: Message,
    state: FSMContext,
):
    await state.clear()

    await message.answer(
        "✅ Бот работает.\n\n"
        "Выберите действие:",
        reply_markup=start_keyboard(),
    )


@dp.message(Command("myid"))
async def myid_handler(message: Message):
    await message.answer(
        f"Ваш Telegram ID:\n"
        f"<code>{message.from_user.id}</code>"
    )


@dp.message(Command("chatid"))
async def chatid_handler(message: Message):
    await message.answer(
        f"ID этого чата:\n"
        f"<code>{message.chat.id}</code>"
    )


@dp.message(Command("cancel"))
async def cancel_command(
    message: Message,
    state: FSMContext,
):
    await state.clear()

    await message.answer(
        "❌ Заполнение полностью отменено.",
        reply_markup=start_keyboard(),
    )


@dp.message(Command("admin"))
async def admin_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(
            "⛔ У вас нет доступа к админ-панели.\n\n"
            "Ваш ID можно узнать через /myid."
        )
        return

    await message.answer(
        "👨‍💼 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
        "Выберите раздел:",
        reply_markup=admin_keyboard(),
    )


@dp.message(Command("report"))
async def report_by_id_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return

    parts = message.text.split(maxsplit=1)

    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer(
            "Использование:\n"
            "<code>/report 15</code>"
        )
        return

    report_id = int(parts[1])

    await message.answer(
        get_report_text_by_id(report_id)
    )


@dp.message(Command("rent"))
async def rent_by_id_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return

    parts = message.text.split(maxsplit=1)

    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer(
            "Использование:\n"
            "<code>/rent 8</code>"
        )
        return

    rent_id = int(parts[1])

    await message.answer(
        get_rent_text_by_id(rent_id)
    )


@dp.message(Command("terminal"))
async def terminal_summary_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return

    parts = message.text.split(maxsplit=1)

    if len(parts) != 2:
        await message.answer(
            "Использование:\n"
            "<code>/terminal 20-й</code>"
        )
        return

    terminal = parts[1].strip()

    await message.answer(
        build_terminal_all_time_summary(terminal)
    )


# ============================================================
# КНОПКИ ГЛАВНОГО МЕНЮ
# ============================================================

@dp.message(F.text == "📊 Новый отчет")
async def new_terminal_report_handler(
    message: Message,
    state: FSMContext,
):
    await state.clear()

    await ask_terminal_step(
        message=message,
        state=state,
        step="terminal",
    )


@dp.message(F.text == "🏠 Оплата аренды")
async def new_rent_handler(
    message: Message,
    state: FSMContext,
):
    await state.clear()

    await ask_rent_step(
        message=message,
        state=state,
        step="terminal",
    )


@dp.message(F.text == "👨‍💼 Админ панель")
async def admin_button_handler(message: Message):
    await admin_command(message)


@dp.message(F.text == "⬅️ Назад")
async def back_handler(
    message: Message,
    state: FSMContext,
):
    await go_back(message, state)


@dp.message(F.text == "❌ Отменить полностью")
async def full_cancel_handler(
    message: Message,
    state: FSMContext,
):
    await state.clear()

    await message.answer(
        "❌ Заполнение полностью отменено.",
        reply_markup=start_keyboard(),
    )


# ============================================================
# ПОЛЯ ОБЫЧНОГО ОТЧЕТА
# ============================================================

@dp.message(TerminalReportForm.terminal)
async def terminal_name_handler(
    message: Message,
    state: FSMContext,
):
    value = (message.text or "").strip()

    if value not in TERMINALS:
        await message.answer(
            "Выберите терминал кнопкой ниже.",
            reply_markup=terminal_choice_keyboard(),
        )
        return

    await process_terminal_step(
        message,
        state,
        "terminal",
        value,
    )


@dp.message(TerminalReportForm.total_amount)
async def terminal_total_handler(
    message: Message,
    state: FSMContext,
):
    try:
        value = parse_money(message.text)
    except ValueError:
        await message.answer(
            "Введите сумму цифрами.\n"
            "Например: <b>150000</b>"
        )
        return

    await process_terminal_step(
        message,
        state,
        "total_amount",
        value,
    )


@dp.message(TerminalReportForm.change_100_before)
async def change_100_before_handler(
    message: Message,
    state: FSMContext,
):
    try:
        value = parse_money(message.text)
    except ValueError:
        await message.answer(
            "Введите сумму цифрами.\n"
            "Например: <b>5000</b>"
        )
        return

    await process_terminal_step(
        message,
        state,
        "change_100_before",
        value,
    )


@dp.message(TerminalReportForm.change_100_added)
async def change_100_added_handler(
    message: Message,
    state: FSMContext,
):
    try:
        value = parse_money(message.text)
    except ValueError:
        await message.answer(
            "Введите сумму цифрами.\n"
            "Например: <b>1000</b>"
        )
        return

    await process_terminal_step(
        message,
        state,
        "change_100_added",
        value,
    )


@dp.message(TerminalReportForm.change_1000_before)
async def change_1000_before_handler(
    message: Message,
    state: FSMContext,
):
    try:
        value = parse_money(message.text)
    except ValueError:
        await message.answer(
            "Введите сумму цифрами.\n"
            "Например: <b>10000</b>"
        )
        return

    await process_terminal_step(
        message,
        state,
        "change_1000_before",
        value,
    )


@dp.message(TerminalReportForm.change_1000_added)
async def change_1000_added_handler(
    message: Message,
    state: FSMContext,
):
    try:
        value = parse_money(message.text)
    except ValueError:
        await message.answer(
            "Введите сумму цифрами.\n"
            "Например: <b>3000</b>"
        )
        return

    await process_terminal_step(
        message,
        state,
        "change_1000_added",
        value,
    )


@dp.message(
    TerminalReportForm.transfer_answer,
    F.text == "✅ Да, с другого терминала",
)
async def transfer_yes_handler(
    message: Message,
    state: FSMContext,
):
    await state.update_data(
        transfer_answer="yes",
        transfer_from_other=True,
        transfer_source="",
    )

    await ask_terminal_step(
        message=message,
        state=state,
        step="transfer_source",
    )


@dp.message(
    TerminalReportForm.transfer_answer,
    F.text == "❌ Нет, внешнее пополнение",
)
async def transfer_no_handler(
    message: Message,
    state: FSMContext,
):
    await state.update_data(
        transfer_answer="no",
        transfer_from_other=False,
        transfer_source="",
    )

    await ask_terminal_step(
        message=message,
        state=state,
        step="salary",
    )


@dp.message(TerminalReportForm.transfer_answer)
async def transfer_answer_invalid_handler(
    message: Message,
):
    await message.answer(
        "Выберите один из вариантов кнопками ниже.",
        reply_markup=transfer_answer_keyboard(),
    )


@dp.message(TerminalReportForm.transfer_source)
async def transfer_source_handler(
    message: Message,
    state: FSMContext,
):
    value = (message.text or "").strip()
    data = await state.get_data()
    current_terminal = str(data.get("terminal", ""))

    if value not in TERMINALS:
        await message.answer(
            "Выберите терминал-источник кнопкой ниже.",
            reply_markup=terminal_choice_keyboard(
                exclude_terminal=current_terminal
            ),
        )
        return

    if value.casefold() == current_terminal.casefold():
        await message.answer(
            "Терминал-источник не может совпадать с текущим терминалом.",
            reply_markup=terminal_choice_keyboard(
                exclude_terminal=current_terminal
            ),
        )
        return

    await state.update_data(
        transfer_answer="yes",
        transfer_from_other=True,
        transfer_source=value,
    )

    if data.get("edit_mode"):
        await state.update_data(
            edit_mode=False,
            editing_field=None,
        )
        await show_terminal_preview(message, state)
        return

    await ask_terminal_step(
        message=message,
        state=state,
        step="salary",
    )


@dp.message(TerminalReportForm.salary)
async def salary_handler(
    message: Message,
    state: FSMContext,
):
    try:
        value = parse_money(message.text)
    except ValueError:
        await message.answer(
            "Введите сумму цифрами.\n"
            "Например: <b>5000</b>"
        )
        return

    await process_terminal_step(
        message,
        state,
        "salary",
        value,
    )


@dp.message(TerminalReportForm.additional)
async def additional_handler(
    message: Message,
    state: FSMContext,
):
    value = (message.text or "").strip()

    if not value:
        value = "нет"

    await process_terminal_step(
        message,
        state,
        "additional",
        value,
    )


# ============================================================
# ПОДТВЕРЖДЕНИЕ ОБЫЧНОГО ОТЧЕТА
# ============================================================

@dp.callback_query(F.data == "terminal_confirm_send")
async def terminal_confirm_send_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    data = await state.get_data()

    required_fields = {
        "terminal",
        "total_amount",
        "change_100_before",
        "change_100_added",
        "change_1000_before",
        "change_1000_added",
        "transfer_answer",
        "salary",
        "additional",
    }

    if not required_fields.issubset(data.keys()):
        await callback.answer(
            "Не все поля заполнены.",
            show_alert=True,
        )
        return

    if data.get("transfer_from_other") and not (data.get("transfer_source") or "").strip():
        await callback.answer(
            "Укажите терминал, откуда взяли сдачу.",
            show_alert=True,
        )
        return

    calc = calculate_terminal_report(data)

    if calc["final_amount"] < 0:
        await callback.answer(
            "Сумма на руках не может быть отрицательной.",
            show_alert=True,
        )
        return

    user_name = get_user_name(callback)
    user_id = callback.from_user.id

    report_id = save_terminal_report(
        calc=calc,
        user_id=user_id,
        user_name=user_name,
    )

    transfer_id = save_terminal_transfer(
        report_id=report_id,
        calc=calc,
        user_id=user_id,
        user_name=user_name,
    )

    report_text = build_terminal_report_text(
        calc=calc,
        user_name=user_name,
        report_id=report_id,
    )

    await send_to_target_chat(
        text=report_text,
        source_chat_id=callback.message.chat.id,
        reply_markup=terminal_delete_keyboard(report_id),
    )

    await callback.message.answer(
        f"✅ Отчет №{report_id} отправлен в группу.",
        reply_markup=start_keyboard(),
    )

    await state.clear()
    await callback.answer()


@dp.callback_query(F.data == "terminal_confirm_edit")
async def terminal_confirm_edit_handler(
    callback: CallbackQuery,
):
    await callback.message.edit_text(
        "✏️ <b>Что нужно исправить?</b>",
        reply_markup=terminal_edit_keyboard(),
    )

    await callback.answer()


@dp.callback_query(F.data == "terminal_confirm_cancel")
async def terminal_confirm_cancel_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    await state.clear()

    await callback.message.answer(
        "❌ Отчет отменен.",
        reply_markup=start_keyboard(),
    )

    await callback.answer()


@dp.callback_query(F.data == "terminal_back_to_preview")
async def terminal_back_to_preview_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    data = await state.get_data()
    calc = calculate_terminal_report(data)
    user_name = get_user_name(callback)

    preview = build_terminal_report_text(
        calc=calc,
        user_name=user_name,
    )

    await callback.message.edit_text(
        "📋 <b>ПРОВЕРЬТЕ ОТЧЕТ ПЕРЕД ОТПРАВКОЙ</b>\n\n"
        + preview,
        reply_markup=terminal_confirm_keyboard(),
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("terminal_edit_"))
async def terminal_edit_field_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    field = callback.data.replace("terminal_edit_", "", 1)

    if field not in TERMINAL_STATE_BY_STEP:
        await callback.answer(
            "Поле не найдено.",
            show_alert=True,
        )
        return

    await state.update_data(
        flow_type="terminal",
        current_step=field,
        edit_mode=True,
        editing_field=field,
    )

    await state.set_state(
        TERMINAL_STATE_BY_STEP[field]
    )

    if field == "terminal":
        edit_keyboard = terminal_choice_keyboard()
    elif field == "transfer_source":
        data = await state.get_data()
        edit_keyboard = terminal_choice_keyboard(
            exclude_terminal=str(data.get("terminal", ""))
        )
    elif field == "transfer_answer":
        edit_keyboard = transfer_answer_keyboard()
    else:
        edit_keyboard = form_keyboard()

    await callback.message.answer(
        f"✏️ Исправляем: "
        f"<b>{TERMINAL_FIELD_NAMES[field]}</b>\n\n"
        f"{TERMINAL_QUESTIONS[field]}",
        reply_markup=edit_keyboard,
    )

    await callback.answer()


# ============================================================
# ПОЛЯ АРЕНДЫ
# ============================================================

@dp.message(RentForm.terminal)
async def rent_terminal_handler(
    message: Message,
    state: FSMContext,
):
    value = (message.text or "").strip()

    if value not in TERMINALS:
        await message.answer(
            "Выберите терминал кнопкой ниже.",
            reply_markup=terminal_choice_keyboard(),
        )
        return

    await process_rent_step(
        message,
        state,
        "terminal",
        value,
    )


@dp.message(RentForm.amount)
async def rent_amount_handler(
    message: Message,
    state: FSMContext,
):
    try:
        value = parse_money(message.text)
    except ValueError:
        await message.answer(
            "Введите сумму цифрами.\n"
            "Например: <b>25000</b>"
        )
        return

    await process_rent_step(
        message,
        state,
        "amount",
        value,
    )


@dp.message(RentForm.rent_period)
async def rent_period_handler(
    message: Message,
    state: FSMContext,
):
    value = (message.text or "").strip()

    if not value:
        await message.answer(
            "Введите период аренды.\n"
            "Например: <b>Июнь 2026</b>"
        )
        return

    await process_rent_step(
        message,
        state,
        "rent_period",
        value,
    )


@dp.message(RentForm.comment)
async def rent_comment_handler(
    message: Message,
    state: FSMContext,
):
    value = (message.text or "").strip()

    if not value:
        value = "нет"

    await process_rent_step(
        message,
        state,
        "comment",
        value,
    )


# ============================================================
# ПОДТВЕРЖДЕНИЕ АРЕНДЫ
# ============================================================

@dp.callback_query(F.data == "rent_confirm_send")
async def rent_confirm_send_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    data = await state.get_data()

    required_fields = set(RENT_STEPS)

    if not required_fields.issubset(data.keys()):
        await callback.answer(
            "Не все поля заполнены.",
            show_alert=True,
        )
        return

    user_name = get_user_name(callback)
    user_id = callback.from_user.id

    rent_id = save_rent_payment(
        data=data,
        user_id=user_id,
        user_name=user_name,
    )

    rent_text = build_rent_text(
        data=data,
        user_name=user_name,
        rent_id=rent_id,
    )

    await send_to_target_chat(
        text=rent_text,
        source_chat_id=callback.message.chat.id,
        reply_markup=rent_delete_keyboard(rent_id),
    )

    await callback.message.answer(
        f"✅ Запись аренды №{rent_id} отправлена в группу.",
        reply_markup=start_keyboard(),
    )

    await state.clear()
    await callback.answer()


@dp.callback_query(F.data == "rent_confirm_edit")
async def rent_confirm_edit_handler(
    callback: CallbackQuery,
):
    await callback.message.edit_text(
        "✏️ <b>Что нужно исправить?</b>",
        reply_markup=rent_edit_keyboard(),
    )

    await callback.answer()


@dp.callback_query(F.data == "rent_confirm_cancel")
async def rent_confirm_cancel_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    await state.clear()

    await callback.message.answer(
        "❌ Запись аренды отменена.",
        reply_markup=start_keyboard(),
    )

    await callback.answer()


@dp.callback_query(F.data == "rent_back_to_preview")
async def rent_back_to_preview_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    data = await state.get_data()
    user_name = get_user_name(callback)

    preview = build_rent_text(
        data=data,
        user_name=user_name,
    )

    await callback.message.edit_text(
        "📋 <b>ПРОВЕРЬТЕ ЗАПИСЬ ПЕРЕД ОТПРАВКОЙ</b>\n\n"
        + preview,
        reply_markup=rent_confirm_keyboard(),
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("rent_edit_"))
async def rent_edit_field_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    field = callback.data.replace("rent_edit_", "", 1)

    if field not in RENT_STATE_BY_STEP:
        await callback.answer(
            "Поле не найдено.",
            show_alert=True,
        )
        return

    await state.update_data(
        flow_type="rent",
        current_step=field,
        edit_mode=True,
        editing_field=field,
    )

    await state.set_state(
        RENT_STATE_BY_STEP[field]
    )

    edit_keyboard = (
        terminal_choice_keyboard()
        if field == "terminal"
        else form_keyboard()
    )

    await callback.message.answer(
        f"✏️ Исправляем: "
        f"<b>{RENT_FIELD_NAMES[field]}</b>\n\n"
        f"{RENT_QUESTIONS[field]}",
        reply_markup=edit_keyboard,
    )

    await callback.answer()


# ============================================================
# АДМИН-ПАНЕЛЬ
# ============================================================

@dp.callback_query(F.data == "admin_main_menu")
async def admin_main_menu_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        "👨‍💼 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
        "Выберите раздел:",
        reply_markup=admin_keyboard(),
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("admin_reports_"))
async def admin_reports_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    action = callback.data.replace(
        "admin_reports_",
        "",
        1,
    )

    if action in {"today", "week", "month", "all"}:
        text = build_reports_summary(action)

    elif action == "last10":
        text = build_last_reports()

    else:
        await callback.answer(
            "Неизвестное действие.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        text,
        reply_markup=admin_keyboard(),
    )

    await callback.answer()


@dp.callback_query(F.data == "admin_rent_menu")
async def admin_rent_menu_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        "🏠 <b>ОТЧЕТЫ ПО АРЕНДЕ</b>\n\n"
        "Выберите период:",
        reply_markup=admin_rent_keyboard(),
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("admin_rent_"))
async def admin_rent_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    action = callback.data.replace(
        "admin_rent_",
        "",
        1,
    )

    if action == "menu":
        return

    if action in {"today", "week", "month", "all"}:
        text = build_rent_summary(action)

    elif action == "last10":
        text = build_last_rent_payments()

    else:
        await callback.answer(
            "Неизвестное действие.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        text,
        reply_markup=admin_rent_keyboard(),
    )

    await callback.answer()


@dp.callback_query(F.data == "admin_terminals_all")
async def admin_terminals_all_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        build_all_terminals_summary(),
        reply_markup=admin_keyboard(),
    )

    await callback.answer()


# ============================================================
# УДАЛЕНИЕ
# ============================================================

@dp.callback_query(F.data.startswith("delete_report_"))
async def delete_report_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "Удалять отчеты может только администратор.",
            show_alert=True,
        )
        return

    report_id_raw = callback.data.replace(
        "delete_report_",
        "",
        1,
    )

    if not report_id_raw.isdigit():
        await callback.answer(
            "Неверный номер отчета.",
            show_alert=True,
        )
        return

    report_id = int(report_id_raw)

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE reports
        SET deleted = 1
        WHERE id = ?
    """, (report_id,))

    changed = cur.rowcount

    cur.execute("""
        UPDATE terminal_transfers
        SET deleted = 1
        WHERE report_id = ?
    """, (report_id,))

    conn.commit()
    conn.close()

    if changed == 0:
        await callback.answer(
            "Отчет не найден.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        f"🗑 <b>Отчет №{report_id} удален.</b>\n\n"
        f"👤 Удалил: {escape(get_user_name(callback))}"
    )

    await callback.answer("Отчет удален.")


@dp.callback_query(F.data.startswith("delete_rent_"))
async def delete_rent_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "Удалять записи может только администратор.",
            show_alert=True,
        )
        return

    rent_id_raw = callback.data.replace(
        "delete_rent_",
        "",
        1,
    )

    if not rent_id_raw.isdigit():
        await callback.answer(
            "Неверный номер записи.",
            show_alert=True,
        )
        return

    rent_id = int(rent_id_raw)

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE rent_payments
        SET deleted = 1
        WHERE id = ?
    """, (rent_id,))

    changed = cur.rowcount

    conn.commit()
    conn.close()

    if changed == 0:
        await callback.answer(
            "Запись не найдена.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        f"🗑 <b>Запись аренды №{rent_id} удалена.</b>\n\n"
        f"👤 Удалил: {escape(get_user_name(callback))}"
    )

    await callback.answer("Запись удалена.")


# ============================================================
# WEB-СЕРВЕР ДЛЯ RENDER
# ============================================================

async def health_check(request):
    return web.Response(
        text="Telegram bot is running"
    )


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT,
    )

    await site.start()

    print(f"Web server started on port {PORT}")


# ============================================================
# ЗАПУСК
# ============================================================

async def main():
    init_db()

    await start_web_server()

    print("Telegram bot started")

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types(),
    )


if __name__ == "__main__":
    asyncio.run(main())
