```python
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

REPORT_CHAT_ID_RAW = os.getenv(
    "REPORT_CHAT_ID",
    "",
).strip()

ADMIN_IDS_RAW = os.getenv(
    "ADMIN_IDS",
    "",
).strip()

DB_PATH = "reports.db"


if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не найден в Environment Variables"
    )


def parse_admin_ids(raw: str) -> set[int]:
    result = set()

    for item in raw.split(","):
        item = item.strip()

        if item.lstrip("-").isdigit():
            result.add(int(item))

    return result


ADMIN_IDS = parse_admin_ids(ADMIN_IDS_RAW)


REPORT_CHAT_ID = None

if (
    REPORT_CHAT_ID_RAW
    and REPORT_CHAT_ID_RAW.lstrip("-").isdigit()
):
    REPORT_CHAT_ID = int(REPORT_CHAT_ID_RAW)


bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    ),
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


# ============================================================
# ПОРЯДОК ВОПРОСОВ
# ============================================================

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

    "change_100_before":
        TerminalReportForm.change_100_before,

    "change_100_added":
        TerminalReportForm.change_100_added,

    "change_1000_before":
        TerminalReportForm.change_1000_before,

    "change_1000_added":
        TerminalReportForm.change_1000_added,

    "transfer_answer":
        TerminalReportForm.transfer_answer,

    "transfer_source":
        TerminalReportForm.transfer_source,

    "salary": TerminalReportForm.salary,
    "additional": TerminalReportForm.additional,
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
        "Сколько было до добавления?\n\n"
        "Например: <b>5000</b>"
    ),

    "change_100_added": (
        "💵 <b>Сдача по 100 ₽</b>\n\n"
        "Сколько добавили?\n\n"
        "Если не добавляли, напишите <b>0</b>"
    ),

    "change_1000_before": (
        "💸 <b>Сдача по 1000 ₽</b>\n\n"
        "Сколько было до добавления?\n\n"
        "Например: <b>10000</b>"
    ),

    "change_1000_added": (
        "💸 <b>Сдача по 1000 ₽</b>\n\n"
        "Сколько добавили?\n\n"
        "Если не добавляли, напишите <b>0</b>"
    ),

    "transfer_answer": (
        "🔄 <b>Добавленную сдачу взяли "
        "с другого терминала?</b>\n\n"
        "Ответьте кнопкой ниже."
    ),

    "transfer_source": (
        "📤 <b>Выберите терминал, "
        "откуда взяли сдачу:</b>"
    ),

    "salary": (
        "👤 Введите сумму ЗП себе:\n\n"
        "Если не брали, напишите <b>0</b>"
    ),

    "additional": (
        "📝 Введите дополнительные расходы:\n\n"
        "Например:\n"
        "<b>продавцу 4000, чеки 3000</b>\n\n"
        "Если расходов нет, напишите:\n"
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
        "Например:\n"
        "<b>Передано владельцу помещения</b>\n\n"
        "Если комментария нет, напишите:\n"
        "<b>нет</b>"
    ),
}


# ============================================================
# БАЗА ДАННЫХ
# ============================================================

def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row

    return connection


def column_exists(
    cursor: sqlite3.Cursor,
    table_name: str,
    column_name: str,
) -> bool:
    cursor.execute(
        f"PRAGMA table_info({table_name})"
    )

    columns = cursor.fetchall()

    return any(
        column[1] == column_name
        for column in columns
    )


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

    if not column_exists(
        cur,
        "reports",
        "deleted",
    ):
        cur.execute("""
            ALTER TABLE reports
            ADD COLUMN deleted INTEGER
            NOT NULL DEFAULT 0
        """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS
        terminal_transfers (
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
            [
                KeyboardButton(
                    text="📊 Новый отчет"
                )
            ],
            [
                KeyboardButton(
                    text="🏠 Оплата аренды"
                )
            ],
            [
                KeyboardButton(
                    text="👨‍💼 Админ панель"
                )
            ],
        ],
        resize_keyboard=True,
    )


def form_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="⬅️ Назад"
                ),
                KeyboardButton(
                    text="❌ Отменить полностью"
                ),
            ]
        ],
        resize_keyboard=True,
    )


def terminal_choice_keyboard(
    exclude_terminal: str | None = None,
) -> ReplyKeyboardMarkup:
    rows = []

    for terminal in TERMINALS:
        if (
            exclude_terminal
            and terminal.casefold()
            == exclude_terminal.casefold()
        ):
            continue

        rows.append([
            KeyboardButton(text=terminal)
        ])

    rows.append([
        KeyboardButton(
            text="❌ Отменить полностью"
        )
    ])

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def transfer_answer_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="✅ Да, с другого терминала"
                )
            ],
            [
                KeyboardButton(
                    text="❌ Нет, внешнее пополнение"
                )
            ],
            [
                KeyboardButton(
                    text="⬅️ Назад"
                ),
                KeyboardButton(
                    text="❌ Отменить полностью"
                ),
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
                    callback_data=(
                        "terminal_confirm_send"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Заполнить заново",
                    callback_data=(
                        "terminal_restart"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data=(
                        "terminal_confirm_cancel"
                    ),
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
                    callback_data=(
                        "rent_confirm_send"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Заполнить заново",
                    callback_data=(
                        "rent_restart"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data=(
                        "rent_confirm_cancel"
                    ),
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
                    callback_data=(
                        "admin_reports_today"
                    ),
                ),
                InlineKeyboardButton(
                    text="📆 Неделя",
                    callback_data=(
                        "admin_reports_week"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗓 Месяц",
                    callback_data=(
                        "admin_reports_month"
                    ),
                ),
                InlineKeyboardButton(
                    text="📚 Всё время",
                    callback_data=(
                        "admin_reports_all"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📋 Последние 10",
                    callback_data=(
                        "admin_reports_last10"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Аренда",
                    callback_data=(
                        "admin_rent_menu"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏧 Все терминалы",
                    callback_data=(
                        "admin_terminals_all"
                    ),
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
                    callback_data=(
                        "admin_rent_today"
                    ),
                ),
                InlineKeyboardButton(
                    text="📆 Неделя",
                    callback_data=(
                        "admin_rent_week"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗓 Месяц",
                    callback_data=(
                        "admin_rent_month"
                    ),
                ),
                InlineKeyboardButton(
                    text="📚 Всё время",
                    callback_data=(
                        "admin_rent_all"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📋 Последние 10",
                    callback_data=(
                        "admin_rent_last10"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=(
                        "admin_main_menu"
                    ),
                )
            ],
        ]
    )


def report_delete_keyboard(
    report_id: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Удалить отчет",
                    callback_data=(
                        f"delete_report_{report_id}"
                    ),
                )
            ]
        ]
    )


def rent_delete_keyboard(
    rent_id: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Удалить аренду",
                    callback_data=(
                        f"delete_rent_{rent_id}"
                    ),
                )
            ]
        ]
    )


# ============================================================
# ОБЩИЕ ФУНКЦИИ
# ============================================================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def get_user_name(obj) -> str:
    user = obj.from_user

    return (
        user.full_name
        or user.username
        or str(user.id)
    )


def now_db() -> str:
    return datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def now_display() -> str:
    return datetime.now().strftime(
        "%d.%m.%Y %H:%M"
    )


def format_money(amount: int | float) -> str:
    amount = round(amount)

    return (
        f"{amount:,}".replace(",", " ")
        + " ₽"
    )


def parse_money(text: str) -> int:
    cleaned = re.sub(
        r"[^\d]",
        "",
        text or "",
    )

    if not cleaned:
        raise ValueError(
            "Сумма не найдена"
        )

    return int(cleaned)


def parse_additional(
    text: str,
) -> tuple[int, str]:
    text = (text or "").strip()

    if text.lower() in {
        "нет",
        "ничего",
        "no",
        "-",
        "0",
    }:
        return 0, "• Нет"

    parts = re.split(
        r"[,;\n]+",
        text,
    )

    items = []

    for part in parts:
        part = part.strip()

        if not part:
            continue

        match = re.search(
            r"(.+?)[\s:—-]+"
            r"([\d\s.,]+)\s*₽?$",
            part,
        )

        if not match:
            continue

        name = (
            match.group(1)
            .strip()
            .capitalize()
        )

        amount = parse_money(
            match.group(2)
        )

        items.append(
            (name, amount)
        )

    if not items:
        return (
            0,
            f"• {escape(text)}",
        )

    total = sum(
        amount
        for _, amount in items
    )

    formatted = "\n".join(
        f"• {escape(name)}: "
        f"{format_money(amount)}"
        for name, amount in items
    )

    return total, formatted


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


def get_period_start(
    period: str,
) -> datetime | None:
    now = datetime.now()

    if period == "today":
        return now.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

    if period == "week":
        start = now - timedelta(
            days=now.weekday()
        )

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

    return None


def period_title(period: str) -> str:
    return {
        "today": "ЗА СЕГОДНЯ",
        "week": "ЗА НЕДЕЛЮ",
        "month": "ЗА МЕСЯЦ",
        "all": "ЗА ВСЁ ВРЕМЯ",
    }.get(period, "")


async def send_to_target_chat(
    text: str,
    source_chat_id: int,
    reply_markup: InlineKeyboardMarkup
    | None = None,
):
    target_chat_id = (
        REPORT_CHAT_ID
        or source_chat_id
    )

    await bot.send_message(
        chat_id=target_chat_id,
        text=text,
        reply_markup=reply_markup,
    )


# ============================================================
# РАСЧЕТ ОТЧЕТА
# ============================================================

def calculate_terminal_report(
    data: dict,
) -> dict:
    change_100_after = (
        data["change_100_before"]
        + data["change_100_added"]
    )

    change_1000_after = (
        data["change_1000_before"]
        + data["change_1000_added"]
    )

    (
        additional_total,
        additional_text,
    ) = parse_additional(
        data.get(
            "additional",
            "нет",
        )
    )

    withheld_total = (
        data["change_100_added"]
        + data["change_1000_added"]
        + data["salary"]
        + additional_total
    )

    final_amount = (
        data["total_amount"]
        - withheld_total
    )

    transfer_from_other = bool(
        data.get(
            "transfer_from_other",
            False,
        )
    )

    transfer_source = (
        data.get(
            "transfer_source",
            "",
        )
        or ""
    ).strip()

    transfer_total = 0

    if transfer_from_other:
        transfer_total = (
            data["change_100_added"]
            + data["change_1000_added"]
        )

    return {
        "terminal":
            data["terminal"],

        "total_amount":
            data["total_amount"],

        "change_100_before":
            data["change_100_before"],

        "change_100_added":
            data["change_100_added"],

        "change_100_after":
            change_100_after,

        "change_1000_before":
            data["change_1000_before"],

        "change_1000_added":
            data["change_1000_added"],

        "change_1000_after":
            change_1000_after,

        "salary":
            data["salary"],

        "additional_text":
            additional_text,

        "additional_total":
            additional_total,

        "withheld_total":
            withheld_total,

        "final_amount":
            final_amount,

        "transfer_from_other":
            transfer_from_other,

        "transfer_source":
            transfer_source,

        "transfer_total":
            transfer_total,
    }


def build_terminal_report_text(
    calc: dict,
    user_name: str,
    report_id: int | None = None,
    created_at: str | None = None,
) -> str:
    if created_at is None:
        created_at = now_display()

    number_text = ""

    if report_id is not None:
        number_text = (
            f"📄 <b>Отчет №{report_id}</b>"
            "\n\n"
        )

    transfer_text = ""

    if (
        calc.get("transfer_from_other")
        and calc.get("transfer_source")
        and calc.get("transfer_total", 0) > 0
    ):
        transfer_text = (
            "\n🔄 <b>СДАЧА ПОЛУЧЕНА "
            "С ДРУГОГО ТЕРМИНАЛА</b>\n\n"

            f"📤 Откуда: "
            f"<b>{escape(calc['transfer_source'])}</b>\n"

            f"📥 Куда: "
            f"<b>{escape(calc['terminal'])}</b>\n\n"

            f"• По 100 ₽: "
            f"{format_money(calc['change_100_added'])}\n"

            f"• По 1000 ₽: "
            f"{format_money(calc['change_1000_added'])}\n"

            f"• Всего: "
            f"<b>{format_money(calc['transfer_total'])}</b>\n"
        )

    return f"""
{number_text}📊 <b>ОТЧЕТ ПО ТЕРМИНАЛУ</b>

🏧 <b>Терминал:</b>
{escape(calc["terminal"])}

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

{transfer_text}
👤 <b>ЗП себе:</b>
{format_money(calc["salary"])}

📝 <b>Дополнительно:</b>
{calc["additional_text"]}

━━━━━━━━━━━━━━

📉 <b>Удержано:</b>

• Сдача 100 ₽:
{format_money(calc["change_100_added"])}

• Сдача 1000 ₽:
{format_money(calc["change_1000_added"])}

• ЗП:
{format_money(calc["salary"])}

• Доп. расходы:
{format_money(calc["additional_total"])}

💵 <b>НА РУКАХ:</b>
<b>{format_money(calc["final_amount"])}</b>

👤 <b>Отчет отправил:</b>
{escape(user_name)}

🕒 {created_at}
""".strip()


# ============================================================
# СОХРАНЕНИЕ ОТЧЕТА И ПЕРЕВОДА
# ============================================================

def save_terminal_report(
    calc: dict,
    user_id: int,
    user_name: str,
) -> int:
    conn = get_connection()
    cur = conn.cursor()

    created_at = now_db()

    try:
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
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, 0
            )
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

        report_id = int(
            cur.lastrowid
        )

        if (
            calc.get("transfer_from_other")
            and calc.get("transfer_source")
            and calc.get("transfer_total", 0) > 0
        ):
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
                VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, 0
                )
            """, (
                report_id,

                calc["transfer_source"],
                calc["terminal"],

                calc["change_100_added"],
                calc["change_1000_added"],
                calc["transfer_total"],

                user_id,
                user_name,

                created_at,
            ))

        conn.commit()

        return report_id

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


# ============================================================
# ШАГИ ОТЧЕТА
# ============================================================

async def ask_terminal_step(
    message: Message,
    state: FSMContext,
    step: str,
):
    await state.update_data(
        flow_type="terminal",
        current_step=step,
    )

    await state.set_state(
        TERMINAL_STATE_BY_STEP[step]
    )

    if step == "terminal":
        keyboard = terminal_choice_keyboard()

    elif step == "transfer_answer":
        keyboard = transfer_answer_keyboard()

    elif step == "transfer_source":
        data = await state.get_data()

        keyboard = terminal_choice_keyboard(
            exclude_terminal=str(
                data.get("terminal", "")
            )
        )

    else:
        keyboard = form_keyboard()

    await message.answer(
        TERMINAL_QUESTIONS[step],
        reply_markup=keyboard,
    )


async def process_terminal_step(
    message: Message,
    state: FSMContext,
    step: str,
    value,
):
    await state.update_data(
        **{step: value}
    )

    index = TERMINAL_STEPS.index(step)

    if step == "transfer_answer":
        return

    if index == len(TERMINAL_STEPS) - 1:
        await show_terminal_preview(
            message,
            state,
        )
        return

    next_step = TERMINAL_STEPS[
        index + 1
    ]

    await ask_terminal_step(
        message,
        state,
        next_step,
    )


async def show_terminal_preview(
    message: Message,
    state: FSMContext,
):
    data = await state.get_data()

    calc = calculate_terminal_report(
        data
    )

    if calc["final_amount"] < 0:
        await message.answer(
            "⚠️ Сумма удержаний больше "
            "общей суммы.\n\n"
            "Нажмите «Заполнить заново» "
            "и исправьте данные.",
            reply_markup=(
                terminal_confirm_keyboard()
            ),
        )
        return

    text = build_terminal_report_text(
        calc=calc,
        user_name=get_user_name(message),
    )

    await message.answer(
        "📋 <b>ПРОВЕРЬТЕ ОТЧЕТ</b>\n\n"
        + text,
        reply_markup=(
            terminal_confirm_keyboard()
        ),
    )


# ============================================================
# АРЕНДА
# ============================================================

async def ask_rent_step(
    message: Message,
    state: FSMContext,
    step: str,
):
    await state.update_data(
        flow_type="rent",
        current_step=step,
    )

    await state.set_state(
        RENT_STATE_BY_STEP[step]
    )

    if step == "terminal":
        keyboard = terminal_choice_keyboard()
    else:
        keyboard = form_keyboard()

    await message.answer(
        RENT_QUESTIONS[step],
        reply_markup=keyboard,
    )


async def process_rent_step(
    message: Message,
    state: FSMContext,
    step: str,
    value,
):
    await state.update_data(
        **{step: value}
    )

    index = RENT_STEPS.index(step)

    if index == len(RENT_STEPS) - 1:
        await show_rent_preview(
            message,
            state,
        )
        return

    next_step = RENT_STEPS[
        index + 1
    ]

    await ask_rent_step(
        message,
        state,
        next_step,
    )


def build_rent_text(
    data: dict,
    user_name: str,
    rent_id: int | None = None,
    created_at: str | None = None,
) -> str:
    if created_at is None:
        created_at = now_display()

    number_text = ""

    if rent_id is not None:
        number_text = (
            f"📄 <b>Аренда №{rent_id}</b>"
            "\n\n"
        )

    return f"""
{number_text}🏠 <b>ОПЛАТА АРЕНДЫ</b>

🏧 <b>Терминал:</b>
{escape(data["terminal"])}

💰 <b>Сумма:</b>
{format_money(data["amount"])}

📅 <b>Период:</b>
{escape(data["rent_period"])}

📝 <b>Комментарий:</b>
{escape(normalize_comment(data["comment"]))}

👤 <b>Добавил:</b>
{escape(user_name)}

🕒 {created_at}
""".strip()


async def show_rent_preview(
    message: Message,
    state: FSMContext,
):
    data = await state.get_data()

    text = build_rent_text(
        data=data,
        user_name=get_user_name(message),
    )

    await message.answer(
        "📋 <b>ПРОВЕРЬТЕ АРЕНДУ</b>\n\n"
        + text,
        reply_markup=rent_confirm_keyboard(),
    )


def save_rent_payment(
    data: dict,
    user_id: int,
    user_name: str,
) -> int:
    conn = get_connection()
    cur = conn.cursor()

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
        normalize_comment(
            data["comment"]
        ),

        user_id,
        user_name,

        now_db(),
    ))

    rent_id = int(
        cur.lastrowid
    )

    conn.commit()
    conn.close()

    return rent_id


# ============================================================
# КНОПКА НАЗАД
# ============================================================

async def go_back(
    message: Message,
    state: FSMContext,
):
    data = await state.get_data()

    flow_type = data.get(
        "flow_type"
    )

    current_step = data.get(
        "current_step"
    )

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
        await state.clear()

        await message.answer(
            "Действие отменено.",
            reply_markup=start_keyboard(),
        )
        return

    if current_step not in steps:
        await state.clear()

        await message.answer(
            "Действие отменено.",
            reply_markup=start_keyboard(),
        )
        return

    current_index = steps.index(
        current_step
    )

    if current_index == 0:
        await message.answer(
            "Вы уже на первом вопросе."
        )
        return

    previous_step = steps[
        current_index - 1
    ]

    if (
        flow_type == "terminal"
        and current_step == "salary"
        and not data.get(
            "transfer_from_other",
            False,
        )
    ):
        previous_step = (
            "transfer_answer"
        )

    await ask_function(
        message,
        state,
        previous_step,
    )


# ============================================================
# АДМИНСКАЯ СТАТИСТИКА
# ============================================================

def get_transfer_totals(
    cursor: sqlite3.Cursor,
    terminal: str,
    start: datetime | None = None,
) -> tuple[int, int]:
    params_out = [terminal]
    params_in = [terminal]

    date_condition = ""

    if start is not None:
        date_condition = (
            " AND created_at >= ?"
        )

        start_text = start.strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        params_out.append(start_text)
        params_in.append(start_text)

    cursor.execute(
        f"""
        SELECT COALESCE(
            SUM(total_amount),
            0
        )
        FROM terminal_transfers
        WHERE source_terminal = ?
          AND deleted = 0
          {date_condition}
        """,
        tuple(params_out),
    )

    outgoing = int(
        cursor.fetchone()[0]
    )

    cursor.execute(
        f"""
        SELECT COALESCE(
            SUM(total_amount),
            0
        )
        FROM terminal_transfers
        WHERE destination_terminal = ?
          AND deleted = 0
          {date_condition}
        """,
        tuple(params_in),
    )

    incoming = int(
        cursor.fetchone()[0]
    )

    return incoming, outgoing


def build_reports_summary(
    period: str,
) -> str:
    start = get_period_start(period)

    conn = get_connection()
    cur = conn.cursor()

    if start is None:
        where = "WHERE deleted = 0"
        params = ()
    else:
        where = (
            "WHERE deleted = 0 "
            "AND created_at >= ?"
        )

        params = (
            start.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        )

    cur.execute(
        f"""
        SELECT
            COUNT(id),
            COALESCE(
                SUM(total_amount),
                0
            ),
            COALESCE(
                SUM(withheld_total),
                0
            ),
            COALESCE(
                SUM(final_amount),
                0
            )
        FROM reports
        {where}
        """,
        params,
    )

    row = cur.fetchone()

    count = int(row[0])
    total_sum = int(row[1])
    withheld_sum = int(row[2])
    final_sum = int(row[3])

    terminal_lines = []

    adjusted_grand_total = 0

    for terminal in TERMINALS:
        terminal_params = [terminal]

        terminal_where = (
            "WHERE terminal = ? "
            "AND deleted = 0"
        )

        if start is not None:
            terminal_where += (
                " AND created_at >= ?"
            )

            terminal_params.append(
                start.strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            )

        cur.execute(
            f"""
            SELECT
                COUNT(id),
                COALESCE(
                    SUM(final_amount),
                    0
                )
            FROM reports
            {terminal_where}
            """,
            tuple(terminal_params),
        )

        terminal_row = cur.fetchone()

        report_count = int(
            terminal_row[0]
        )

        terminal_final = int(
            terminal_row[1]
        )

        incoming, outgoing = (
            get_transfer_totals(
                cur,
                terminal,
                start,
            )
        )

        adjusted_balance = (
            terminal_final
            + incoming
            - outgoing
        )

        adjusted_grand_total += (
            adjusted_balance
        )

        terminal_lines.append(
            f"🏧 <b>{escape(terminal)}</b>\n"
            f"• Отчетов: {report_count}\n"
            f"• По отчетам: "
            f"{format_money(terminal_final)}\n"
            f"• Получено: "
            f"{format_money(incoming)}\n"
            f"• Передано: "
            f"{format_money(outgoing)}\n"
            f"• Итоговый баланс: "
            f"<b>{format_money(adjusted_balance)}</b>"
        )

    conn.close()

    terminal_text = (
        "\n\n".join(terminal_lines)
    )

    return f"""
📊 <b>ОТЧЕТЫ {period_title(period)}</b>

📌 Количество отчетов:
<b>{count}</b>

💰 Общая сумма:
<b>{format_money(total_sum)}</b>

📉 Всего удержано:
<b>{format_money(withheld_sum)}</b>

💵 На руках по отчетам:
<b>{format_money(final_sum)}</b>

🔄 Итог с переводами:
<b>{format_money(adjusted_grand_total)}</b>

━━━━━━━━━━━━━━

{terminal_text}
""".strip()


def build_last_reports(
    limit: int = 10,
) -> str:
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

    text = (
        "📋 <b>ПОСЛЕДНИЕ ОТЧЕТЫ</b>\n\n"
    )

    for row in rows:
        text += (
            f"📄 №{row['id']}\n"
            f"🏧 {escape(row['terminal'])}\n"
            f"💵 На руках: "
            f"<b>{format_money(row['final_amount'])}</b>\n"
            f"👤 {escape(row['user_name'])}\n"
            f"🕒 {row['created_at']}\n\n"
        )

    return text.strip()


def build_all_terminals_summary() -> str:
    conn = get_connection()
    cur = conn.cursor()

    text = (
        "🏧 <b>ТЕРМИНАЛЫ "
        "ЗА ВСЁ ВРЕМЯ</b>\n\n"
    )

    grand_reports = 0
    grand_incoming = 0
    grand_outgoing = 0
    grand_adjusted = 0
    grand_rent = 0

    for terminal in TERMINALS:
        cur.execute("""
            SELECT
                COUNT(id),
                COALESCE(
                    SUM(total_amount),
                    0
                ),
                COALESCE(
                    SUM(withheld_total),
                    0
                ),
                COALESCE(
                    SUM(final_amount),
                    0
                )
            FROM reports
            WHERE terminal = ?
              AND deleted = 0
        """, (terminal,))

        row = cur.fetchone()

        report_count = int(row[0])
        total_sum = int(row[1])
        withheld_sum = int(row[2])
        final_sum = int(row[3])

        incoming, outgoing = (
            get_transfer_totals(
                cur,
                terminal,
            )
        )

        adjusted_balance = (
            final_sum
            + incoming
            - outgoing
        )

        cur.execute("""
            SELECT
                COUNT(id),
                COALESCE(
                    SUM(amount),
                    0
                )
            FROM rent_payments
            WHERE terminal = ?
              AND deleted = 0
        """, (terminal,))

        rent_row = cur.fetchone()

        rent_count = int(
            rent_row[0]
        )

        rent_sum = int(
            rent_row[1]
        )

        grand_reports += final_sum
        grand_incoming += incoming
        grand_outgoing += outgoing
        grand_adjusted += (
            adjusted_balance
        )
        grand_rent += rent_sum

        text += (
            f"🏧 <b>{escape(terminal)}</b>\n"
            f"📊 Отчетов: {report_count}\n"
            f"💰 Общая сумма: "
            f"{format_money(total_sum)}\n"
            f"📉 Удержано: "
            f"{format_money(withheld_sum)}\n"
            f"💵 На руках по отчетам: "
            f"{format_money(final_sum)}\n"
            f"📥 Получено с терминалов: "
            f"{format_money(incoming)}\n"
            f"📤 Передано терминалам: "
            f"{format_money(outgoing)}\n"
            f"✅ Баланс с переводами: "
            f"<b>{format_money(adjusted_balance)}</b>\n"
            f"🏠 Оплат аренды: {rent_count}\n"
            f"🏠 Аренда: "
            f"{format_money(rent_sum)}\n\n"
        )

    conn.close()

    text += (
        "━━━━━━━━━━━━━━\n\n"

        f"💵 По отчетам:\n"
        f"<b>{format_money(grand_reports)}</b>\n\n"

        f"📥 Получено:\n"
        f"<b>{format_money(grand_incoming)}</b>\n\n"

        f"📤 Передано:\n"
        f"<b>{format_money(grand_outgoing)}</b>\n\n"

        f"✅ Итоговый баланс:\n"
        f"<b>{format_money(grand_adjusted)}</b>\n\n"

        f"🏠 Всего аренда:\n"
        f"<b>{format_money(grand_rent)}</b>"
    )

    return text.strip()


def build_terminal_all_time_summary(
    terminal: str,
) -> str:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            COUNT(id),
            COALESCE(
                SUM(total_amount),
                0
            ),
            COALESCE(
                SUM(withheld_total),
                0
            ),
            COALESCE(
                SUM(final_amount),
                0
            )
        FROM reports
        WHERE terminal = ?
          AND deleted = 0
    """, (terminal,))

    row = cur.fetchone()

    report_count = int(row[0])
    total_sum = int(row[1])
    withheld_sum = int(row[2])
    final_sum = int(row[3])

    incoming, outgoing = (
        get_transfer_totals(
            cur,
            terminal,
        )
    )

    adjusted_balance = (
        final_sum
        + incoming
        - outgoing
    )

    cur.execute("""
        SELECT
            COUNT(id),
            COALESCE(
                SUM(amount),
                0
            )
        FROM rent_payments
        WHERE terminal = ?
          AND deleted = 0
    """, (terminal,))

    rent_row = cur.fetchone()

    rent_count = int(rent_row[0])
    rent_sum = int(rent_row[1])

    cur.execute("""
        SELECT
            id,
            source_terminal,
            destination_terminal,
            amount_100,
            amount_1000,
            total_amount,
            created_at
        FROM terminal_transfers
        WHERE (
            source_terminal = ?
            OR destination_terminal = ?
        )
          AND deleted = 0
        ORDER BY id DESC
        LIMIT 10
    """, (
        terminal,
        terminal,
    ))

    transfer_rows = cur.fetchall()

    conn.close()

    transfer_text = ""

    for transfer in transfer_rows:
        if (
            transfer["source_terminal"]
            == terminal
        ):
            direction = (
                "📤 Передано в "
                f"{escape(transfer['destination_terminal'])}"
            )
        else:
            direction = (
                "📥 Получено из "
                f"{escape(transfer['source_terminal'])}"
            )

        transfer_text += (
            f"• №{transfer['id']} — "
            f"{direction}\n"
            f"  По 100 ₽: "
            f"{format_money(transfer['amount_100'])}\n"
            f"  По 1000 ₽: "
            f"{format_money(transfer['amount_1000'])}\n"
            f"  Всего: "
            f"{format_money(transfer['total_amount'])}\n"
            f"  {transfer['created_at']}\n\n"
        )

    if not transfer_text:
        transfer_text = (
            "• Переводов пока нет"
        )

    return f"""
🏧 <b>ТЕРМИНАЛ: {escape(terminal)}</b>

📊 Отчетов:
<b>{report_count}</b>

💰 Общая сумма:
<b>{format_money(total_sum)}</b>

📉 Всего удержано:
<b>{format_money(withheld_sum)}</b>

💵 На руках по отчетам:
<b>{format_money(final_sum)}</b>

📥 Получено с других терминалов:
<b>{format_money(incoming)}</b>

📤 Передано другим терминалам:
<b>{format_money(outgoing)}</b>

✅ <b>БАЛАНС С ПЕРЕВОДАМИ:</b>
<b>{format_money(adjusted_balance)}</b>

🏠 Оплат аренды:
<b>{rent_count}</b>

🏠 Всего аренда:
<b>{format_money(rent_sum)}</b>

━━━━━━━━━━━━━━

🔄 <b>Последние переводы:</b>

{transfer_text}
""".strip()


def build_rent_summary(
    period: str,
) -> str:
    start = get_period_start(period)

    conn = get_connection()
    cur = conn.cursor()

    if start is None:
        where = "WHERE deleted = 0"
        params = ()
    else:
        where = (
            "WHERE deleted = 0 "
            "AND created_at >= ?"
        )

        params = (
            start.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        )

    cur.execute(
        f"""
        SELECT
            COUNT(id),
            COALESCE(
                SUM(amount),
                0
            )
        FROM rent_payments
        {where}
        """,
        params,
    )

    row = cur.fetchone()

    count = int(row[0])
    total_amount = int(row[1])

    cur.execute(
        f"""
        SELECT
            id,
            terminal,
            amount,
            rent_period,
            user_name,
            created_at
        FROM rent_payments
        {where}
        ORDER BY id DESC
        LIMIT 30
        """,
        params,
    )

    rows = cur.fetchall()
    conn.close()

    details = ""

    for rent in rows:
        details += (
            f"📄 №{rent['id']}\n"
            f"🏧 {escape(rent['terminal'])}\n"
            f"💰 {format_money(rent['amount'])}\n"
            f"📅 {escape(rent['rent_period'])}\n"
            f"👤 {escape(rent['user_name'])}\n"
            f"🕒 {rent['created_at']}\n\n"
        )

    if not details:
        details = "• Нет данных"

    return f"""
🏠 <b>АРЕНДА {period_title(period)}</b>

📌 Количество оплат:
<b>{count}</b>

💰 Всего отдали:
<b>{format_money(total_amount)}</b>

━━━━━━━━━━━━━━

{details}
""".strip()


def build_last_rent_payments(
    limit: int = 10,
) -> str:
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
        return (
            "🏠 Записей об аренде пока нет."
        )

    text = (
        "🏠 <b>ПОСЛЕДНИЕ ОПЛАТЫ "
        "АРЕНДЫ</b>\n\n"
    )

    for rent in rows:
        text += (
            f"📄 №{rent['id']}\n"
            f"🏧 {escape(rent['terminal'])}\n"
            f"💰 {format_money(rent['amount'])}\n"
            f"📅 {escape(rent['rent_period'])}\n"
            f"👤 {escape(rent['user_name'])}\n"
            f"🕒 {rent['created_at']}\n\n"
        )

    return text.strip()


# ============================================================
# ПОЛУЧЕНИЕ ОТЧЕТА ПО ID
# ============================================================

def get_report_text_by_id(
    report_id: int,
) -> str:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM reports
        WHERE id = ?
    """, (report_id,))

    report = cur.fetchone()

    if not report:
        conn.close()

        return "❌ Отчет не найден."

    if report["deleted"]:
        conn.close()

        return (
            f"🗑 Отчет №{report_id} удален."
        )

    cur.execute("""
        SELECT *
        FROM terminal_transfers
        WHERE report_id = ?
          AND deleted = 0
        LIMIT 1
    """, (report_id,))

    transfer = cur.fetchone()

    conn.close()

    calc = {
        "terminal":
            report["terminal"],

        "total_amount":
            report["total_amount"],

        "change_100_before":
            report["change_100_before"],

        "change_100_added":
            report["change_100_added"],

        "change_100_after":
            report["change_100_after"],

        "change_1000_before":
            report["change_1000_before"],

        "change_1000_added":
            report["change_1000_added"],

        "change_1000_after":
            report["change_1000_after"],

        "salary":
            report["salary"],

        "additional_text":
            report["additional_text"],

        "additional_total":
            report["additional_total"],

        "withheld_total":
            report["withheld_total"],

        "final_amount":
            report["final_amount"],

        "transfer_from_other":
            bool(transfer),

        "transfer_source":
            (
                transfer["source_terminal"]
                if transfer else ""
            ),

        "transfer_total":
            (
                transfer["total_amount"]
                if transfer else 0
            ),
    }

    return build_terminal_report_text(
        calc=calc,
        user_name=report["user_name"],
        report_id=report_id,
        created_at=report["created_at"],
    )


def get_rent_text_by_id(
    rent_id: int,
) -> str:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM rent_payments
        WHERE id = ?
    """, (rent_id,))

    rent = cur.fetchone()
    conn.close()

    if not rent:
        return (
            "❌ Запись аренды не найдена."
        )

    if rent["deleted"]:
        return (
            f"🗑 Аренда №{rent_id} удалена."
        )

    data = {
        "terminal":
            rent["terminal"],

        "amount":
            rent["amount"],

        "rent_period":
            rent["rent_period"],

        "comment":
            rent["comment"],
    }

    return build_rent_text(
        data=data,
        user_name=rent["user_name"],
        rent_id=rent_id,
        created_at=rent["created_at"],
    )


# ============================================================
# КОМАНДЫ
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
async def myid_handler(
    message: Message,
):
    await message.answer(
        "Ваш Telegram ID:\n"
        f"<code>{message.from_user.id}</code>"
    )


@dp.message(Command("chatid"))
async def chatid_handler(
    message: Message,
):
    await message.answer(
        "ID этого чата:\n"
        f"<code>{message.chat.id}</code>"
    )


@dp.message(Command("cancel"))
async def cancel_command(
    message: Message,
    state: FSMContext,
):
    await state.clear()

    await message.answer(
        "❌ Заполнение отменено.",
        reply_markup=start_keyboard(),
    )


@dp.message(Command("admin"))
async def admin_command(
    message: Message,
):
    if not is_admin(
        message.from_user.id
    ):
        await message.answer(
            "⛔ У вас нет доступа.\n\n"
            "Узнать ID: /myid"
        )
        return

    await message.answer(
        "👨‍💼 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
        "Выберите раздел:",
        reply_markup=admin_keyboard(),
    )


@dp.message(Command("report"))
async def report_by_id_command(
    message: Message,
):
    if not is_admin(
        message.from_user.id
    ):
        await message.answer(
            "⛔ Нет доступа."
        )
        return

    parts = message.text.split(
        maxsplit=1
    )

    if (
        len(parts) != 2
        or not parts[1].isdigit()
    ):
        await message.answer(
            "Использование:\n"
            "<code>/report 15</code>"
        )
        return

    await message.answer(
        get_report_text_by_id(
            int(parts[1])
        )
    )


@dp.message(Command("rent"))
async def rent_by_id_command(
    message: Message,
):
    if not is_admin(
        message.from_user.id
    ):
        await message.answer(
            "⛔ Нет доступа."
        )
        return

    parts = message.text.split(
        maxsplit=1
    )

    if (
        len(parts) != 2
        or not parts[1].isdigit()
    ):
        await message.answer(
            "Использование:\n"
            "<code>/rent 8</code>"
        )
        return

    await message.answer(
        get_rent_text_by_id(
            int(parts[1])
        )
    )


@dp.message(Command("terminal"))
async def terminal_command(
    message: Message,
):
    if not is_admin(
        message.from_user.id
    ):
        await message.answer(
            "⛔ Нет доступа."
        )
        return

    parts = message.text.split(
        maxsplit=1
    )

    if len(parts) != 2:
        await message.answer(
            "Использование:\n"
            "<code>/terminal 20-й</code>\n"
            "<code>/terminal Бирлога 1</code>"
        )
        return

    terminal = parts[1].strip()

    if terminal not in TERMINALS:
        await message.answer(
            "❌ Такого терминала нет.\n\n"
            "Доступные терминалы:\n"
            + "\n".join(
                f"• {name}"
                for name in TERMINALS
            )
        )
        return

    await message.answer(
        build_terminal_all_time_summary(
            terminal
        )
    )


# ============================================================
# ГЛАВНОЕ МЕНЮ
# ============================================================

@dp.message(F.text == "📊 Новый отчет")
async def new_report_handler(
    message: Message,
    state: FSMContext,
):
    await state.clear()

    await ask_terminal_step(
        message,
        state,
        "terminal",
    )


@dp.message(F.text == "🏠 Оплата аренды")
async def new_rent_handler(
    message: Message,
    state: FSMContext,
):
    await state.clear()

    await ask_rent_step(
        message,
        state,
        "terminal",
    )


@dp.message(F.text == "👨‍💼 Админ панель")
async def admin_button_handler(
    message: Message,
):
    await admin_command(message)


@dp.message(F.text == "⬅️ Назад")
async def back_handler(
    message: Message,
    state: FSMContext,
):
    await go_back(
        message,
        state,
    )


@dp.message(
    F.text == "❌ Отменить полностью"
)
async def full_cancel_handler(
    message: Message,
    state: FSMContext,
):
    await state.clear()

    await message.answer(
        "❌ Заполнение отменено.",
        reply_markup=start_keyboard(),
    )


# ============================================================
# ОБРАБОТЧИКИ ОТЧЕТА
# ============================================================

@dp.message(TerminalReportForm.terminal)
async def terminal_name_handler(
    message: Message,
    state: FSMContext,
):
    value = (
        message.text or ""
    ).strip()

    if value not in TERMINALS:
        await message.answer(
            "❌ Выберите терминал "
            "кнопкой ниже.",
            reply_markup=(
                terminal_choice_keyboard()
            ),
        )
        return

    await process_terminal_step(
        message,
        state,
        "terminal",
        value,
    )


@dp.message(
    TerminalReportForm.total_amount
)
async def total_amount_handler(
    message: Message,
    state: FSMContext,
):
    try:
        value = parse_money(
            message.text
        )
    except ValueError:
        await message.answer(
            "Введите сумму цифрами."
        )
        return

    await process_terminal_step(
        message,
        state,
        "total_amount",
        value,
    )


@dp.message(
    TerminalReportForm.change_100_before
)
async def change_100_before_handler(
    message: Message,
    state: FSMContext,
):
    try:
        value = parse_money(
            message.text
        )
    except ValueError:
        await message.answer(
            "Введите сумму цифрами."
        )
        return

    await process_terminal_step(
        message,
        state,
        "change_100_before",
        value,
    )


@dp.message(
    TerminalReportForm.change_100_added
)
async def change_100_added_handler(
    message: Message,
    state: FSMContext,
):
    try:
        value = parse_money(
            message.text
        )
    except ValueError:
        await message.answer(
            "Введите сумму цифрами."
        )
        return

    await process_terminal_step(
        message,
        state,
        "change_100_added",
        value,
    )


@dp.message(
    TerminalReportForm.change_1000_before
)
async def change_1000_before_handler(
    message: Message,
    state: FSMContext,
):
    try:
        value = parse_money(
            message.text
        )
    except ValueError:
        await message.answer(
            "Введите сумму цифрами."
        )
        return

    await process_terminal_step(
        message,
        state,
        "change_1000_before",
        value,
    )


@dp.message(
    TerminalReportForm.change_1000_added
)
async def change_1000_added_handler(
    message: Message,
    state: FSMContext,
):
    try:
        value = parse_money(
            message.text
        )
    except ValueError:
        await message.answer(
            "Введите сумму цифрами."
        )
        return

    await state.update_data(
        change_1000_added=value
    )

    data = await state.get_data()

    total_added = (
        data.get(
            "change_100_added",
            0,
        )
        + value
    )

    if total_added <= 0:
        await state.update_data(
            transfer_answer="no",
            transfer_from_other=False,
            transfer_source="",
        )

        await ask_terminal_step(
            message,
            state,
            "salary",
        )
        return

    await ask_terminal_step(
        message,
        state,
        "transfer_answer",
    )


@dp.message(
    TerminalReportForm.transfer_answer
)
async def transfer_answer_handler(
    message: Message,
    state: FSMContext,
):
    answer = (
        message.text or ""
    ).strip()

    if answer == (
        "✅ Да, с другого терминала"
    ):
        await state.update_data(
            transfer_answer="yes",
            transfer_from_other=True,
        )

        await ask_terminal_step(
            message,
            state,
            "transfer_source",
        )
        return

    if answer == (
        "❌ Нет, внешнее пополнение"
    ):
        await state.update_data(
            transfer_answer="no",
            transfer_from_other=False,
            transfer_source="",
        )

        await ask_terminal_step(
            message,
            state,
            "salary",
        )
        return

    await message.answer(
        "Ответьте кнопкой ниже.",
        reply_markup=(
            transfer_answer_keyboard()
        ),
    )


@dp.message(
    TerminalReportForm.transfer_source
)
async def transfer_source_handler(
    message: Message,
    state: FSMContext,
):
    source_terminal = (
        message.text or ""
    ).strip()

    data = await state.get_data()

    current_terminal = str(
        data.get(
            "terminal",
            "",
        )
    ).strip()

    if source_terminal not in TERMINALS:
        await message.answer(
            "❌ Выберите терминал "
            "кнопкой ниже.",
            reply_markup=(
                terminal_choice_keyboard(
                    exclude_terminal=(
                        current_terminal
                    )
                )
            ),
        )
        return

    if (
        source_terminal.casefold()
        == current_terminal.casefold()
    ):
        await message.answer(
            "❌ Нельзя получить сдачу "
            "из того же терминала.",
            reply_markup=(
                terminal_choice_keyboard(
                    exclude_terminal=(
                        current_terminal
                    )
                )
            ),
        )
        return

    await state.update_data(
        transfer_from_other=True,
        transfer_source=source_terminal,
    )

    await ask_terminal_step(
        message,
        state,
        "salary",
    )


@dp.message(TerminalReportForm.salary)
async def salary_handler(
    message: Message,
    state: FSMContext,
):
    try:
        value = parse_money(
            message.text
        )
    except ValueError:
        await message.answer(
            "Введите сумму цифрами."
        )
        return

    await process_terminal_step(
        message,
        state,
        "salary",
        value,
    )


@dp.message(
    TerminalReportForm.additional
)
async def additional_handler(
    message: Message,
    state: FSMContext,
):
    value = (
        message.text or ""
    ).strip()

    if not value:
        value = "нет"

    await process_terminal_step(
        message,
        state,
        "additional",
        value,
    )


# ============================================================
# ПОДТВЕРЖДЕНИЕ ОТЧЕТА
# ============================================================

@dp.callback_query(
    F.data == "terminal_confirm_send"
)
async def confirm_report_handler(
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
        "salary",
        "additional",
    }

    if not required_fields.issubset(
        data.keys()
    ):
        await callback.answer(
            "Не все поля заполнены.",
            show_alert=True,
        )
        return

    calc = calculate_terminal_report(
        data
    )

    if calc["final_amount"] < 0:
        await callback.answer(
            "Сумма на руках не может "
            "быть отрицательной.",
            show_alert=True,
        )
        return

    if (
        calc["transfer_from_other"]
        and not calc["transfer_source"]
    ):
        await callback.answer(
            "Не указан терминал-источник.",
            show_alert=True,
        )
        return

    user_id = callback.from_user.id

    user_name = get_user_name(
        callback
    )

    try:
        report_id = save_terminal_report(
            calc=calc,
            user_id=user_id,
            user_name=user_name,
        )
    except Exception as error:
        await callback.message.answer(
            "❌ Не удалось сохранить отчет.\n\n"
            f"<code>{escape(str(error))}</code>"
        )

        await callback.answer()
        return

    report_text = (
        build_terminal_report_text(
            calc=calc,
            user_name=user_name,
            report_id=report_id,
        )
    )

    await send_to_target_chat(
        text=report_text,
        source_chat_id=(
            callback.message.chat.id
        ),
        reply_markup=(
            report_delete_keyboard(
                report_id
            )
        ),
    )

    await callback.message.answer(
        f"✅ Отчет №{report_id} "
        "отправлен в группу.",
        reply_markup=start_keyboard(),
    )

    await state.clear()
    await callback.answer()


@dp.callback_query(
    F.data == "terminal_restart"
)
async def restart_report_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    await state.clear()

    await callback.message.answer(
        "🔄 Заполняем отчет заново."
    )

    await ask_terminal_step(
        callback.message,
        state,
        "terminal",
    )

    await callback.answer()


@dp.callback_query(
    F.data == "terminal_confirm_cancel"
)
async def cancel_report_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    await state.clear()

    await callback.message.answer(
        "❌ Отчет отменен.",
        reply_markup=start_keyboard(),
    )

    await callback.answer()


# ============================================================
# ОБРАБОТЧИКИ АРЕНДЫ
# ============================================================

@dp.message(RentForm.terminal)
async def rent_terminal_handler(
    message: Message,
    state: FSMContext,
):
    value = (
        message.text or ""
    ).strip()

    if value not in TERMINALS:
        await message.answer(
            "❌ Выберите терминал "
            "кнопкой ниже.",
            reply_markup=(
                terminal_choice_keyboard()
            ),
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
        value = parse_money(
            message.text
        )
    except ValueError:
        await message.answer(
            "Введите сумму цифрами."
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
    value = (
        message.text or ""
    ).strip()

    if not value:
        await message.answer(
            "Введите период аренды."
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
    value = (
        message.text or ""
    ).strip()

    if not value:
        value = "нет"

    await process_rent_step(
        message,
        state,
        "comment",
        value,
    )


@dp.callback_query(
    F.data == "rent_confirm_send"
)
async def confirm_rent_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    data = await state.get_data()

    required_fields = set(
        RENT_STEPS
    )

    if not required_fields.issubset(
        data.keys()
    ):
        await callback.answer(
            "Не все поля заполнены.",
            show_alert=True,
        )
        return

    user_id = callback.from_user.id

    user_name = get_user_name(
        callback
    )

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
        source_chat_id=(
            callback.message.chat.id
        ),
        reply_markup=(
            rent_delete_keyboard(
                rent_id
            )
        ),
    )

    await callback.message.answer(
        f"✅ Аренда №{rent_id} "
        "отправлена в группу.",
        reply_markup=start_keyboard(),
    )

    await state.clear()
    await callback.answer()


@dp.callback_query(
    F.data == "rent_restart"
)
async def restart_rent_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    await state.clear()

    await callback.message.answer(
        "🔄 Заполняем аренду заново."
    )

    await ask_rent_step(
        callback.message,
        state,
        "terminal",
    )

    await callback.answer()


@dp.callback_query(
    F.data == "rent_confirm_cancel"
)
async def cancel_rent_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    await state.clear()

    await callback.message.answer(
        "❌ Запись аренды отменена.",
        reply_markup=start_keyboard(),
    )

    await callback.answer()


# ============================================================
# АДМИН-ПАНЕЛЬ
# ============================================================

@dp.callback_query(
    F.data == "admin_main_menu"
)
async def admin_main_menu_handler(
    callback: CallbackQuery,
):
    if not is_admin(
        callback.from_user.id
    ):
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


@dp.callback_query(
    F.data.startswith(
        "admin_reports_"
    )
)
async def admin_reports_handler(
    callback: CallbackQuery,
):
    if not is_admin(
        callback.from_user.id
    ):
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

    if action in {
        "today",
        "week",
        "month",
        "all",
    }:
        text = build_reports_summary(
            action
        )

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


@dp.callback_query(
    F.data == "admin_rent_menu"
)
async def admin_rent_menu_handler(
    callback: CallbackQuery,
):
    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        "🏠 <b>ОТЧЕТЫ ПО АРЕНДЕ</b>\n\n"
        "Выберите период:",
        reply_markup=(
            admin_rent_keyboard()
        ),
    )

    await callback.answer()


@dp.callback_query(
    F.data.startswith(
        "admin_rent_"
    )
)
async def admin_rent_handler(
    callback: CallbackQuery,
):
    if not is_admin(
        callback.from_user.id
    ):
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

    if action in {
        "today",
        "week",
        "month",
        "all",
    }:
        text = build_rent_summary(
            action
        )

    elif action == "last10":
        text = (
            build_last_rent_payments()
        )

    else:
        await callback.answer(
            "Неизвестное действие.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        text,
        reply_markup=(
            admin_rent_keyboard()
        ),
    )

    await callback.answer()


@dp.callback_query(
    F.data == "admin_terminals_all"
)
async def all_terminals_handler(
    callback: CallbackQuery,
):
    if not is_admin(
        callback.from_user.id
    ):
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
# УДАЛЕНИЕ ОТЧЕТА
# ============================================================

@dp.callback_query(
    F.data.startswith(
        "delete_report_"
    )
)
async def delete_report_handler(
    callback: CallbackQuery,
):
    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Удалять отчеты может "
            "только администратор.",
            show_alert=True,
        )
        return

    raw_id = callback.data.replace(
        "delete_report_",
        "",
        1,
    )

    if not raw_id.isdigit():
        await callback.answer(
            "Неверный номер.",
            show_alert=True,
        )
        return

    report_id = int(raw_id)

    conn = get_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            UPDATE reports
            SET deleted = 1
            WHERE id = ?
              AND deleted = 0
        """, (report_id,))

        changed = cur.rowcount

        cur.execute("""
            UPDATE terminal_transfers
            SET deleted = 1
            WHERE report_id = ?
        """, (report_id,))

        conn.commit()

    except Exception:
        conn.rollback()
        conn.close()
        raise

    conn.close()

    if changed == 0:
        await callback.answer(
            "Отчет уже удален "
            "или не найден.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        f"🗑 <b>Отчет №{report_id} удален.</b>\n\n"
        f"Связанный перевод также "
        f"удален из статистики.\n\n"
        f"👤 Удалил: "
        f"{escape(get_user_name(callback))}"
    )

    await callback.answer(
        "Отчет удален."
    )


# ============================================================
# УДАЛЕНИЕ АРЕНДЫ
# ============================================================

@dp.callback_query(
    F.data.startswith(
        "delete_rent_"
    )
)
async def delete_rent_handler(
    callback: CallbackQuery,
):
    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Удалять аренду может "
            "только администратор.",
            show_alert=True,
        )
        return

    raw_id = callback.data.replace(
        "delete_rent_",
        "",
        1,
    )

    if not raw_id.isdigit():
        await callback.answer(
            "Неверный номер.",
            show_alert=True,
        )
        return

    rent_id = int(raw_id)

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE rent_payments
        SET deleted = 1
        WHERE id = ?
          AND deleted = 0
    """, (rent_id,))

    changed = cur.rowcount

    conn.commit()
    conn.close()

    if changed == 0:
        await callback.answer(
            "Запись уже удалена "
            "или не найдена.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        f"🗑 <b>Аренда №{rent_id} удалена.</b>\n\n"
        f"👤 Удалил: "
        f"{escape(get_user_name(callback))}"
    )

    await callback.answer(
        "Аренда удалена."
    )


# ============================================================
# WEB-СЕРВЕР ДЛЯ RENDER
# ============================================================

async def health_check(request):
    return web.Response(
        text="Telegram bot is running"
    )


async def start_web_server():
    app = web.Application()

    app.router.add_get(
        "/",
        health_check,
    )

    app.router.add_get(
        "/health",
        health_check,
    )

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT,
    )

    await site.start()

    print(
        f"Web server started "
        f"on port {PORT}"
    )


# ============================================================
# ЗАПУСК
# ============================================================

async def main():
    init_db()

    await start_web_server()

    print(
        "Telegram bot started"
    )

    await dp.start_polling(
        bot,
        allowed_updates=(
            dp.resolve_used_update_types()
        ),
    )


if __name__ == "__main__":
    asyncio.run(main())
```
