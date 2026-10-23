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
    BufferedInputFile, FSInputFile, InlineKeyboardButton
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
            blocked INT DEFAULT 0,
            score INT DEFAULT 0
        )
        """)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

class Quiz(StatesGroup):
    age = State()
    experience = State()
    time = State()
    traffic = State()
    wait = State()

class AdminState(StatesGroup):
    broadcast_text = State()

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# Функция для генерации кнопок
def get_kb(options):
    buttons = []
    for text, callback in options:
        buttons.append([InlineKeyboardButton(text=text, callback_data=callback)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@dp.message(Command("start"))
async def start(msg: Message, state: FSMContext):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT user_id FROM users WHERE user_id=$1", msg.from_user.id)
    
    if row:
        await msg.answer("Ты уже проходил опрос. Если хочешь пообщаться с менеджером, пиши ему: " + MANAGER_URL)
        return

    # Вопрос 1
    await msg.answer("1/5. Сколько тебе лет?")
    await state.set_state(Quiz.age)

@dp.message(Quiz.age)
async def quiz_age(msg: Message, state: FSMContext):
    await state.update_data(age=msg.text.strip()[:50])
    await msg.answer("2/5. Какой у тебя опыт в привлечении клиентов (трафике)?", 
                     reply_markup=get_kb([("Новичок, опыта нет", "exp_new"), ("Есть опыт в других темах", "exp_other"), ("Работал с банками/МФО", "exp_bank")]))
    await state.set_state(Quiz.experience)

@dp.callback_query(Quiz.experience)
async def quiz_experience(cb: CallbackQuery, state: FSMContext):
    await state.update_data(exp=cb.data)
    await cb.message.edit_text("3/5. Сколько времени в день ты готов уделять работе?")
    await cb.message.answer("Выбери вариант:", 
                            reply_markup=get_kb([("1-2 часа", "time_1"), ("3-5 часов", "time_3"), ("Весь день", "time_all")]))
    await state.set_state(Quiz.time)
    await cb.answer()

@dp.callback_query(Quiz.time)
async def quiz_time(cb: CallbackQuery, state: FSMContext):
    await state.update_data(time=cb.data)
    await cb.message.edit_text("4/5. Как ты планируешь искать клиентов?")
    await cb.message.answer("Выбери вариант:", 
                            reply_markup=get_kb([("Тёплые знакомые", "tr_warm"), ("Реклама/ТГ", "tr_tg"), ("Куплю базу номеров", "tr_buy")]))
    await state.set_state(Quiz.traffic)
    await cb.answer()

@dp.callback_query(Quiz.traffic)
async def quiz_traffic(cb: CallbackQuery, state: FSMContext):
    await state.update_data(traffic=cb.data)
    await cb.message.edit_text("5/5. Готов ли ты первые 2 недели работать без выплат, пока не пойдут первые апрувы?")
    await cb.message.answer("Выбери вариант:", 
                            reply_markup=get_kb([("Да, готов", "wait_yes"), ("Нет, нужны деньги сразу", "wait_no")]))
    await state.set_state(Quiz.wait)
    await cb.answer()

@dp.callback_query(Quiz.wait)
async def quiz_wait(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    
    # Логика скоринга
    score = 0
    if data.get("exp") == "exp_bank": score += 3
    elif data.get("exp") == "exp_other": score += 2
    
    if data.get("time") == "time_all": score += 3
    elif data.get("time") == "time_3": score += 2
    
    if data.get("traffic") == "tr_tg": score += 3
    elif data.get("traffic") == "tr_warm": score += 2
    
    if data.get("wait") == "wait_yes": score += 3
    
    # Сохраняем в БД
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id, username, age, rko_knowledge, desired_income, location, call_ready, joined_at, score) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
            cb.from_user.id, cb.from_user.username, data.get("age"), data.get("exp"), 
            data.get("time"), data.get("traffic"), data.get("wait"), datetime.now().isoformat(), score
        )
    await state.clear()

    # Формируем уведомление с флагами
    flags = []
    if data.get("traffic") == "tr_buy": flags.append("🚩 Купит базу (скам)")
    if data.get("wait") == "wait_no": flags.append("🚩 Не готов ждать выплат")
    if data.get("time") == "time_1": flags.append("⚠️ Мало времени (1-2 часа)")
    
    flag_text = "\n".join(flags) if flags else "✅ Чистый лид"

    admin_text = (
        f"🔥 **Новая анкета!** (Скоринг: {score}/12)\n\n"
        f"👤 @{cb.from_user.username or 'без_юзернейма'}\n"
        f"🆔 ID: {cb.from_user.id}\n"
        f"🎂 Возраст: {data.get('age')}\n"
        f"💼 Опыт: {data.get('exp')}\n"
        f"⏳ Время: {data.get('time')}\n"
        f"📊 Трафик: {data.get('traffic')}\n"
        f"💰 Готов ждать: {data.get('wait')}\n\n"
        f"**{flag_text}**"
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_text, parse_mode="Markdown")
        except Exception as e:
            logging.warning(f"Не удалось отправить уведомление: {e}")

    # Ответ клиенту в зависимости от скоринга
    if score >= 7:
        await cb.message.answer(
            f"✅ Отлично! Ты прошел отбор.\n\n"
            f"Твой гайд по первым 100к готов. Забирай 👇\n\n"
            f"Хочешь получить деньги за простые действия? Напиши мне слово «РКО»: {MANAGER_URL}"
        )
        pdf_path = "guide.pdf"
        if os.path.exists(pdf_path):
            file = FSInputFile(pdf_path, filename="guide.pdf")
            await cb.message.answer_document(file)
        else:
            await cb.message.answer("⚠️ (Файл guide.pdf не найден).")
    else:
        await cb.message.answer(
            f"⚠️ Спасибо за ответы, но пока мы не готовы взять тебя в команду.\n"
            f"С такими вводными (мало времени или не готов ждать выплат) лучше начать с другого.\n"
            f"Если передумаешь — пиши: {MANAGER_URL}"
        )

    await cb.answer()

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
        good = await conn.fetchval("SELECT COUNT(*) FROM users WHERE score >= 7")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="a_back")]])
    await cb.message.answer(f"📊 Всего анкет: {total}\n🔥 Прошли отбор (Скоринг 7+): {good}", reply_markup=kb)
    await cb.answer()

@dp.callback_query(F.data == "a_back")
async def a_back(cb: CallbackQuery):
    await cb.message.edit_text("🛠 Админ-панель", reply_markup=admin_menu())

@dp.callback_query(F.data.startswith("a_users:"))
async def a_users(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return await cb.answer("Нет доступа", show_alert=True)
    page = int(cb.data.split(":")[1])
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id, age, rko_knowledge, score FROM users ORDER BY joined_at DESC LIMIT 5 OFFSET $1", page*5)
    
    if not rows: return await cb.answer("Пусто", show_alert=True)
    
    text = f"👥 Анкеты (стр. {page+1})\n\n"
    for r in rows:
        text += f"ID: {r['user_id']} | {r['age']} лет | Скоринг: {r['score']}/12\n\n"
    
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
    writer.writerow(["user_id", "username", "age", "exp", "time", "traffic", "wait", "score"])
    for r in rows:
        writer.writerow([r['user_id'], r['username'], r['age'], r['rko_knowledge'], r['desired_income'], r['location'], r['call_ready'], r['score']])
    
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
