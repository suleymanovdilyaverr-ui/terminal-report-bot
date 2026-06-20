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

BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", "10000"))
REPORT_CHAT_ID_RAW = os.getenv("REPORT_CHAT_ID", "").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()

DB_PATH = "reports.db"

TERMINALS = [
"20-й",
"Бирлога 1",
"Тысячник",
"Сидоровка",
]

if not BOT_TOKEN:
raise RuntimeError(
"BOT_TOKEN не найден в Environment Variables"
)

ADMIN_IDS = {
int(item.strip())
for item in ADMIN_IDS_RAW.split(",")
if item.strip().lstrip("-").isdigit()
}

REPORT_CHAT_ID = (
int(REPORT_CHAT_ID_RAW)
if REPORT_CHAT_ID_RAW.lstrip("-").isdigit()
else None
)

bot = Bot(
token=BOT_TOKEN,
default=DefaultBotProperties(
parse_mode=ParseMode.HTML
),
)

dp = Dispatcher()

class ReportForm(StatesGroup):
terminal = State()
total = State()

```
change_100_before = State()
change_100_added = State()

change_1000_before = State()
change_1000_added = State()

transfer_answer = State()
transfer_source = State()

salary = State()
additional = State()
```

class RentForm(StatesGroup):
terminal = State()
amount = State()
period = State()
comment = State()

def get_connection():
connection = sqlite3.connect(DB_PATH)
connection.row_factory = sqlite3.Row

```
return connection
```

def init_db():
connection = get_connection()
cursor = connection.cursor()

```
cursor.execute(
    """
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

        additional_text TEXT NOT NULL,
        additional_total INTEGER NOT NULL,

        withheld_total INTEGER NOT NULL,
        final_amount INTEGER NOT NULL,

        user_id INTEGER NOT NULL,
        user_name TEXT NOT NULL,

        created_at TEXT NOT NULL,
        deleted INTEGER NOT NULL DEFAULT 0
    )
    """
)

cursor.execute(
    """
    CREATE TABLE IF NOT EXISTS terminal_transfers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        report_id INTEGER NOT NULL,

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
    """
)

cursor.execute(
    """
    CREATE TABLE IF NOT EXISTS rent_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        terminal TEXT NOT NULL,
        amount INTEGER NOT NULL,

        rent_period TEXT NOT NULL,
        comment TEXT NOT NULL,

        user_id INTEGER NOT NULL,
        user_name TEXT NOT NULL,

        created_at TEXT NOT NULL,
        deleted INTEGER NOT NULL DEFAULT 0
    )
    """
)

connection.commit()
connection.close()
```

def now_db():
return datetime.now().strftime(
"%Y-%m-%d %H:%M:%S"
)

def now_display():
return datetime.now().strftime(
"%d.%m.%Y %H:%M"
)

def format_money(value):
return (
f"{round(value):,}".replace(",", " ")
+ " ₽"
)

def parse_money(text):
cleaned = re.sub(
r"[^\d]",
"",
text or "",
)

```
if not cleaned:
    raise ValueError(
        "Сумма не найдена"
    )

return int(cleaned)
```

def get_user_name(obj):
user = obj.from_user

```
return (
    user.full_name
    or user.username
    or str(user.id)
)
```

def is_admin(user_id):
return user_id in ADMIN_IDS

def parse_additional(text):
text = (text or "").strip()

```
if text.lower() in {
    "нет",
    "ничего",
    "no",
    "-",
    "0",
}:
    return 0, "• Нет"

rows = []
total = 0

for part in re.split(
    r"[,;\n]+",
    text,
):
    part = part.strip()

    if not part:
        continue

    match = re.search(
        r"(.+?)[\s:—-]+"
        r"([\d\s.,]+)\s*₽?$",
        part,
    )

    if match:
        name = (
            match.group(1)
            .strip()
            .capitalize()
        )

        amount = parse_money(
            match.group(2)
        )

        total += amount

        rows.append(
            f"• {escape(name)}: "
            f"{format_money(amount)}"
        )
    else:
        rows.append(
            f"• {escape(part)}"
        )

return (
    total,
    "\n".join(rows)
    if rows
    else "• Нет",
)
```

def main_keyboard():
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

def cancel_keyboard():
return ReplyKeyboardMarkup(
keyboard=[
[
KeyboardButton(
text="❌ Отменить полностью"
)
]
],
resize_keyboard=True,
)

def terminals_keyboard(
exclude_terminal=None,
):
rows = []

```
for terminal in TERMINALS:
    if (
        exclude_terminal
        and terminal.casefold()
        == exclude_terminal.casefold()
    ):
        continue

    rows.append(
        [
            KeyboardButton(
                text=terminal
            )
        ]
    )

rows.append(
    [
        KeyboardButton(
            text="❌ Отменить полностью"
        )
    ]
)

return ReplyKeyboardMarkup(
    keyboard=rows,
    resize_keyboard=True,
    one_time_keyboard=True,
)
```

def transfer_keyboard():
return ReplyKeyboardMarkup(
keyboard=[
[
KeyboardButton(
text=(
"✅ Да, с другого "
"терминала"
)
)
],
[
KeyboardButton(
text=(
"❌ Нет, внешнее "
"пополнение"
)
)
],
[
KeyboardButton(
text="❌ Отменить полностью"
)
],
],
resize_keyboard=True,
)

def confirm_keyboard(prefix):
return InlineKeyboardMarkup(
inline_keyboard=[
[
InlineKeyboardButton(
text="✅ Отправить",
callback_data=(
f"{prefix}_send"
),
)
],
[
InlineKeyboardButton(
text="🔄 Заполнить заново",
callback_data=(
f"{prefix}_restart"
),
)
],
[
InlineKeyboardButton(
text="❌ Отменить",
callback_data=(
f"{prefix}_cancel"
),
)
],
]
)

def admin_keyboard():
return InlineKeyboardMarkup(
inline_keyboard=[
[
InlineKeyboardButton(
text="📅 Сегодня",
callback_data="admin_today",
),
InlineKeyboardButton(
text="📆 Неделя",
callback_data="admin_week",
),
],
[
InlineKeyboardButton(
text="🗓 Месяц",
callback_data="admin_month",
),
InlineKeyboardButton(
text="📚 Всё время",
callback_data="admin_all",
),
],
[
InlineKeyboardButton(
text="📋 Последние 10",
callback_data="admin_last",
)
],
[
InlineKeyboardButton(
text="🏧 Все терминалы",
callback_data=(
"admin_terminals"
),
)
],
[
InlineKeyboardButton(
text="🏠 Аренда",
callback_data="admin_rent",
)
],
]
)

def rent_admin_keyboard():
return InlineKeyboardMarkup(
inline_keyboard=[
[
InlineKeyboardButton(
text="📅 Сегодня",
callback_data="rent_today",
),
InlineKeyboardButton(
text="📆 Неделя",
callback_data="rent_week",
),
],
[
InlineKeyboardButton(
text="🗓 Месяц",
callback_data="rent_month",
),
InlineKeyboardButton(
text="📚 Всё время",
callback_data="rent_all",
),
],
[
InlineKeyboardButton(
text="📋 Последние 10",
callback_data="rent_last",
)
],
[
InlineKeyboardButton(
text="⬅️ Назад",
callback_data="admin_back",
)
],
]
)

def delete_report_keyboard(
report_id,
):
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

def delete_rent_keyboard(
rent_id,
):
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

async def send_to_target(
text,
source_chat_id,
reply_markup=None,
):
target_chat_id = (
REPORT_CHAT_ID
or source_chat_id
)

```
await bot.send_message(
    chat_id=target_chat_id,
    text=text,
    reply_markup=reply_markup,
)
```

def calculate_report(data):
(
additional_total,
additional_text,
) = parse_additional(
data.get(
"additional",
"нет",
)
)

```
change_100_after = (
    data["change_100_before"]
    + data["change_100_added"]
)

change_1000_after = (
    data["change_1000_before"]
    + data["change_1000_added"]
)

withheld_total = (
    data["change_100_added"]
    + data["change_1000_added"]
    + data["salary"]
    + additional_total
)

final_amount = (
    data["total"]
    - withheld_total
)

transfer = bool(
    data.get(
        "transfer",
        False,
    )
)

transfer_total = 0

if transfer:
    transfer_total = (
        data["change_100_added"]
        + data["change_1000_added"]
    )

return {
    **data,

    "change_100_after":
        change_100_after,

    "change_1000_after":
        change_1000_after,

    "additional_total":
        additional_total,

    "additional_text":
        additional_text,

    "withheld_total":
        withheld_total,

    "final_amount":
        final_amount,

    "transfer_total":
        transfer_total,
}
```

def build_report_text(
calculation,
user_name,
report_id=None,
created_at=None,
):
if created_at is None:
created_at = now_display()

```
number_text = ""

if report_id is not None:
    number_text = (
        f"📄 <b>Отчет №{report_id}</b>"
        "\n\n"
    )

transfer_text = ""

if (
    calculation.get("transfer")
    and calculation.get("source")
    and calculation["transfer_total"] > 0
):
    transfer_text = (
        "\n🔄 <b>ПЕРЕВОД СДАЧИ</b>\n"

        f"📤 Откуда: "
        f"<b>{escape(calculation['source'])}</b>\n"

        f"📥 Куда: "
        f"<b>{escape(calculation['terminal'])}</b>\n"

        f"• По 100 ₽: "
        f"{format_money(calculation['change_100_added'])}\n"

        f"• По 1000 ₽: "
        f"{format_money(calculation['change_1000_added'])}\n"

        f"• Всего: "
        f"<b>{format_money(calculation['transfer_total'])}</b>\n"
    )

return f"""
```

{number_text}📊 <b>ОТЧЕТ ПО ТЕРМИНАЛУ</b>

🏧 <b>Терминал:</b>
{escape(calculation["terminal"])}

💰 <b>Общая сумма:</b>
{format_money(calculation["total"])}

💵 <b>Сдача по 100 ₽</b>
Было: {format_money(calculation["change_100_before"])}
Добавлено: {format_money(calculation["change_100_added"])}
Стало: {format_money(calculation["change_100_after"])}

💸 <b>Сдача по 1000 ₽</b>
Было: {format_money(calculation["change_1000_before"])}
Добавлено: {format_money(calculation["change_1000_added"])}
Стало: {format_money(calculation["change_1000_after"])}

{transfer_text}
👤 <b>ЗП себе:</b>
{format_money(calculation["salary"])}

📝 <b>Дополнительно:</b>
{calculation["additional_text"]}

━━━━━━━━━━━━━━

📉 <b>Удержано:</b>

• Сдача 100 ₽:
{format_money(calculation["change_100_added"])}

• Сдача 1000 ₽:
{format_money(calculation["change_1000_added"])}

• ЗП:
{format_money(calculation["salary"])}

• Дополнительные расходы:
{format_money(calculation["additional_total"])}

💵 <b>НА РУКАХ:</b> <b>{format_money(calculation["final_amount"])}</b>

👤 <b>Отчет отправил:</b>
{escape(user_name)}

🕒 {created_at}
""".strip()

def save_report(
calculation,
user_id,
user_name,
):
connection = get_connection()
cursor = connection.cursor()

```
created_at = now_db()

try:
    cursor.execute(
        """
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
        """,
        (
            calculation["terminal"],
            calculation["total"],

            calculation[
                "change_100_before"
            ],
            calculation[
                "change_100_added"
            ],
            calculation[
                "change_100_after"
            ],

            calculation[
                "change_1000_before"
            ],
            calculation[
                "change_1000_added"
            ],
            calculation[
                "change_1000_after"
            ],

            calculation["salary"],

            calculation[
                "additional_text"
            ],
            calculation[
                "additional_total"
            ],

            calculation[
                "withheld_total"
            ],
            calculation[
                "final_amount"
            ],

            user_id,
            user_name,

            created_at,
        ),
    )

    report_id = int(
        cursor.lastrowid
    )

    if (
        calculation.get("transfer")
        and calculation.get("source")
        and calculation["transfer_total"] > 0
    ):
        cursor.execute(
            """
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
            """,
            (
                report_id,

                calculation["source"],
                calculation["terminal"],

                calculation[
                    "change_100_added"
                ],
                calculation[
                    "change_1000_added"
                ],
                calculation[
                    "transfer_total"
                ],

                user_id,
                user_name,

                created_at,
            ),
        )

    connection.commit()

    return report_id

except Exception:
    connection.rollback()
    raise

finally:
    connection.close()
```

def get_period_start(period):
now = datetime.now()

```
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
```

def get_period_title(period):
titles = {
"today": "ЗА СЕГОДНЯ",
"week": "ЗА НЕДЕЛЮ",
"month": "ЗА МЕСЯЦ",
"all": "ЗА ВСЁ ВРЕМЯ",
}

```
return titles[period]
```

def get_transfer_totals(
cursor,
terminal,
start=None,
):
date_sql = ""

```
outgoing_parameters = [
    terminal
]

incoming_parameters = [
    terminal
]

if start is not None:
    date_sql = (
        " AND created_at >= ?"
    )

    start_text = start.strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    outgoing_parameters.append(
        start_text
    )

    incoming_parameters.append(
        start_text
    )

cursor.execute(
    f"""
    SELECT COALESCE(
        SUM(total_amount),
        0
    )
    FROM terminal_transfers
    WHERE source_terminal = ?
      AND deleted = 0
      {date_sql}
    """,
    tuple(outgoing_parameters),
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
      {date_sql}
    """,
    tuple(incoming_parameters),
)

incoming = int(
    cursor.fetchone()[0]
)

return incoming, outgoing
```

def build_reports_summary(period):
start = get_period_start(
period
)

```
connection = get_connection()
cursor = connection.cursor()

where_sql = (
    "WHERE deleted = 0"
)

parameters = []

if start is not None:
    where_sql += (
        " AND created_at >= ?"
    )

    parameters.append(
        start.strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

cursor.execute(
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
    {where_sql}
    """,
    tuple(parameters),
)

report_row = cursor.fetchone()

report_count = int(
    report_row[0]
)

total_amount = int(
    report_row[1]
)

withheld_total = int(
    report_row[2]
)

final_amount = int(
    report_row[3]
)

terminal_blocks = []
total_with_transfers = 0

for terminal in TERMINALS:
    terminal_where = (
        "WHERE terminal = ? "
        "AND deleted = 0"
    )

    terminal_parameters = [
        terminal
    ]

    if start is not None:
        terminal_where += (
            " AND created_at >= ?"
        )

        terminal_parameters.append(
            start.strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )

    cursor.execute(
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
        tuple(terminal_parameters),
    )

    terminal_row = (
        cursor.fetchone()
    )

    terminal_report_count = int(
        terminal_row[0]
    )

    terminal_final = int(
        terminal_row[1]
    )

    (
        incoming,
        outgoing,
    ) = get_transfer_totals(
        cursor,
        terminal,
        start,
    )

    terminal_balance = (
        terminal_final
        + incoming
        - outgoing
    )

    total_with_transfers += (
        terminal_balance
    )

    terminal_blocks.append(
        f"🏧 <b>{escape(terminal)}</b>\n"
        f"• Отчетов: "
        f"{terminal_report_count}\n"
        f"• По отчетам: "
        f"{format_money(terminal_final)}\n"
        f"• Получено: "
        f"{format_money(incoming)}\n"
        f"• Передано: "
        f"{format_money(outgoing)}\n"
        f"• Баланс: "
        f"<b>{format_money(terminal_balance)}</b>"
    )

connection.close()

terminal_text = (
    "\n\n".join(
        terminal_blocks
    )
)

return f"""
```

📊 <b>ОТЧЕТЫ {get_period_title(period)}</b>

📌 Количество отчетов: <b>{report_count}</b>

💰 Общая сумма: <b>{format_money(total_amount)}</b>

📉 Всего удержано: <b>{format_money(withheld_total)}</b>

💵 На руках по отчетам: <b>{format_money(final_amount)}</b>

🔄 Итог с переводами: <b>{format_money(total_with_transfers)}</b>

━━━━━━━━━━━━━━

{terminal_text}
""".strip()

def build_last_reports():
connection = get_connection()

```
rows = connection.execute(
    """
    SELECT
        id,
        terminal,
        final_amount,
        user_name,
        created_at
    FROM reports
    WHERE deleted = 0
    ORDER BY id DESC
    LIMIT 10
    """
).fetchall()

connection.close()

if not rows:
    return (
        "📋 Отчетов пока нет."
    )

text = (
    "📋 <b>ПОСЛЕДНИЕ ОТЧЕТЫ</b>"
    "\n\n"
)

for row in rows:
    text += (
        f"📄 №{row['id']}\n"

        f"🏧 "
        f"{escape(row['terminal'])}\n"

        f"💵 На руках: "
        f"<b>{format_money(row['final_amount'])}</b>\n"

        f"👤 "
        f"{escape(row['user_name'])}\n"

        f"🕒 "
        f"{row['created_at']}\n\n"
    )

return text.strip()
```

def build_terminals_summary():
connection = get_connection()
cursor = connection.cursor()

```
text = (
    "🏧 <b>ТЕРМИНАЛЫ "
    "ЗА ВСЁ ВРЕМЯ</b>\n\n"
)

for terminal in TERMINALS:
    cursor.execute(
        """
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
        """,
        (terminal,),
    )

    report_row = cursor.fetchone()

    report_count = int(
        report_row[0]
    )

    total_amount = int(
        report_row[1]
    )

    withheld_total = int(
        report_row[2]
    )

    final_amount = int(
        report_row[3]
    )

    (
        incoming,
        outgoing,
    ) = get_transfer_totals(
        cursor,
        terminal,
    )

    balance = (
        final_amount
        + incoming
        - outgoing
    )

    cursor.execute(
        """
        SELECT
            COUNT(id),
            COALESCE(
                SUM(amount),
                0
            )
        FROM rent_payments
        WHERE terminal = ?
          AND deleted = 0
        """,
        (terminal,),
    )

    rent_row = cursor.fetchone()

    rent_count = int(
        rent_row[0]
    )

    rent_total = int(
        rent_row[1]
    )

    text += (
        f"🏧 <b>{escape(terminal)}</b>\n"

        f"📊 Отчетов: "
        f"{report_count}\n"

        f"💰 Общая сумма: "
        f"{format_money(total_amount)}\n"

        f"📉 Удержано: "
        f"{format_money(withheld_total)}\n"

        f"💵 По отчетам: "
        f"{format_money(final_amount)}\n"

        f"📥 Получено: "
        f"{format_money(incoming)}\n"

        f"📤 Передано: "
        f"{format_money(outgoing)}\n"

        f"✅ Баланс: "
        f"<b>{format_money(balance)}</b>\n"

        f"🏠 Аренда: "
        f"{rent_count} оплат, "
        f"{format_money(rent_total)}\n\n"
    )

connection.close()

return text.strip()
```

def build_rent_summary(period):
start = get_period_start(
period
)

```
connection = get_connection()

where_sql = (
    "WHERE deleted = 0"
)

parameters = []

if start is not None:
    where_sql += (
        " AND created_at >= ?"
    )

    parameters.append(
        start.strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

rows = connection.execute(
    f"""
    SELECT
        id,
        terminal,
        amount,
        rent_period,
        user_name,
        created_at
    FROM rent_payments
    {where_sql}
    ORDER BY id DESC
    LIMIT 30
    """,
    tuple(parameters),
).fetchall()

summary_row = connection.execute(
    f"""
    SELECT
        COUNT(id),
        COALESCE(
            SUM(amount),
            0
        )
    FROM rent_payments
    {where_sql}
    """,
    tuple(parameters),
).fetchone()

connection.close()

rent_count = int(
    summary_row[0]
)

rent_total = int(
    summary_row[1]
)

details = ""

for row in rows:
    details += (
        f"📄 №{row['id']} | "
        f"🏧 {escape(row['terminal'])}\n"

        f"💰 "
        f"{format_money(row['amount'])}\n"

        f"📅 "
        f"{escape(row['rent_period'])}\n"

        f"👤 "
        f"{escape(row['user_name'])}\n"

        f"🕒 "
        f"{row['created_at']}\n\n"
    )

if not details:
    details = "• Нет данных"

return f"""
```

🏠 <b>АРЕНДА {get_period_title(period)}</b>

📌 Количество оплат: <b>{rent_count}</b>

💰 Всего отдали: <b>{format_money(rent_total)}</b>

━━━━━━━━━━━━━━

{details}
""".strip()

@dp.message(CommandStart())
async def start_handler(
message: Message,
state: FSMContext,
):
await state.clear()

```
await message.answer(
    "✅ Бот работает.\n\n"
    "Выберите действие:",
    reply_markup=main_keyboard(),
)
```

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

@dp.message(Command("admin"))
@dp.message(
F.text == "👨‍💼 Админ панель"
)
async def admin_handler(
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

```
await message.answer(
    "👨‍💼 <b>АДМИН-ПАНЕЛЬ</b>",
    reply_markup=admin_keyboard(),
)
```

@dp.message(Command("cancel"))
@dp.message(
F.text == "❌ Отменить полностью"
)
async def cancel_handler(
message: Message,
state: FSMContext,
):
await state.clear()

```
await message.answer(
    "❌ Заполнение отменено.",
    reply_markup=main_keyboard(),
)
```

@dp.message(
F.text == "📊 Новый отчет"
)
async def new_report_handler(
message: Message,
state: FSMContext,
):
await state.clear()

```
await state.set_state(
    ReportForm.terminal
)

await message.answer(
    "🏧 <b>Выберите терминал:</b>",
    reply_markup=terminals_keyboard(),
)
```

@dp.message(ReportForm.terminal)
async def report_terminal_handler(
message: Message,
state: FSMContext,
):
terminal = (
message.text or ""
).strip()

```
if terminal not in TERMINALS:
    await message.answer(
        "❌ Выберите терминал кнопкой.",
        reply_markup=terminals_keyboard(),
    )
    return

await state.update_data(
    terminal=terminal
)

await state.set_state(
    ReportForm.total
)

await message.answer(
    "💰 Введите общую сумму:",
    reply_markup=cancel_keyboard(),
)
```

async def process_money_step(
message,
state,
key,
next_state,
question,
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

```
await state.update_data(
    **{key: value}
)

await state.set_state(
    next_state
)

await message.answer(
    question,
    reply_markup=cancel_keyboard(),
)
```

@dp.message(ReportForm.total)
async def report_total_handler(
message: Message,
state: FSMContext,
):
await process_money_step(
message=message,
state=state,
key="total",
next_state=(
ReportForm.change_100_before
),
question=(
"💵 Сколько сдачи "
"по 100 ₽ было?"
),
)

@dp.message(
ReportForm.change_100_before
)
async def change_100_before_handler(
message: Message,
state: FSMContext,
):
await process_money_step(
message=message,
state=state,
key="change_100_before",
next_state=(
ReportForm.change_100_added
),
question=(
"💵 Сколько добавили "
"сдачи по 100 ₽?\n\n"
"Если не добавляли — 0"
),
)

@dp.message(
ReportForm.change_100_added
)
async def change_100_added_handler(
message: Message,
state: FSMContext,
):
await process_money_step(
message=message,
state=state,
key="change_100_added",
next_state=(
ReportForm.change_1000_before
),
question=(
"💸 Сколько сдачи "
"по 1000 ₽ было?"
),
)

@dp.message(
ReportForm.change_1000_before
)
async def change_1000_before_handler(
message: Message,
state: FSMContext,
):
await process_money_step(
message=message,
state=state,
key="change_1000_before",
next_state=(
ReportForm.change_1000_added
),
question=(
"💸 Сколько добавили "
"сдачи по 1000 ₽?\n\n"
"Если не добавляли — 0"
),
)

@dp.message(
ReportForm.change_1000_added
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

```
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
        transfer=False,
        source="",
    )

    await state.set_state(
        ReportForm.salary
    )

    await message.answer(
        "👤 Введите сумму ЗП себе.\n\n"
        "Если не брали — 0",
        reply_markup=cancel_keyboard(),
    )
    return

await state.set_state(
    ReportForm.transfer_answer
)

await message.answer(
    "🔄 Добавленную сдачу взяли "
    "с другого терминала?",
    reply_markup=transfer_keyboard(),
)
```

@dp.message(
ReportForm.transfer_answer
)
async def transfer_answer_handler(
message: Message,
state: FSMContext,
):
answer = (
message.text or ""
).strip()

```
if answer == (
    "✅ Да, с другого терминала"
):
    data = await state.get_data()

    await state.update_data(
        transfer=True
    )

    await state.set_state(
        ReportForm.transfer_source
    )

    await message.answer(
        "📤 Выберите терминал, "
        "откуда взяли сдачу:",
        reply_markup=terminals_keyboard(
            exclude_terminal=(
                data["terminal"]
            )
        ),
    )
    return

if answer == (
    "❌ Нет, внешнее пополнение"
):
    await state.update_data(
        transfer=False,
        source="",
    )

    await state.set_state(
        ReportForm.salary
    )

    await message.answer(
        "👤 Введите сумму ЗП себе.\n\n"
        "Если не брали — 0",
        reply_markup=cancel_keyboard(),
    )
    return

await message.answer(
    "Ответьте кнопкой ниже.",
    reply_markup=transfer_keyboard(),
)
```

@dp.message(
ReportForm.transfer_source
)
async def transfer_source_handler(
message: Message,
state: FSMContext,
):
source_terminal = (
message.text or ""
).strip()

```
data = await state.get_data()

current_terminal = data[
    "terminal"
]

if (
    source_terminal not in TERMINALS
    or source_terminal
    == current_terminal
):
    await message.answer(
        "❌ Выберите другой "
        "терминал кнопкой.",
        reply_markup=terminals_keyboard(
            exclude_terminal=(
                current_terminal
            )
        ),
    )
    return

await state.update_data(
    source=source_terminal
)

await state.set_state(
    ReportForm.salary
)

await message.answer(
    "👤 Введите сумму ЗП себе.\n\n"
    "Если не брали — 0",
    reply_markup=cancel_keyboard(),
)
```

@dp.message(ReportForm.salary)
async def salary_handler(
message: Message,
state: FSMContext,
):
await process_money_step(
message=message,
state=state,
key="salary",
next_state=(
ReportForm.additional
),
question=(
"📝 Введите дополнительные "
"расходы или напишите: нет"
),
)

@dp.message(ReportForm.additional)
async def additional_handler(
message: Message,
state: FSMContext,
):
additional = (
message.text or "нет"
).strip()

```
if not additional:
    additional = "нет"

await state.update_data(
    additional=additional
)

data = await state.get_data()

calculation = calculate_report(
    data
)

if calculation[
    "final_amount"
] < 0:
    await message.answer(
        "❌ Удержания больше общей суммы.\n\n"
        "Нажмите «Заполнить заново».",
        reply_markup=confirm_keyboard(
            "report"
        ),
    )
    return

await message.answer(
    "📋 <b>ПРОВЕРЬТЕ ОТЧЕТ</b>\n\n"
    + build_report_text(
        calculation=calculation,
        user_name=get_user_name(
            message
        ),
    ),
    reply_markup=confirm_keyboard(
        "report"
    ),
)
```

@dp.callback_query(
F.data == "report_send"
)
async def report_send_handler(
callback: CallbackQuery,
state: FSMContext,
):
data = await state.get_data()

```
required_fields = {
    "terminal",
    "total",
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

calculation = calculate_report(
    data
)

if calculation[
    "final_amount"
] < 0:
    await callback.answer(
        "Сумма на руках "
        "не может быть отрицательной.",
        show_alert=True,
    )
    return

report_id = save_report(
    calculation=calculation,
    user_id=callback.from_user.id,
    user_name=get_user_name(
        callback
    ),
)

await send_to_target(
    text=build_report_text(
        calculation=calculation,
        user_name=get_user_name(
            callback
        ),
        report_id=report_id,
    ),
    source_chat_id=(
        callback.message.chat.id
    ),
    reply_markup=(
        delete_report_keyboard(
            report_id
        )
    ),
)

await state.clear()

await callback.message.answer(
    f"✅ Отчет №{report_id} "
    "отправлен.",
    reply_markup=main_keyboard(),
)

await callback.answer()
```

@dp.callback_query(
F.data == "report_restart"
)
async def report_restart_handler(
callback: CallbackQuery,
state: FSMContext,
):
await state.clear()

```
await state.set_state(
    ReportForm.terminal
)

await callback.message.answer(
    "🏧 Выберите терминал:",
    reply_markup=terminals_keyboard(),
)

await callback.answer()
```

@dp.callback_query(
F.data == "report_cancel"
)
async def report_cancel_handler(
callback: CallbackQuery,
state: FSMContext,
):
await state.clear()

```
await callback.message.answer(
    "❌ Отчет отменен.",
    reply_markup=main_keyboard(),
)

await callback.answer()
```

@dp.message(
F.text == "🏠 Оплата аренды"
)
async def new_rent_handler(
message: Message,
state: FSMContext,
):
await state.clear()

```
await state.set_state(
    RentForm.terminal
)

await message.answer(
    "🏧 Выберите терминал:",
    reply_markup=terminals_keyboard(),
)
```

@dp.message(RentForm.terminal)
async def rent_terminal_handler(
message: Message,
state: FSMContext,
):
terminal = (
message.text or ""
).strip()

```
if terminal not in TERMINALS:
    await message.answer(
        "❌ Выберите терминал кнопкой.",
        reply_markup=terminals_keyboard(),
    )
    return

await state.update_data(
    terminal=terminal
)

await state.set_state(
    RentForm.amount
)

await message.answer(
    "💰 Введите сумму аренды:",
    reply_markup=cancel_keyboard(),
)
```

@dp.message(RentForm.amount)
async def rent_amount_handler(
message: Message,
state: FSMContext,
):
try:
amount = parse_money(
message.text
)
except ValueError:
await message.answer(
"Введите сумму цифрами."
)
return

```
await state.update_data(
    amount=amount
)

await state.set_state(
    RentForm.period
)

await message.answer(
    "📅 За какой период "
    "оплачена аренда?"
)
```

@dp.message(RentForm.period)
async def rent_period_handler(
message: Message,
state: FSMContext,
):
period = (
message.text or ""
).strip()

```
if not period:
    await message.answer(
        "Введите период аренды."
    )
    return

await state.update_data(
    period=period
)

await state.set_state(
    RentForm.comment
)

await message.answer(
    "📝 Введите комментарий "
    "или напишите: нет"
)
```

@dp.message(RentForm.comment)
async def rent_comment_handler(
message: Message,
state: FSMContext,
):
comment = (
message.text or "нет"
).strip()

```
if not comment:
    comment = "нет"

await state.update_data(
    comment=comment
)

data = await state.get_data()

text = f"""
```

🏠 <b>ОПЛАТА АРЕНДЫ</b>

🏧 Терминал: <b>{escape(data["terminal"])}</b>

💰 Сумма: <b>{format_money(data["amount"])}</b>

📅 Период:
{escape(data["period"])}

📝 Комментарий:
{escape(data["comment"])}

👤 Добавил:
{escape(get_user_name(message))}

🕒 {now_display()}
""".strip()

```
await message.answer(
    "📋 <b>ПРОВЕРЬТЕ АРЕНДУ</b>\n\n"
    + text,
    reply_markup=confirm_keyboard(
        "rent"
    ),
)
```

@dp.callback_query(
F.data == "rent_send"
)
async def rent_send_handler(
callback: CallbackQuery,
state: FSMContext,
):
data = await state.get_data()

```
required_fields = {
    "terminal",
    "amount",
    "period",
    "comment",
}

if not required_fields.issubset(
    data.keys()
):
    await callback.answer(
        "Не все поля заполнены.",
        show_alert=True,
    )
    return

connection = get_connection()
cursor = connection.cursor()

cursor.execute(
    """
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
    VALUES (
        ?, ?, ?, ?, ?, ?, ?, 0
    )
    """,
    (
        data["terminal"],
        data["amount"],
        data["period"],
        data["comment"],

        callback.from_user.id,
        get_user_name(callback),

        now_db(),
    ),
)

rent_id = int(
    cursor.lastrowid
)

connection.commit()
connection.close()

text = f"""
```

📄 <b>Аренда №{rent_id}</b>

🏠 <b>ОПЛАТА АРЕНДЫ</b>

🏧 Терминал: <b>{escape(data["terminal"])}</b>

💰 Сумма: <b>{format_money(data["amount"])}</b>

📅 Период:
{escape(data["period"])}

📝 Комментарий:
{escape(data["comment"])}

👤 Добавил:
{escape(get_user_name(callback))}

🕒 {now_display()}
""".strip()

```
await send_to_target(
    text=text,
    source_chat_id=(
        callback.message.chat.id
    ),
    reply_markup=(
        delete_rent_keyboard(
            rent_id
        )
    ),
)

await state.clear()

await callback.message.answer(
    f"✅ Аренда №{rent_id} "
    "отправлена.",
    reply_markup=main_keyboard(),
)

await callback.answer()
```

@dp.callback_query(
F.data == "rent_restart"
)
async def rent_restart_handler(
callback: CallbackQuery,
state: FSMContext,
):
await state.clear()

```
await state.set_state(
    RentForm.terminal
)

await callback.message.answer(
    "🏧 Выберите терминал:",
    reply_markup=terminals_keyboard(),
)

await callback.answer()
```

@dp.callback_query(
F.data == "rent_cancel"
)
async def rent_cancel_handler(
callback: CallbackQuery,
state: FSMContext,
):
await state.clear()

```
await callback.message.answer(
    "❌ Аренда отменена.",
    reply_markup=main_keyboard(),
)

await callback.answer()
```

@dp.callback_query(
F.data.startswith("admin_")
)
async def admin_callback_handler(
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

```
action = callback.data.replace(
    "admin_",
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

    await callback.message.edit_text(
        text,
        reply_markup=admin_keyboard(),
    )

elif action == "last":
    await callback.message.edit_text(
        build_last_reports(),
        reply_markup=admin_keyboard(),
    )

elif action == "terminals":
    await callback.message.edit_text(
        build_terminals_summary(),
        reply_markup=admin_keyboard(),
    )

elif action == "rent":
    await callback.message.edit_text(
        "🏠 <b>ОТЧЕТЫ ПО АРЕНДЕ</b>",
        reply_markup=(
            rent_admin_keyboard()
        ),
    )

elif action == "back":
    await callback.message.edit_text(
        "👨‍💼 <b>АДМИН-ПАНЕЛЬ</b>",
        reply_markup=admin_keyboard(),
    )

await callback.answer()
```

@dp.callback_query(
F.data.startswith("rent_")
)
async def rent_admin_callback_handler(
callback: CallbackQuery,
):
if callback.data in {
"rent_send",
"rent_restart",
"rent_cancel",
}:
return

```
if not is_admin(
    callback.from_user.id
):
    await callback.answer(
        "Нет доступа.",
        show_alert=True,
    )
    return

action = callback.data.replace(
    "rent_",
    "",
    1,
)

if action in {
    "today",
    "week",
    "month",
    "all",
}:
    text = build_rent_summary(
        action
    )

elif action == "last":
    text = build_rent_summary(
        "all"
    )

else:
    return

await callback.message.edit_text(
    text,
    reply_markup=rent_admin_keyboard(),
)

await callback.answer()
```

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
"Удалять может только "
"администратор.",
show_alert=True,
)
return

```
report_id_text = (
    callback.data.replace(
        "delete_report_",
        "",
        1,
    )
)

if not report_id_text.isdigit():
    await callback.answer(
        "Неверный номер отчета.",
        show_alert=True,
    )
    return

report_id = int(
    report_id_text
)

connection = get_connection()
cursor = connection.cursor()

cursor.execute(
    """
    UPDATE reports
    SET deleted = 1
    WHERE id = ?
      AND deleted = 0
    """,
    (report_id,),
)

changed = cursor.rowcount

cursor.execute(
    """
    UPDATE terminal_transfers
    SET deleted = 1
    WHERE report_id = ?
    """,
    (report_id,),
)

connection.commit()
connection.close()

if not changed:
    await callback.answer(
        "Отчет не найден "
        "или уже удален.",
        show_alert=True,
    )
    return

await callback.message.edit_text(
    f"🗑 <b>Отчет №{report_id} удален.</b>\n\n"
    f"👤 Удалил: "
    f"{escape(get_user_name(callback))}"
)

await callback.answer(
    "Удалено"
)
```

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
"Удалять может только "
"администратор.",
show_alert=True,
)
return

```
rent_id_text = (
    callback.data.replace(
        "delete_rent_",
        "",
        1,
    )
)

if not rent_id_text.isdigit():
    await callback.answer(
        "Неверный номер записи.",
        show_alert=True,
    )
    return

rent_id = int(
    rent_id_text
)

connection = get_connection()
cursor = connection.cursor()

cursor.execute(
    """
    UPDATE rent_payments
    SET deleted = 1
    WHERE id = ?
      AND deleted = 0
    """,
    (rent_id,),
)

changed = cursor.rowcount

connection.commit()
connection.close()

if not changed:
    await callback.answer(
        "Запись не найдена "
        "или уже удалена.",
        show_alert=True,
    )
    return

await callback.message.edit_text(
    f"🗑 <b>Аренда №{rent_id} удалена.</b>\n\n"
    f"👤 Удалил: "
    f"{escape(get_user_name(callback))}"
)

await callback.answer(
    "Удалено"
)
```

async def health_check(request):
return web.Response(
text="Telegram bot is running"
)

async def start_web_server():
application = web.Application()

```
application.router.add_get(
    "/",
    health_check,
)

application.router.add_get(
    "/health",
    health_check,
)

runner = web.AppRunner(
    application
)

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
```

async def main():
init_db()

```
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
```

if **name** == "**main**":
asyncio.run(main())
