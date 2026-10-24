import asyncio
import csv
import io
import logging
import os
from datetime import datetime, timedelta
import asyncpg
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile, FSInputFile,
)
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
MANAGER_URL = os.getenv("MANAGER_URL", "https://t.me/kindbulat")
DATABASE_URL = os.getenv("DATABASE_URL")
CHANNEL_ID = "@semuabuz"

if not BOT_TOKEN: raise SystemExit("BOT_TOKEN не задан")
if not ADMIN_IDS: raise SystemExit("ADMIN_IDS не задан")
if not DATABASE_URL: raise SystemExit("DATABASE_URL не задан")

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
            rko_knowledge TEXT,
            desired_income TEXT,
            location TEXT,
            call_ready TEXT,
            joined_at TEXT,
            last_reminded TEXT,
            blocked INT DEFAULT 0
        )
        """)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

class Quiz(StatesGroup):
    age = State()
    rko_knowledge = State()
    desired_income = State()
    location = State()
    call_ready = State()

class AdminState(StatesGroup):
    broadcast_text = State()

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
async def quiz_age(msg: Message, state: FSMContext):
    await state.update_data(age=msg.text.strip()[:50])
    await msg.answer("2/5. Насколько ты знаешь РКО? (0-10)")
    await state.set_state(Quiz.rko_knowledge)

@dp.message(Quiz.rko_knowledge)
async def quiz_rko(msg: Message, state: FSMContext):
    await state.update_data(rko=msg.text.strip()[:50])
    await msg.answer("3/5. Какой доход хочешь?")
    await state.set_state(Quiz.desired_income)

@dp.message(Quiz.desired_income)
async def quiz_income(msg: Message, state: FSMContext):
    await state.update_data(income=msg.text.strip()[:100])
    await msg.answer("4/5. Город?")
    await state.set_state(Quiz.location)

@dp.message(Quiz.location)
async def quiz_location(msg: Message, state: FSMContext):
    await state.update_data(location=msg.text.strip()[:100])
    await msg.answer("5/5. Готов на созвон?", reply_markup=get_kb([("Да", "call_yes"), ("Нет", "call_no")]))
    await state.set_state(Quiz.call_ready)

@dp.callback_query(Quiz.call_ready)
async def quiz_call(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id, username, age, rko_knowledge, desired_income, location, call_ready, joined_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
            cb.from_user.id, cb.from_user.username, data.get("age"), data.get("rko"), 
            data.get("income"), data.get("location"), cb.data, datetime.now().isoformat()
        )
    await state.clear()

    call_status = "Да" if cb.data == "call_yes" else "Нет"
    admin_text = f"🔥 **Новая анкета!**\n\n👤 @{cb.from_user.username or 'нет'}\n🆔 ID: {cb.from_user.id}\n🎂 Возраст: {data.get('age')}\n📊 РКО: {data.get('rko')}/10\n💰 Доход: {data.get('income')}\n📍 Город: {data.get('location')}\n📞 Созвон: {call_status}"
    
    for admin_id in ADMIN_IDS:
        try: await bot.send_message(admin_id, admin_text, parse_mode="Markdown")
        except: pass

    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=cb.from_user.id)
        if member.status in ['left', 'kicked']:
            raise Exception("Not subscribed")
    except Exception:
        channel_link = f"https://t.me/{CHANNEL_ID.replace('@', '')}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Подписаться на канал", url=channel_link)],
            [InlineKeyboardButton(text="✅ Я подписался, дай PDF", callback_data="check_sub")]
        ])
        await cb.message.edit_text("Спасибо за ответы! 🎁\n\nТвой гайд готов. Но чтобы забрать его, подпишись на наш канал 👇", reply_markup=kb)
        return

    await send_pdf(cb.message, data)
    await cb.answer()

@dp.callback_query(F.data == "check_sub")
async def check_sub(cb: CallbackQuery):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE user_id=$1", cb.from_user.id)
    if not row:
        await cb.answer("Ошибка. Напиши /start", show_alert=True)
        return

    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=cb.from_user.id)
        if member.status in ['left', 'kicked']:
            raise Exception("Not subscribed")
        
        await cb.message.edit_text("✅ Подписка подтверждена! Забирай:")
        await send_pdf(cb.message, row)
    except Exception:
        await cb.answer("❌ Ты всё еще не подписан на канал!", show_alert=True)
    await cb.answer()

async def send_pdf(message: Message, data: dict):
    text = "✅ Ты прошел отбор!\n\nВот твой гайд по первым 100к. Забирай 👇\n\n💸 А еще у меня есть офферы, где платят до **15 000 ₽** за оформление карты.\n\nХочешь получить доступ? Напиши мне слово «РКО» 👇\n" + MANAGER_URL
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Написать «РКО»", url=MANAGER_URL)]])
    if os.path.exists("guide.pdf"):
        await message.answer_document(FSInputFile("guide.pdf", filename="guide.pdf"), caption=text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb, disable_web_page_preview=True)

    await asyncio.sleep(86400)
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT blocked FROM users WHERE user_id=$1", message.from_user.id)
        if user and user['blocked'] == 0:
            try:
                await bot.send_message(message.from_user.id, f"👋 {message.from_user.first_name}, ты так и не забрал оффер.\nМеста ограничены. Если не готов — передам заявку другому.\nЕсли готов — пиши 👇", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Забрать оффер", url=MANAGER_URL)]]))
            except: pass

async def reminder_loop():
    while True:
        try:
            now = datetime.now()
            async with db_pool.acquire() as conn:
                rows = await conn.fetch("SELECT user_id, last_reminded FROM users WHERE blocked=0")
                for row in rows:
                    last = row['last_reminded']
                    if last is None or (now - datetime.fromisoformat(last)) >= timedelta(days=1):
                        try:
                            await bot.send_message(row['user_id'], "👋 Напоминаю: ты проходил опрос. Жми кнопку!", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Написать «РКО»", url=MANAGER_URL)]]))
                            await conn.execute("UPDATE users SET last_reminded=$1 WHERE user_id=$2", now.isoformat(), row['user_id'])
                        except: pass
        except: pass
        await asyncio.sleep(3600)

def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👥 Анкеты", callback_data="a_users:0")],[InlineKeyboardButton(text="📊 Статистика", callback_data="a_stats")],[InlineKeyboardButton(text="📢 Рассылка", callback_data="a_broadcast")]])

@dp.message(Command("admin"))
async def admin_cmd(msg: Message):
    if is_admin(msg.from_user.id): await msg.answer("🛠 Админ-панель", reply_markup=admin_menu())

@dp.callback_query(F.data == "a_stats")
async def a_stats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return await cb.answer("Нет доступа", show_alert=True)
    async with db_pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM users")
        ready = await conn.fetchval("SELECT COUNT(*) FROM users WHERE call_ready = 'call_yes'")
    await cb.message.answer(f"📊 Всего: {total}\n🔥 Готовы к созвону: {ready}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="a_back")]]))
    await cb.answer()

@dp.callback_query(F.data == "a_back")
async def a_back(cb: CallbackQuery): await cb.message.edit_text("🛠 Админ-панель", reply_markup=admin_menu())

@dp.callback_query(F.data.startswith("a_users:"))
async def a_users(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return await cb.answer("Нет", show_alert=True)
    page = int(cb.data.split(":")[1])
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id, age, location, call_ready FROM users ORDER BY joined_at DESC LIMIT 5 OFFSET $1", page*5)
    if not rows: return await cb.answer("Пусто", show_alert=True)
    text = f"👥 Стр. {page+1}\n\n" + "\n".join([f"ID: {r['user_id']} | {r['age']} лет | {r['location']} | Созвон: {'Да' if r['call_ready']=='call_yes' else 'Нет'}" for r in rows])
    nav = []
    if page > 0: nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"a_users:{page-1}"))
    nav.append(InlineKeyboardButton(text="◀️ Меню", callback_data="a_back"))
    nav.append(InlineKeyboardButton(text="➡️", callback_data=f"a_users:{page+1}"))
    try: await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[nav]))
    except: await cb.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[nav]))
    await cb.answer()

@dp.callback_query(F.data == "a_broadcast")
async def a_broadcast(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return await cb.answer("Нет", show_alert=True)
    await cb.message.answer("📢 Введи текст (или /cancel):")
    await state.set_state(AdminState.broadcast_text)
    await cb.answer()

@dp.message(AdminState.broadcast_text)
async def a_broadcast_send(msg: Message, state: FSMContext):
    if msg.text == "/cancel": await state.clear(); return await msg.answer("Отменено.")
    await state.clear()
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM users WHERE blocked=0")
    sent = 0
    for row in rows:
        try:
            await bot.send_message(row['user_id'], msg.text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Написать «РКО»", url=MANAGER_URL)]]))
            sent += 1
        except: pass
        await asyncio.sleep(0.05)
    await msg.answer(f"✅ Отправлено: {sent}", reply_markup=admin_menu())

async def handle(request): return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.getenv("PORT", 8080)))
    await site.start()

async def main():
    await init_db()
    asyncio.create_task(reminder_loop())
    await start_web_server()
    logging.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
