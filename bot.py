import asyncio
import logging
import os
from datetime import datetime
import asyncpg
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile,
)
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
MANAGER_URL = os.getenv("MANAGER_URL", "https://t.me/kindbulat")
DATABASE_URL = os.getenv("DATABASE_URL")
CHANNEL_ID = "@semuabuz"

logging.basicConfig(level=logging.INFO)
db_pool = None

async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    async with db_pool.acquire() as conn:
        # Бот сам создаст таблицу, если её нет!
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            age TEXT,
            rko_knowledge TEXT,
            desired_income TEXT,
            location TEXT,
            call_ready TEXT,
            joined_at TEXT
        )
        """)

class Quiz(StatesGroup):
    age = State()
    rko = State()
    income = State()
    location = State()
    call = State()

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

def get_kb(options):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=callback)] for text, callback in options])

@dp.message(Command("start"))
async def start(msg: Message, state: FSMContext):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE user_id=$1", msg.from_user.id)
    if row:
        await msg.answer("Ты уже проходил опрос. Пиши менеджеру: " + MANAGER_URL)
        return
    await msg.answer("1/5. Сколько тебе лет?")
    await state.set_state(Quiz.age)

@dp.message(Quiz.age)
async def q_age(msg: Message, state: FSMContext):
    await state.update_data(age=msg.text)
    await msg.answer("2/5. Насколько ты знаешь РКО? (0-10)")
    await state.set_state(Quiz.rko)

@dp.message(Quiz.rko)
async def q_rko(msg: Message, state: FSMContext):
    await state.update_data(rko=msg.text)
    await msg.answer("3/5. Какой доход хочешь?")
    await state.set_state(Quiz.income)

@dp.message(Quiz.income)
async def q_income(msg: Message, state: FSMContext):
    await state.update_data(income=msg.text)
    await msg.answer("4/5. Город?")
    await state.set_state(Quiz.location)

@dp.message(Quiz.location)
async def q_loc(msg: Message, state: FSMContext):
    await state.update_data(location=msg.text)
    await msg.answer("5/5. Готов на созвон?", reply_markup=get_kb([("Да", "yes"), ("Нет", "no")]))
    await state.set_state(Quiz.call)

@dp.callback_query(Quiz.call)
async def q_call(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(call=cb.data)

    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id, username, age, rko_knowledge, desired_income, location, call_ready, joined_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
            cb.from_user.id, cb.from_user.username, data.get("age"), data.get("rko"), data.get("income"), data.get("location"), cb.data, datetime.now().isoformat()
        )

    admin_text = f"🔥 **Новая анкета!**\n\n👤 @{cb.from_user.username or 'нет'}\n🆔 ID: {cb.from_user.id}\n🎂 Возраст: {data.get('age')}\n📊 РКО: {data.get('rko')}/10\n💰 Доход: {data.get('income')}\n📍 Город: {data.get('location')}\n📞 Созвон: {cb.data}"
    for admin_id in ADMIN_IDS:
        try: await bot.send_message(admin_id, admin_text, parse_mode="Markdown")
        except: pass

    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=cb.from_user.id)
        if member.status in ['left', 'kicked']: raise Exception("Not subscribed")
    except Exception:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Подписаться на канал", url=f"https://t.me/{CHANNEL_ID.replace('@', '')}")],
            [InlineKeyboardButton(text="✅ Я подписался, дай PDF", callback_data="check")]
        ])
        await cb.message.edit_text("Спасибо за ответы! 🎁\n\nТвой гайд готов. Но чтобы забрать его, подпишись на наш канал 👇", reply_markup=kb)
        return

    await send_pdf(cb.message, data)
    await cb.answer()

@dp.callback_query(F.data == "check")
async def check_sub(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=cb.from_user.id)
        if member.status in ['left', 'kicked']: raise Exception("Not subscribed")
        await cb.message.edit_text("✅ Подписка подтверждена! Забирай:")
        await send_pdf(cb.message, data)
        await state.clear()
    except Exception:
        await cb.answer("❌ Ты всё еще не подписан на канал!", show_alert=True)
    await cb.answer()

async def send_pdf(message: Message, data: dict):
    text = "✅ Ты прошел отбор!\n\nВот твой гайд по первым 100к. Забирай 👇\n\n💸 А еще у меня есть офферы, где платят до **15 000 ₽** за оформление карты.\n\nХочешь получить доступ? Напиши мне слово «РКО» 👇\n" + MANAGER_URL
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Написать «РКО»", url=MANAGER_URL)]])
    if os.path.exists("guide.pdf"):
        await message.answer_document(FSInputFile("guide.pdf", filename="guide.pdf"), caption=text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)

async def handle(request): return web.Response(text="Bot is running!")

async def main():
    await init_db()
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.getenv("PORT", 8080)))
    await site.start()
    logging.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
