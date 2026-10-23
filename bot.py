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
            subscribed INT DEFAULT 1,
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

@dp.message(Command("start"))
async def start(msg: Message, state: FSMContext):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT user_id FROM users WHERE user_id=$1", msg.from_user.id)
    
    if row:
        await msg.answer("Ты уже проходил опрос. Если хочешь пообщаться с менеджером, пиши ему: " + MANAGER_URL)
        return
    await msg.answer("1/5. Сколько тебе лет?")
    await state.set_state(Quiz.age)

@dp.message(Quiz.age)
async def quiz_age(msg: Message, state: FSMContext):
    await state.update_data(age=msg.text.strip()[:50])
    await msg.answer("2/5. Насколько ты знаешь о такой вертикали как РКО? (от 0 до 10)")
    await state.set_state(Quiz.rko_knowledge)

@dp.message(Quiz.rko_knowledge)
async def quiz_rko(msg: Message, state: FSMContext):
    await state.update_data(rko=msg.text.strip()[:50])
    await msg.answer("3/5. Расскажи, на какой уровень дохода ты бы хотел сейчас выйти.")
    await state.set_state(Quiz.desired_income)

@dp.message(Quiz.desired_income)
async def quiz_income(msg: Message, state: FSMContext):
    await state.update_data(income=msg.text.strip()[:100])
    await msg.answer("4/5. Из какой ты страны и города?")
    await state.set_state(Quiz.location)

@dp.message(Quiz.location)
async def quiz_location(msg: Message, state: FSMContext):
    await state.update_data(location=msg.text.strip()[:100])
    await msg.answer("5/5. Хочешь ли ты выйти на созвон, где мы обсудим конкретно ТВОЮ ситуацию?")
    await state.set_state(Quiz.call_ready)

@dp.message(Quiz.call_ready)
async def quiz_call(msg: Message, state: FSMContext):
    data = await state.get_data()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id, username, age, rko_knowledge, desired_income, location, call_ready, joined_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
            msg.from_user.id, msg.from_user.username, data.get("age"), data.get("rko"), 
            data.get("income"), data.get("location"), msg.text.strip()[:50], datetime.now().isoformat()
        )
    await state.clear()

    text = (
        "Спасибо за ответы. Твой гайд по первым 100к уже готов. Забирай 👇\n\n"
        "Хочешь получить деньги за простые действия в РКО?\n\n"
        "Напиши мне слово «РКО» 👇\n"
        f"{MANAGER_URL}\n\n"
        "Заявки обрабатываю сразу."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Написать «РКО»", url=MANAGER_URL)]])

    pdf_path = "guide.pdf"
    if os.path.exists(pdf_path):
        file = FSInputFile(pdf_path, filename="guide.pdf")
        await msg.answer_document(file, caption=text, reply_markup=kb)
    else:
        await msg.answer(f"⚠️ (Файл guide.pdf не найден).\n\n{text}", reply_markup=kb, disable_web_page_preview=True)

async def reminder_loop():
    while True:
        try:
            now = datetime.now()
            async with db_pool.acquire() as conn:
                rows = await conn.fetch("SELECT user_id, last_reminded FROM users WHERE subscribed=1 AND blocked=0")
                for row in rows:
                    user_id = row['user_id']
                    last = row['last_reminded']
                    if last is None or (now - datetime.fromisoformat(last)) >= timedelta(days=2):
                        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Написать «РКО»", url=MANAGER_URL)]])
                        try:
                            await bot.send_message(user_id, "👋 Напоминаю: ты проходил опрос по РКО. Хочешь узнать подробнее? Жми кнопку!", reply_markup=kb)
                            await conn.execute("UPDATE users SET last_reminded=$1 WHERE user_id=$2", now.isoformat(), user_id)
                        except Exception:
                            await conn.execute("UPDATE users SET blocked=1 WHERE user_id=$1", user_id)
        except Exception as e:
            logging.error(f"Ошибка в напоминаниях: {e}")
        await asyncio.sleep(3600)

def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Анкеты", callback_data="a_users:0")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="a_stats")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="a_broadcast")],
        [InlineKeyboardButton(text="📤 Выгрузить CSV", callback_data="a_export")],
    ])

@dp.message(Command("admin"))
async def admin_cmd(msg: Message):
    if is_admin(msg.from_user.id):
        await msg.answer("🛠 Админ-панель", reply_markup=admin_menu())

@dp.callback_query(F.data == "a_stats")
async def a_stats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return await cb.answer("Нет доступа", show_alert=True)
    async with db_pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM users")
        ready = await conn.fetchval("SELECT COUNT(*) FROM users WHERE call_ready LIKE '%да%' OR call_ready LIKE '%Да%'")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="a_back")]])
    await cb.message.answer(f"📊 Всего анкет: {total}\n🔥 Готовы к созвону: {ready}", reply_markup=kb)
    await cb.answer()

@dp.callback_query(F.data == "a_back")
async def a_back(cb: CallbackQuery):
    await cb.message.edit_text("🛠 Админ-панель", reply_markup=admin_menu())

@dp.callback_query(F.data.startswith("a_users:"))
async def a_users(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return await cb.answer("Нет доступа", show_alert=True)
    page = int(cb.data.split(":")[1])
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id, age, rko_knowledge, desired_income, location, call_ready FROM users ORDER BY joined_at DESC LIMIT 5 OFFSET $1", page*5)
    
    if not rows: return await cb.answer("Пусто", show_alert=True)
    
    text = f"👥 Анкеты (стр. {page+1})\n\n"
    for r in rows:
        text += f"ID: {r['user_id']} | {r['age']} лет | {r['location']}\nДоход: {r['desired_income']} | РКО: {r['rko_knowledge']}/10\nСозвон: {r['call_ready']}\n\n"
    
    nav = []
    if page > 0: nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"a_users:{page-1}"))
    nav.append(InlineKeyboardButton(text="◀️ Меню", callback_data="a_back"))
    nav.append(InlineKeyboardButton(text="➡️", callback_data=f"a_users:{page+1}"))
    
    kb = InlineKeyboardMarkup(inline_keyboard=[nav])
    try: await cb.message.edit_text(text, reply_markup=kb)
    except: await cb.message.answer(text, reply_markup=kb)
    await cb.answer()

@dp.callback_query(F.data == "a_broadcast")
async def a_broadcast(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return await cb.answer("Нет доступа", show_alert=True)
    await cb.message.answer("📢 Введи текст для рассылки (или /cancel):")
    await state.set_state(AdminState.broadcast_text)
    await cb.answer()

@dp.message(AdminState.broadcast_text)
async def a_broadcast_send(msg: Message, state: FSMContext):
    if msg.text == "/cancel":
        await state.clear()
        return await msg.answer("Отменено.")
    
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Написать «РКО»", url=MANAGER_URL)]])
    
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM users WHERE subscribed=1 AND blocked=0")
        
    sent, failed = 0, 0
    for row in rows:
        uid = row['user_id']
        try:
            await bot.send_message(uid, msg.text, reply_markup=kb)
            sent += 1
        except Exception:
            failed += 1
            async with db_pool.acquire() as conn:
                await conn.execute("UPDATE users SET blocked=1 WHERE user_id=$1", uid)
        await asyncio.sleep(0.05)
    await msg.answer(f"✅ Отправлено: {sent}\n❌ Ошибок: {failed}", reply_markup=admin_menu())

@dp.callback_query(F.data == "a_export")
async def a_export(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return await cb.answer("Нет доступа", show_alert=True)
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM users")
    
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["user_id", "username", "age", "rko_knowledge", "desired_income", "location", "call_ready", "joined_at"])
    for r in rows:
        writer.writerow([r['user_id'], r['username'], r['age'], r['rko_knowledge'], r['desired_income'], r['location'], r['call_ready'], r['joined_at']])
    
    file = BufferedInputFile(output.getvalue().encode("utf-8"), filename="leads.csv")
    await cb.message.answer_document(file, caption=f"Выгрузка: {len(rows)} анкет")
    await cb.answer()

async def handle(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.getenv("PORT", 8080)))
    await site.start()
    logging.info("Веб-сервер запущен на порту")

async def main():
    await init_db()
    asyncio.create_task(reminder_loop())
    await start_web_server()
    logging.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
