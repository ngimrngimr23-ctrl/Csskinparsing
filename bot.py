import asyncio
import json
import logging
import aiohttp
import re
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

# ==========================================
# 1. ТВОИ ДАННЫЕ (ЗАПОЛНИТЬ ПЕРЕД ЗАПУСКОМ)
# ==========================================
API_TOKEN = '8729170911:AAHNn8T5NIUbsdsjQ6PNpzmBJbLsM_3ZrVg'
STEAM_APIS_KEY = 'd3UBKCHmu7CHGLiokpc6UR-mOtU' # Твой купленный ключ
ADMIN_ID = 368097348  # Твой ID в Телеграм (узнай у @userinfobot)

# Если есть прокси, раскомментируй строку ниже и вставь свои данные
# PROXY_URL = 'http://login:password@ip:port'
PROXY_URL = None 

SETTINGS_FILE = 'settings.json'

# Базовые настройки по умолчанию (как на твоих скринах)
DEFAULT_SETTINGS = {
    "min_price": 0.8,
    "drop_percentage": 15.0,
    "sticker_markup": [8.0, 6.5, 5.5], # Ниже 100$, 100-300$, Выше 300$
    "streak_markup": {2: 10.0, 3: 14.0, 4: 20.0, 5: 25.0}
}

# ==========================================
# 2. ИНИЦИАЛИЗАЦИЯ И ХРАНЕНИЕ НАСТРОЕК
# ==========================================
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

def load_settings():
    try:
        with open(SETTINGS_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return DEFAULT_SETTINGS

def save_settings(settings):
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=4)

settings = load_settings()

class SetupStates(StatesGroup):
    waiting_for_drop_perc = State()
    waiting_for_sticker_markups = State()

# ==========================================
# 3. КЛАВИАТУРЫ (ИНТЕРФЕЙС)
# ==========================================
def get_main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Общие настройки"), KeyboardButton(text="Настройки стикеров")],
        [KeyboardButton(text="Настройки флоатов"), KeyboardButton(text="Настройки оферов")],
        [KeyboardButton(text="Вернуться")]
    ], resize_keyboard=True)

def get_stickers_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Минимальная цена"), KeyboardButton(text="Наценки за стикеры")],
        [KeyboardButton(text="Наценки за стрики"), KeyboardButton(text="Потертости стриков")],
        [KeyboardButton(text="Вернуться в главное меню")]
    ], resize_keyboard=True)

# ==========================================
# 4. ОБРАБОТЧИКИ КНОПОК
# ==========================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("🚀 Снайпер-бот запущен! Выберите раздел:", reply_markup=get_main_kb())

@dp.message(F.text == "Вернуться в главное меню")
async def back_to_main(message: types.Message):
    await message.answer("Главное меню:", reply_markup=get_main_kb())

# --- Настройки оферов ---
@dp.message(F.text == "Настройки оферов")
async def setup_offers(message: types.Message, state: FSMContext):
    await message.answer(f"Текущий порог: {settings.get('drop_percentage', 15.0)}%\nВведите новый процент:")
    await state.set_state(SetupStates.waiting_for_drop_perc)

@dp.message(SetupStates.waiting_for_drop_perc)
async def process_drop_perc(message: types.Message, state: FSMContext):
    try:
        val = float(message.text.replace(',', '.'))
        settings['drop_percentage'] = val
        save_settings(settings)
        await message.answer(f"✅ Сохранено! Ищем скидки от {val}%", reply_markup=get_main_kb())
        await state.clear()
    except ValueError:
        await message.answer("❌ Введите число!")

# --- Настройки стикеров ---
@dp.message(F.text == "Настройки стикеров")
async def sticker_menu(message: types.Message):
    markups = settings.get('sticker_markup', [8.0, 6.5, 5.5])
    text = (
        "📊 <b>Настройки стикеров:</b>\n\n"
        f"Наценка (>300$): {markups[2]}%\n"
        f"Наценка (100-300$): {markups[1]}%\n"
        f"Наценка (<100$): {markups[0]}%\n"
    )
    await message.answer(text, reply_markup=get_stickers_kb(), parse_mode="HTML")

@dp.message(F.text == "Наценки за стикеры")
async def ask_sticker_markups(message: types.Message, state: FSMContext):
    await message.answer("Укажите наценки через пробел (<100$ 100-300$ >300$):")
    await state.set_state(SetupStates.waiting_for_sticker_markups)

@dp.message(SetupStates.waiting_for_sticker_markups)
async def process_sticker_markups(message: types.Message, state: FSMContext):
    parts = message.text.replace(',', '.').split()
    if len(parts) != 3:
        await message.answer("❌ Нужно ввести 3 числа через пробел!")
        return
    try:
        settings['sticker_markup'] = [float(parts[0]), float(parts[1]), float(parts[2])]
        save_settings(settings)
        await message.answer("✅ Наценки обновлены!", reply_markup=get_stickers_kb())
        await state.clear()
    except ValueError:
        await message.answer("❌ Введите только числа!")

# ==========================================
# 5. МАТЕМАТИКА И ОЦЕНКА
# ==========================================
def calculate_fair_value(skin_name, stickers, base_prices):
    skin_base_price = base_prices.get(skin_name, 0)
    if skin_base_price == 0:
        return 0
    
    total_overpay = 0
    sticker_counts = {}
    
    for s_name in stickers:
        full_name = f"Sticker | {s_name}"
        s_price = base_prices.get(full_name, 0)
        
        if s_price > 0:
            sticker_counts[s_name] = sticker_counts.get(s_name, 0) + 1
            if s_price >= 300: pct = settings['sticker_markup'][2]
            elif 100 <= s_price < 300: pct = settings['sticker_markup'][1]
            else: pct = settings['sticker_markup'][0]
            
            total_overpay += (s_price * (pct / 100))

    # Стрики
    streak_dict = {str(k): v for k, v in settings['streak_markup'].items()}
    for s_name, count in sticker_counts.items():
        if count >= 2 and str(count) in streak_dict:
            total_overpay += (total_overpay * (streak_dict[str(count)] / 100))

    return skin_base_price + total_overpay

# ==========================================
# 6. ЯДРО БОТА (ПАРСЕР СТИМА)
# ==========================================
async def market_parser():
    base_prices = {}
    steam_url = "https://steamcommunity.com/market/search/render/?query=&start=0&count=10&sort_column=default&sort_dir=desc&appid=730&norender=1"
    headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"}
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                # 1. Загрузка базы цен (раз в 30 минут, тут упрощено для старта)
                if not base_prices:
                    print("🔄 Загружаем прайс-лист SteamApis...")
                    async with session.get(f"https://api.steamapis.com/market/items/730?api_key={STEAM_APIS_KEY}") as resp:
                        if resp.status == 200:
                            for item in (await resp.json()).get('data', []):
                                base_prices[item['market_hash_name']] = item.get('price', {}).get('avg', 0)
                            print(f"✅ База загружена! Предметов: {len(base_prices)}")

                # 2. Парсинг Стима
                kwargs = {"headers": headers}
                if PROXY_URL: kwargs["proxy"] = PROXY_URL
                
                async with session.get(steam_url, **kwargs) as response:
                    if response.status == 200:
                        results = (await response.json()).get("results", [])
                        
                        for item in results:
                            hash_name = item.get("hash_name")
                            price_str = item.get("sell_price_text", "$0.00").replace('$', '').replace(',', '').replace(' USD', '')
                            current_price = float(price_str)
                            
                            if current_price < settings['min_price']: continue

                            # Ищем стикеры
                            stickers = []
                            for desc in item.get("asset_description", {}).get("descriptions", []):
                                val_text = desc.get("value", "")
                                if "Sticker:" in val_text:
                                    found = re.findall(r'<br>Sticker:(.*?)</center>', val_text)
                                    if found:
                                        clean = re.sub(r'<[^>]+>', '', found[0])
                                        stickers = [s.strip() for s in clean.split(',')]
                            
                            # Считаем выгоду
                            fair_price = calculate_fair_value(hash_name, stickers, base_prices)
                            profit = fair_price - current_price
                            
                            if fair_price > 0 and (profit > 0.1 or (1 - (current_price / base_prices.get(hash_name, current_price))) * 100 >= settings['drop_percentage']):
                                st_text = "\n".join([f"🀄️ {s}" for s in stickers]) if stickers else "Нет"
                                msg = (
                                    f"🎯 <b>НАЙДЕН ЛОТ!</b>\n"
                                    f"📦 <b>{hash_name}</b>\n\n"
                                    f"💰 Цена сейчас: <b>{current_price}$</b>\n"
                                    f"⚖️ Справедливая цена: <b>{round(fair_price, 2)}$</b>\n"
                                    f"📈 Профит: <b>+{round(profit, 2)}$</b>\n\n"
                                    f"🔖 <b>Стикеры:</b>\n{st_text}\n\n"
                                    f"🔗 <a href='https://steamcommunity.com/market/listings/730/{hash_name}'>Купить в Steam</a>"
                                )
                                await bot.send_message(ADMIN_ID, msg, parse_mode="HTML", disable_web_page_preview=True)
                                await asyncio.sleep(1)

                    elif response.status == 429:
                        print("⚠️ Steam дал лимит. Ждем 30 сек...")
                        await asyncio.sleep(30)
                        
                await asyncio.sleep(5) 
                
            except Exception as e:
                logging.error(f"Ошибка парсера: {e}")
                await asyncio.sleep(10)

# ==========================================
# 7. ЗАПУСК
# ==========================================
async def main():
    logging.basicConfig(level=logging.INFO)
    asyncio.create_task(market_parser())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
            
