import asyncio
import json
import logging
import aiohttp
import re
import os
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

# --- 1. НАСТРОЙКИ ---
API_TOKEN = os.getenv('API_TOKEN')
STEAM_APIS_KEY = os.getenv('STEAM_APIS_KEY', 'd3UBKCHmu7CHGLiokpc6UR-mOtU')
ADMIN_ID = 368097348  
PROXY_URL = "http://ngimrngimr23:hpC78oAwq5@151.247.120.145:50100"

SETTINGS_FILE = 'settings.json'
DEFAULT_SETTINGS = {
    "min_price": 0.8,
    "drop_percentage": 0.1,
    "sticker_markup": [8.0, 6.5, 5.5],
    "streak_markup": {"2": 10.0, "3": 14.0, "4": 20.0, "5": 25.0}
}

# --- 2. УПРАВЛЕНИЕ НАСТРОЙКАМИ ---
def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logging.warning(f"Ошибка загрузки настроек: {e}")
    return DEFAULT_SETTINGS

def save_settings(s):
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(s, f, indent=4)
    except Exception as e:
        logging.error(f"Ошибка сохранения настроек: {e}")

settings = load_settings()

class SetupStates(StatesGroup):
    waiting_for_drop_perc = State()
    waiting_for_min_price = State()
    waiting_for_sticker_markups = State()
    waiting_for_streak_markups = State()

# --- 3. ИНТЕРФЕЙС (КЛАВИАТУРЫ) ---
def get_main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Общие настройки"), KeyboardButton(text="Настройки стикеров")],
        [KeyboardButton(text="Настройки флоатов"), KeyboardButton(text="Настройки агентов")],
        [KeyboardButton(text="Настройки брелоков"), KeyboardButton(text="Настройки гемов")],
        [KeyboardButton(text="Настройки оферов"), KeyboardButton(text="Вернуться")]
    ], resize_keyboard=True)

def get_stickers_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Минимальная цена"), KeyboardButton(text="Наценки за стикеры")],
        [KeyboardButton(text="Наценки за стрики"), KeyboardButton(text="Потертости стриков")],
        [KeyboardButton(text="Вернуться в главное меню")]
    ], resize_keyboard=True)

# --- 4. ОБРАБОТЧИКИ ---
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🚀 Снайпер-бот запущен и мониторит рынок!", reply_markup=get_main_kb())

@dp.message(F.text.in_(["Вернуться в главное меню", "Вернуться"]))
async def back_to_main(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Главное меню:", reply_markup=get_main_kb())

# МЕНЮ СТИКЕРОВ
@dp.message(F.text == "Настройки стикеров")
async def sticker_menu(message: types.Message, state: FSMContext):
    await state.clear()
    m = settings.get('sticker_markup', [8.0, 6.5, 5.5])
    text = (
        "📊 <b>Настройки стикеров:</b>\n\n"
        f"Мин. цена предмета 👉 {settings.get('min_price', 0.8)}$\n"
        f"Наценка (>300$) 👉 {m[2]}%\n"
        f"Наценка (100-300$) 👉 {m[1]}%\n"
        f"Наценка (&lt;100$) 👉 {m[0]}%\n\n"
        "Выберите раздел для изменения:"
    )
    await message.answer(text, reply_markup=get_stickers_kb(), parse_mode="HTML")

# --- ОБРАБОТКА НАЖАТИЯ ВНУТРЕННИХ КНОПОК ---

@dp.message(F.text == "Минимальная цена")
async def edit_min(m: types.Message, state: FSMContext):
    await state.set_state(SetupStates.waiting_for_min_price)
    await m.answer("Введите мин. цену предмета (например, 0.5):")

@dp.message(SetupStates.waiting_for_min_price)
async def proc_min(m: types.Message, state: FSMContext):
    try:
        val = float(m.text.replace(',', '.'))
        settings['min_price'] = val
        save_settings(settings)
        await m.answer(f"✅ Сохранено: {val}$", reply_markup=get_stickers_kb())
        await state.clear()
    except:
        await m.answer("❌ Введите число!")

@dp.message(F.text == "Наценки за стикеры")
async def edit_st_markup(m: types.Message, state: FSMContext):
    await state.set_state(SetupStates.waiting_for_sticker_markups)
    await m.answer("Введите 3 наценки через пробел (напр: 10 8 5):")

@dp.message(SetupStates.waiting_for_sticker_markups)
async def proc_st_markup(m: types.Message, state: FSMContext):
    try:
        v = [float(x) for x in m.text.replace(',','.').split()]
        if len(v) == 3:
            settings['sticker_markup'] = v
            save_settings(settings)
            await m.answer("✅ Наценки обновлены!", reply_markup=get_stickers_kb())
            await state.clear()
        else:
            await m.answer("❌ Нужно ввести ровно 3 числа!")
    except:
        await m.answer("❌ Ошибка ввода!")

@dp.message(F.text == "Наценки за стрики")
async def edit_streak(m: types.Message, state: FSMContext):
    await state.set_state(SetupStates.waiting_for_streak_markups)
    await m.answer("Введите 4 числа для стриков (2шт 3шт 4шт 5шт):")

@dp.message(SetupStates.waiting_for_streak_markups)
async def proc_streak(m: types.Message, state: FSMContext):
    try:
        v = [float(x) for x in m.text.replace(',','.').split()]
        if len(v) == 4:
            settings['streak_markup'] = {"2": v[0], "3": v[1], "4": v[2], "5": v[3]}
            save_settings(settings)
            await m.answer("✅ Стрики обновлены!", reply_markup=get_stickers_kb())
            await state.clear()
        else:
            await m.answer("❌ Нужно ввести ровно 4 числа!")
    except:
        await m.answer("❌ Ошибка ввода!")

@dp.message(F.text == "Потертости стриков")
async def edit_wear(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("⚠️ Функция учета потертостей пока в разработке.", reply_markup=get_stickers_kb())

# --- ОФЕРЫ ---
@dp.message(F.text == "Настройки оферов")
async def ask_drop(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(f"Текущий порог: {settings.get('drop_percentage') or 15}%\nВведите новый %:")
    await state.set_state(SetupStates.waiting_for_drop_perc)

@dp.message(SetupStates.waiting_for_drop_perc)
async def proc_drop(message: types.Message, state: FSMContext):
    try:
        val = float(message.text.replace(',', '.'))
        settings['drop_percentage'] = val
        save_settings(settings)
        await message.answer(f"✅ Порог {val}% установлен", reply_markup=get_main_kb())
        await state.clear()
    except:
        await message.answer("❌ Введите число!")

# --- 5. ЯДРО (ПАРСЕР) ---
async def market_parser():
    logging.info("🎬 [ПАРСЕР] Фоновая задача успешно запущена!")
    base_prices = {}
    steam_url = "https://steamcommunity.com/market/search/render/?query=&start=0&count=10&sort_column=default&sort_dir=desc&appid=730&norender=1&currency=1"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                # ЗАГРУЗКА ИЗ STEAMAPIS
                if not base_prices:
                    logging.info("🔄 [ПАРСЕР] Запрос базы цен SteamApis...")
                    async with session.get(f"https://api.steamapis.com/market/items/730?api_key={STEAM_APIS_KEY}") as r:
                        if r.status == 200:
                            data = await r.json()
                            for i in data.get('data', []):
                                base_prices[i['market_hash_name']] = i.get('price', {}).get('avg', 0)
                            logging.info(f"✅ [ПАРСЕР] База загружена! Предметов: {len(base_prices)}")
                        else:
                            logging.error(f"❌ [ПАРСЕР] Ошибка SteamApis! Статус: {r.status}")
                            await asyncio.sleep(60)
                            continue

                # ПАРСИНГ СТИМА
                try:
                    async with session.get(steam_url, headers=headers, proxy=PROXY_URL, timeout=10) as r:
                        if r.status == 200:
                            data = await r.json()
                            results = data.get("results", [])
                            logging.info(f"🔎 [ПАРСЕР] Стим ответил. Проверяю {len(results)} лотов...")
                            
                            for item in results:
                                name = item.get("hash_name")
                                price_raw = item.get("sell_price_text", "$0").replace('$','').replace(',','').split()[0]
                                curr_price = float(price_raw)
                                
                                if curr_price < settings['min_price']: continue
                                
                                # Проверка скидки по проценту
                                if name in base_prices:
                                    base_p = base_prices[name]
                                    drop = (1 - (curr_price / base_p)) * 100
                                    
                                    if drop >= settings['drop_percentage']:
                                        msg = f"🔥 <b>СКИДКА {round(drop, 1)}%!</b>\n📦 {name}\n💰 Цена: {curr_price}$\n📊 База: {base_p}$"
                                        await bot.send_message(ADMIN_ID, msg, parse_mode="HTML")
                        elif r.status == 429:
                            logging.warning("⚠️ [ПАРСЕР] Лимит запросов к Steam (429). Ждем 30 сек.")
                            await asyncio.sleep(30)
                        else:
                            logging.warning(f"⚠️ [ПАРСЕР] Ошибка Steam: {r.status}")
                except Exception as e:
                    logging.error(f"❌ [ПАРСЕР] Ошибка сети/прокси: {e}")

                await asyncio.sleep(5)
            except Exception as e:
                logging.error(f"🚨 [ПАРСЕР] Критическая ошибка цикла: {e}")
                await asyncio.sleep(10)

# --- 6. ЗАПУСК ДЛЯ RENDER ---
async def handle_ping(request): 
    return web.Response(text="Alive")

async def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logging.info("🚀 Запуск приложения...")
    
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 10000).start()
    logging.info("🌐 Веб-сервер запущен")
    
    parser_task = asyncio.create_task(market_parser())
    
    logging.info("🤖 Бот подключается к Telegram...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
                                        
