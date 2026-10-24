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
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
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
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            age TEXT,
            rko TEXT,
            income TEXT,
            location TEXT,
            call_status TEXT,
            joined_at TEXT
        )
        """)

class Quiz(StatesGroup):
    age = State()
    rko = State()
    income = State()
    location = State()
    call_status = State()

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def start(msg: Message, state: FSMContext):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE user_id=$1", msg.from_user.id)
    
    if row:
        await msg.answer("Ты уже проходил опрос. Пиши менеджеру: " + MANAGER_URL)
        return

    await msg.answer("Привет! 👋 Рад видеть тебя в команде.\nДавай познакомимся, чтобы я мог подобрать для тебя лучшие офферы.\n\n1/5. Для начала, сколько тебе лет?\n(Это поможет мне подобрать варианты, которые лучше всего подойдут под твой возраст)")
    await state.set_state(Quiz.age)

@dp.message(Quiz.age)
async def q_age(msg: Message, state: FSMContext):
    await state.update_data(age=msg.text)
    await msg.answer("2/5. Оцени свой уровень в РКО от 0 до 10.\n(0 — слышу впервые, 10 — работал с банками и знаю все тонкости)\n\nНапиши просто цифру (например: 3)")
    await state.set_state(Quiz.rko)

@dp.message(Quiz.rko)
async def q_rko(msg: Message, state: FSMContext):
    await state.update_data(rko=msg.text)
    await msg.answer("3/5. Какую сумму ты хочешь зарабатывать в месяц?\nНапиши конкретно (например: 50к, 100к, 300к). Это нужно, чтобы понять твои цели.")
    await state.set_state(Quiz.income)

@dp.message(Quiz.income)
async def q_income(msg: Message, state: FSMContext):
    await state.update_data(income=msg.text)
    await msg.answer("4/5. Из какого ты города?\n(Это нужно, чтобы подобрать банки, которые лучше всего работают в твоем регионе)")
    await state.set_state(Quiz.location)

@dp.message(Quiz.location)
async def q_loc(msg: Message, state: FSMContext):
    await state.update_data(location=msg.text)
    await msg.answer("5/5. Готов ли ты выйти на короткий созвон (10-15 минут)?\nНа нем мы обсудим, как тебе выйти на первые 100к. Без воды, только конкретика.", 
                     reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                         [InlineKeyboardButton(text="✅ Да, готов", callback_data="call_yes")],
                         [InlineKeyboardButton(text="❌ Нет, не готов", callback_data="call_no")]
                     ]))
    await state.set_state(Quiz.call_status)

@dp.callback_query(Quiz.call_status)
async def q_call(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(call=cb.data)

    # ИСПОЛЬЗУЕМ ON CONFLICT DO NOTHING, ЧТОБЫ ИЗБЕЖАТЬ ОШИБКИ DUPLICATE KEY
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id, username, age, rko, income, location, call_status, joined_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT (user_id) DO NOTHING",
            cb.from_user.id, cb.from_user.username, data.get("age"), data.get("rko"), data.get("income"), data.get("location"), cb.data, datetime.now().isoformat()
        )

    call_text = "Да" if cb.data == "call_yes" else "Нет"
    admin_text = f"🔥 Новая анкета!\n\n👤 @{cb.from_user.username or 'нет'}\n🆔 ID: {cb.from_user.id}\n🎂 Возраст: {data.get('age')}\n📊 РКО: {data.get('rko')}/10\n💰 Доход: {data.get('income')}\n📍 Город: {data.get('location')}\n📞 Созвон: {call_text}"
    for admin_id in ADMIN_IDS:
        try: await bot.send_message(admin_id, admin_text)
        except: pass

    # ПОКАЗЫВАЕМ КНОПКИ ПОДПИСКИ
    channel_link = f"https://t.me/{CHANNEL_ID.replace('@', '')}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться на канал", url=channel_link)],
        [InlineKeyboardButton(text="✅ Я подписался, дай PDF", callback_data="give_pdf")]
    ])
    await cb.message.edit_text("Спасибо за ответы! 🎁\n\nТвой гайд и доступ к офферам (до 15 000 ₽ за карту) уже готовы.\n\nПодпишись на наш канал и забери файл 👇", reply_markup=kb)
    await cb.answer()

@dp.callback_query(F.data == "give_pdf")
async def give_pdf(cb: CallbackQuery):
    # БЕЗ ПРОВЕРКИ ПОДПИСКИ - СРАЗУ ДАЕМ PDF
    await cb.message.edit_text("✅ Отлично! Забирай:")
    text = "✅ Ты прошел отбор!\n\nВот твой гайд по первым 100к. Забирай 👇\n\n💸 А еще у меня есть офферы, где платят до 15 000 ₽ за оформление карты.\n\nХочешь получить доступ? Напиши мне слово «РКО» 👇\n" + MANAGER_URL
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Написать «РКО»", url=MANAGER_URL)]])
    
    if os.path.exists("guide.pdf"):
        await cb.message.answer_document(FSInputFile("guide.pdf"), caption=text, reply_markup=kb)
    else:
        await cb.message.answer(text, reply_markup=kb)
    await cb.answer()

async def handle(request): return web.Response(text="OK")

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
