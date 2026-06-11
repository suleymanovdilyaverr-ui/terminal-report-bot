import asyncio
import os

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message


BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer("✅ Бот работает. Теперь можно делать отчеты.")


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
    await start_web_server()
    print("telegram bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
