import os
import json
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
from product_categories import (
    get_category_keyboard, get_products_in_category, 
    get_all_product_names, get_product_info
)
# Импорт для работы с базой данных SQLAlchemy
from database_service import db_service
from data_adapter import UserProfile as AdapterUserProfile, sql_user_to_user_profile

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота
bot_token = os.getenv('BOT_TOKEN')
if not bot_token:
    raise ValueError("BOT_TOKEN not found in environment variables")
bot = Bot(token=bot_token)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Состояния FSM
class UserStates(StatesGroup):
    waiting_for_gender = State()
    waiting_for_age = State()
    waiting_for_weight = State()
    waiting_for_height = State()
    waiting_for_activity = State()
    waiting_for_goal = State()
    waiting_for_category = State()
    waiting_for_product_selection = State()
    waiting_for_product_weight = State()

# Перечисления
class Gender(Enum):
    MALE = "male"
    FEMALE = "female"

class Activity(Enum):
    SEDENTARY = 1.2
    LIGHT = 1.375
    MODERATE = 1.55
    ACTIVE = 1.725
    VERY_ACTIVE = 1.9

class Goal(Enum):
    WEIGHT_LOSS = "weight_loss"
    BALANCE = "balance"
    WEIGHT_GAIN = "weight_gain"

# Структуры данных
@dataclass
class UserProfile:
    user_id: int
    gender: Gender
    age: int
    weight: float
    height: float
    activity: Activity
    goal: Goal
    daily_calories: int = 0
    protein: float = 0
    fat: float = 0
    carbs: float = 0
    meals_count: int = 4

@dataclass
class Product:
    name: str
    calories: float
    protein: float
    fat: float
    carbs: float

@dataclass
class MealPlan:
    breakfast: List[Tuple[str, float]]
    snack: List[Tuple[str, float]]
    lunch: List[Tuple[str, float]]
    dinner: List[Tuple[str, float]]
    second_snack: Optional[List[Tuple[str, float]]] = None

# Хранилище данных (теперь используем PostgreSQL)
# Временный кеш для совместимости со старым кодом
users: Dict[int, UserProfile] = {}
user_products: Dict[int, List[Tuple[str, float]]] = {}

async def clear_user_products(user_id: int) -> None:
    """Очищает список продуктов пользователя для нового дня"""
    await db_service.clear_user_products(user_id)
    if user_id in user_products:
        user_products[user_id] = []

async def add_product_to_user(user_id: int, product_name: str, weight: float) -> None:
    """Добавляет продукт к пользователю, объединяя одинаковые продукты"""
    await db_service.add_user_product(user_id, product_name, weight)
    
    # Обновляем локальный кеш
    if user_id not in user_products:
        user_products[user_id] = []
    
    # Ищем, есть ли уже такой продукт в кеше
    for i, (name, existing_weight) in enumerate(user_products[user_id]):
        if name.lower() == product_name.lower():
            # Объединяем веса
            user_products[user_id][i] = (name, existing_weight + weight)
            return
    
    # Если продукт не найден, добавляем новый
    user_products[user_id].append((product_name.lower(), weight))

def get_user_products_summary(user_id: int) -> str:
    """Возвращает краткую сводку продуктов пользователя"""
    if user_id not in user_products or not user_products[user_id]:
        return "Нет добавленных продуктов"
    
    products = user_products.get(user_id, [])
    total_calories = calculate_total_calories(products)
    
    summary = f"🛒 Ваша корзина ({len(products)} шт.):\n\n"
    
    for product_name, weight in products:
        db_product_name = find_similar_product(product_name)
        if db_product_name:
            product = PRODUCTS_DB[db_product_name]
            calories = product.calories * weight / 100
            summary += f"• {product.name} - {weight:.0f}г ({calories:.1f} ккал)\n"
        else:
            # Если продукт не найден, показываем с примерной калорийностью
            calories = 100 * weight / 100
            summary += f"• {product_name} - {weight:.0f}г (~{calories:.1f} ккал)\n"
    
    summary += f"\n📊 Общие калории: {total_calories:.1f}"
    
    if user_id in users:
        user = users[user_id]
        summary += f" / {user.daily_calories} ккал"
        
        if total_calories > user.daily_calories + 200:
            summary += "\n⚠️ Слишком много калорий! Рекомендую уменьшить количество продуктов."
        elif total_calories < user.daily_calories - 100:
            summary += "\n⚠️ Недостаточно калорий! Добавьте еще продуктов для разнообразия."
    
    return summary

async def remove_product_from_user(user_id: int, product_name: str) -> bool:
    """Удаляет продукт из списка пользователя"""
    # Удаляем из базы данных
    db_success = await db_service.remove_user_product(user_id, product_name)
    
    # Удаляем из локального кеша
    if user_id not in user_products:
        return db_success
    
    products = user_products[user_id]
    for i, (name, weight) in enumerate(products):
        if name.lower() == product_name.lower():
            del products[i]
            break
    
    return db_success

async def load_user_from_db(user_id: int) -> Optional[UserProfile]:
    """Загружает пользователя из базы данных в кеш"""
    sql_user = await db_service.get_user(user_id)
    adapter_user = sql_user_to_user_profile(sql_user)
    if not adapter_user:
        return None
    # Конвертируем в локальную модель и локальные Enum'ы
    try:
        local_user = UserProfile(
            user_id=adapter_user.user_id,
            gender=Gender(adapter_user.gender.value),
            age=adapter_user.age,
            weight=adapter_user.weight,
            height=adapter_user.height,
            activity=Activity(adapter_user.activity.value),
            goal=Goal(adapter_user.goal.value),
            daily_calories=adapter_user.daily_calories,
            protein=adapter_user.protein,
            fat=adapter_user.fat,
            carbs=adapter_user.carbs,
            meals_count=adapter_user.meals_count,
        )
    except Exception as e:
        logging.error(f"Ошибка конвертации пользователя {user_id} из БД: {e}")
        return None
    users[user_id] = local_user
    return local_user

async def load_user_products_from_db(user_id: int) -> List[Tuple[str, float]]:
    """Загружает продукты пользователя из базы данных в кеш"""
    products = await db_service.get_user_products(user_id)
    user_products[user_id] = products
    return products

async def save_user_to_db(user: UserProfile) -> bool:
    """Сохраняет пользователя в базу данных и кеш"""
    # Преобразуем локальный профиль пользователя в адаптерный для корректного сохранения
    try:
        from models import Gender as ModelGender, Activity as ModelActivity, Goal as ModelGoal
        adapted_user: AdapterUserProfile = AdapterUserProfile(
            user_id=user.user_id,
            gender=ModelGender(user.gender.value),
            age=user.age,
            weight=user.weight,
            height=user.height,
            activity=ModelActivity(user.activity.value),
            goal=ModelGoal(user.goal.value),
            daily_calories=user.daily_calories,
            protein=user.protein,
            fat=user.fat,
            carbs=user.carbs,
            meals_count=user.meals_count,
        )
        success = await db_service.save_user(adapted_user.to_dict())
    except Exception as e:
        logging.error(f"Не удалось сохранить пользователя {user.user_id}: {e}")
        return False
    if success:
        users[user.user_id] = user
    return success

def get_products_management_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру для управления продуктами"""
    if user_id not in user_products or not user_products[user_id]:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_compose_plan")]
        ])
    
    keyboard = []
    products = user_products[user_id]
    
    # Добавляем кнопки для каждого продукта
    for product_name, weight in products:
        db_product_name = find_similar_product(product_name)
        if db_product_name:
            product = PRODUCTS_DB[db_product_name]
            calories = product.calories * weight / 100
            keyboard.append([
                InlineKeyboardButton(
                    text=f"🗑️ {product.name} ({weight:.0f}г) - {calories:.1f} ккал", 
                    callback_data=f"remove_{product_name}"
                )
            ])
        else:
            # Если продукт не найден, показываем с примерной калорийностью
            calories = 100 * weight / 100
            keyboard.append([
                InlineKeyboardButton(
                    text=f"🗑️ {product_name} ({weight:.0f}г) - ~{calories:.1f} ккал", 
                    callback_data=f"remove_{product_name}"
                )
            ])
    
    # Добавляем кнопки управления
    keyboard.append([
        InlineKeyboardButton(text="🗑️ Очистить все", callback_data="clear_all_products"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_compose_plan")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# Загрузка базы продуктов
def load_products() -> Dict[str, Product]:
    """Загружает базу продуктов из JSON файла"""
    try:
        with open('products.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            products = {}
            for item in data:
                products[item['name'].lower()] = Product(
                    name=item['name'],
                    calories=item['calories'],
                    protein=item['protein'],
                    fat=item['fat'],
                    carbs=item['carbs']
                )
            return products
    except FileNotFoundError:
        # Создаем расширенную базу продуктов
        try:
            with open('extended_products.json', 'r', encoding='utf-8') as f:
                basic_products = json.load(f)
        except FileNotFoundError:
            # Если расширенная база не найдена, используем базовую
            basic_products = [
                {"name": "курица", "calories": 165, "protein": 31, "fat": 3.6, "carbs": 0},
                {"name": "гречка", "calories": 343, "protein": 13, "fat": 3.4, "carbs": 72},
                {"name": "рис", "calories": 344, "protein": 7, "fat": 1, "carbs": 78},
                {"name": "яйца", "calories": 157, "protein": 13, "fat": 11, "carbs": 1.1},
                {"name": "творог", "calories": 88, "protein": 18, "fat": 0.6, "carbs": 1.8},
                {"name": "молоко", "calories": 42, "protein": 3.4, "fat": 1, "carbs": 5},
                {"name": "хлеб", "calories": 265, "protein": 9, "fat": 3.2, "carbs": 49},
                {"name": "картофель", "calories": 77, "protein": 2, "fat": 0.1, "carbs": 17},
                {"name": "морковь", "calories": 41, "protein": 0.9, "fat": 0.2, "carbs": 9.6},
                {"name": "яблоко", "calories": 52, "protein": 0.3, "fat": 0.2, "carbs": 14},
                {"name": "банан", "calories": 89, "protein": 1.1, "fat": 0.3, "carbs": 23},
                {"name": "борщ", "calories": 85, "protein": 4.5, "fat": 3.8, "carbs": 8.2},
                {"name": "пельмени", "calories": 275, "protein": 12, "fat": 8, "carbs": 42},
            ]
        
        with open('products.json', 'w', encoding='utf-8') as f:
            json.dump(basic_products, f, ensure_ascii=False, indent=2)
        
        return load_products()

# Загружаем продукты
PRODUCTS_DB = load_products()

# Функции расчета КБЖУ
def calculate_bmr(gender: Gender, weight: float, height: float, age: int) -> float:
    """Расчет базового метаболизма по формуле Миффлина-Сан Жеора"""
    if gender == Gender.MALE:
        return 10 * weight + 6.25 * height - 5 * age + 5
    else:
        return 10 * weight + 6.25 * height - 5 * age - 161

def calculate_daily_calories(bmr: float, activity: Activity, goal: Goal) -> int:
    """Расчет суточных калорий с учетом активности и цели"""
    tdee = bmr * activity.value
    
    if goal == Goal.WEIGHT_LOSS:
        return int(tdee * 0.8)  # 1200-1500 ккал для похудения
    elif goal == Goal.BALANCE:
        return int(tdee * 0.9)  # 1800 ккал для баланса
    else:  # WEIGHT_GAIN
        return int(tdee * 1.1)  # 2400+ ккал для набора массы

def calculate_macros(calories: int, goal: Goal) -> Tuple[float, float, float]:
    """Расчет БЖУ"""
    if goal == Goal.WEIGHT_LOSS:
        protein = calories * 0.3 / 4  # 30% белки
        fat = calories * 0.25 / 9     # 25% жиры
        carbs = calories * 0.45 / 4   # 45% углеводы
    elif goal == Goal.BALANCE:
        protein = calories * 0.25 / 4  # 25% белки
        fat = calories * 0.3 / 9       # 30% жиры
        carbs = calories * 0.45 / 4    # 45% углеводы
    else:  # WEIGHT_GAIN
        protein = calories * 0.25 / 4  # 25% белки
        fat = calories * 0.25 / 9      # 25% жиры
        carbs = calories * 0.5 / 4     # 50% углеводы
    
    return protein, fat, carbs

def get_meals_count(goal: Goal) -> int:
    """Количество приемов пищи в зависимости от цели"""
    if goal == Goal.WEIGHT_GAIN:
        return 5
    return 4

# Функции распределения продуктов
def distribute_products(products: List[Tuple[str, float]], meals_count: int) -> MealPlan:
    """Распределяет продукты по приемам пищи поровну"""
    if not products:
        return MealPlan([], [], [], [])
    
    # Распределяем каждый продукт поровну между всеми приемами пищи
    breakfast = []
    snack = []
    lunch = []
    dinner = []
    second_snack = []
    
    for product_name, total_weight in products:
        # Делим вес продукта поровну между всеми приемами пищи
        weight_per_meal = total_weight / meals_count
        
        # Добавляем продукт во все приемы пищи
        breakfast.append((product_name, weight_per_meal))
        snack.append((product_name, weight_per_meal))
        lunch.append((product_name, weight_per_meal))
        dinner.append((product_name, weight_per_meal))
        
        if meals_count == 5:
            second_snack.append((product_name, weight_per_meal))
    
    return MealPlan(breakfast, snack, lunch, dinner, second_snack if meals_count == 5 else None)

def find_similar_product(product_name: str) -> Optional[str]:
    """Находит похожий продукт в базе данных"""
    product_name_lower = product_name.lower()
    
    # Сначала ищем точное совпадение
    if product_name_lower in PRODUCTS_DB:
        return product_name_lower
    
    # Ищем частичные совпадения
    for db_name in PRODUCTS_DB.keys():
        if product_name_lower in db_name or db_name in product_name_lower:
            return db_name
    
    return None

def calculate_total_calories(products: List[Tuple[str, float]]) -> float:
    """Вычисляет общее количество калорий из всех продуктов"""
    total_calories = 0.0
    for product_name, weight in products:
        # Ищем продукт в базе данных
        db_product_name = find_similar_product(product_name)
        if db_product_name:
            product = PRODUCTS_DB[db_product_name]
            total_calories += product.calories * weight / 100
        else:
            # Если продукт не найден, используем примерную оценку
            # Средняя калорийность продуктов ~100 ккал/100г
            total_calories += 100 * weight / 100
    return total_calories

def suggest_meal_plan_days(products: List[Tuple[str, float]], daily_calories: int) -> int:
    """Предлагает количество дней для распределения продуктов"""
    total_calories = calculate_total_calories(products)
    if total_calories <= daily_calories:
        return 1
    
    # Вычисляем, на сколько дней можно растянуть продукты
    days = max(1, int(total_calories / daily_calories))
    return min(days, 7)  # Максимум 7 дней

def create_multi_day_plan(
    products: List[Tuple[str, float]],
    daily_calories: int,
    meals_count: int,
    calorie_excess_cap: int = 0,
) -> List[MealPlan]:
    """Создает план питания на несколько дней так, чтобы избыток калорий в день
    не превышал daily_calories + calorie_excess_cap. При calorie_excess_cap=0 избыток отсутствует,
    весь остаток уходит в последний день."""

    # Вспомогательная функция: калории на 1 грамм продукта
    def calories_per_gram(product_name: str) -> float:
        db_product_name = find_similar_product(product_name)
        if db_product_name:
            product = PRODUCTS_DB[db_product_name]
            return product.calories / 100.0
        return 100.0 / 100.0  # по умолчанию 100 ккал на 100г

    # Готовим изменяемую копию с остатками веса
    remaining = [
        {
            "name": name,
            "weight": float(weight),
            "kcal_per_g": calories_per_gram(name),
        }
        for name, weight in products
        if weight > 0
    ]

    cap_per_day = float(daily_calories + calorie_excess_cap)
    days: List[List[Tuple[str, float]]] = []  # список дней, каждый день: [(name, grams), ...]

    # Пока остались граммы любых продуктов — продолжаем формировать дни
    while any(item["weight"] > 1e-9 for item in remaining):
        # Сколько калорий всего осталось
        total_remaining_kcal = sum(item["weight"] * item["kcal_per_g"] for item in remaining)
        if total_remaining_kcal <= 1e-9:
            break

        target_kcal = min(cap_per_day, total_remaining_kcal)

        # Изначально распределяем пропорционально доле калорий каждого продукта
        assigned_grams = {item["name"]: 0.0 for item in remaining}

        for item in remaining:
            if item["weight"] <= 1e-9:
                continue
            share_kcal = target_kcal * ((item["weight"] * item["kcal_per_g"]) / total_remaining_kcal)
            grams = min(item["weight"], share_kcal / max(item["kcal_per_g"], 1e-9))
            if grams > 0:
                assigned_grams[item["name"]] += grams

        # Корректируем, чтобы не превышать target_kcal из-за округлений
        day_kcal = sum(
            assigned_grams[name] * next(itm for itm in remaining if itm["name"] == name)["kcal_per_g"]
            for name in assigned_grams
        )

        if day_kcal > target_kcal + 1e-6:
            scale = target_kcal / day_kcal
            for name in assigned_grams:
                assigned_grams[name] *= scale
            day_kcal = target_kcal
        else:
            # Попробуем добрать остаток, не превышая лимит
            residual_kcal = max(0.0, target_kcal - day_kcal)
            if residual_kcal > 1e-6:
                for item in remaining:
                    if residual_kcal <= 1e-6:
                        break
                    if item["weight"] <= 1e-9:
                        continue
                    kcal_per_g = max(item["kcal_per_g"], 1e-9)
                    current_assigned = assigned_grams.get(item["name"], 0.0)
                    can_add_grams = max(0.0, item["weight"] - current_assigned)
                    add_grams = min(can_add_grams, residual_kcal / kcal_per_g)
                    if add_grams > 0:
                        assigned_grams[item["name"]] = current_assigned + add_grams
                        residual_kcal -= add_grams * kcal_per_g
                day_kcal = target_kcal - residual_kcal

        # Применяем списание и фиксируем день
        day_products: List[Tuple[str, float]] = []
        for item in remaining:
            grams = assigned_grams.get(item["name"], 0.0)
            grams = max(0.0, min(grams, item["weight"]))
            if grams > 0:
                day_products.append((item["name"], grams))
                item["weight"] -= grams
                if item["weight"] < 1e-9:
                    item["weight"] = 0.0

        days.append(day_products)

    # Преобразуем каждый день в MealPlan с разбиением по приемам пищи
    daily_plans: List[MealPlan] = []
    for day_products in days:
        daily_plans.append(distribute_products(day_products, meals_count))

    return daily_plans

# Клавиатуры
def get_gender_keyboard() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Мужской", callback_data="gender_male")],
        [InlineKeyboardButton(text="Женский", callback_data="gender_female")]
    ])
    return keyboard

def get_activity_keyboard() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Сидячий образ жизни", callback_data="activity_1.2")],
        [InlineKeyboardButton(text="Легкая активность", callback_data="activity_1.375")],
        [InlineKeyboardButton(text="Умеренная активность", callback_data="activity_1.55")],
        [InlineKeyboardButton(text="Высокая активность", callback_data="activity_1.725")],
        [InlineKeyboardButton(text="Очень высокая активность", callback_data="activity_1.9")]
    ])
    return keyboard

def get_goal_keyboard() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Похудение", callback_data="goal_weight_loss")],
        [InlineKeyboardButton(text="Баланс", callback_data="goal_balance")],
        [InlineKeyboardButton(text="Набор массы", callback_data="goal_weight_gain")]
    ])
    return keyboard

def get_main_menu_keyboard() -> None:
    # Возвращаем None, чтобы убрать кнопки из-под панели ввода
    return None

def get_main_menu_text() -> str:
    """Возвращает текст с доступными командами главного меню"""
    return (
        "Доступные команды:\n\n"
        "📊 Мои данные - просмотр и изменение профиля\n"
        "📋 Составить план - добавление продуктов и составление плана питания\n"
        "🔄 Новый день - очистка корзины и начало нового дня\n\n"
        "💡 Также доступны команды:\n"
        "/start - главное меню\n"
        "/help - показать эту справку\n"
        "/settings - показать ваши данные\n"
        "/plan - составить план питания\n"
        "/newday - начать новый день\n\n"
        "Просто введите нужную команду в чат!"
    )

def get_main_menu_inline_keyboard() -> InlineKeyboardMarkup:
    """Создает inline клавиатуру для главного меню"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Мои данные", callback_data="view_profile")],
        [InlineKeyboardButton(text="📋 Составить план", callback_data="compose_plan_menu")],
        [InlineKeyboardButton(text="🔄 Новый день", callback_data="new_day_inline")]
    ])
    return keyboard

def get_profile_menu_keyboard() -> InlineKeyboardMarkup:
    """Создает клавиатуру для меню профиля"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить данные", callback_data="edit_profile")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    return keyboard

def get_plan_menu_keyboard() -> InlineKeyboardMarkup:
    """Создает клавиатуру для меню составления плана"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Получить план", callback_data="get_plan")],
        [InlineKeyboardButton(text="🛒 Моя корзина", callback_data="view_cart")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    return keyboard

def get_compose_plan_menu_keyboard() -> InlineKeyboardMarkup:
    """Создает клавиатуру для меню составления плана (выбор действия)"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗂 Добавить продукты", callback_data="select_products")],
        [InlineKeyboardButton(text="📋 Составить план", callback_data="get_plan")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    return keyboard

def get_plan_context_keyboard() -> InlineKeyboardMarkup:
    """Создает клавиатуру для контекста составления плана (после добавления продуктов)"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗂 Добавить еще продукты", callback_data="select_products")],
        [InlineKeyboardButton(text="📋 Получить план", callback_data="get_plan")],
        [InlineKeyboardButton(text="🛒 Моя корзина", callback_data="view_cart")],
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")]
    ])
    return keyboard

def get_meal_plan_keyboard() -> InlineKeyboardMarkup:
    """Создает клавиатуру для плана питания"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗂 Добавить продукты", callback_data="select_products")],
        [InlineKeyboardButton(text="🛒 Моя корзина", callback_data="view_cart")],
        [InlineKeyboardButton(text="🔄 Новый день", callback_data="new_day_inline")],
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")]
    ])
    return keyboard

def get_category_inline_keyboard() -> InlineKeyboardMarkup:
    """Создает inline клавиатуру с категориями продуктов"""
    keyboard = []
    categories = get_category_keyboard()
    
    for row in categories:
        keyboard_row = []
        for category in row:
            keyboard_row.append(InlineKeyboardButton(text=category, callback_data=f"category_{category}"))
        keyboard.append(keyboard_row)
    
    # Добавляем кнопку "Назад"
    keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_compose_plan")])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_products_inline_keyboard(category: str) -> InlineKeyboardMarkup:
    """Создает inline клавиатуру с продуктами в категории"""
    keyboard = []
    products = get_products_in_category(category)
    
    # Размещаем по 2 продукта в ряд
    for i in range(0, len(products), 2):
        row = []
        product_name, calories = products[i]
        row.append(InlineKeyboardButton(
            text=f"{product_name} ({calories})", 
            callback_data=f"product_{product_name}"
        ))
        
        if i + 1 < len(products):
            product_name2, calories2 = products[i + 1]
            row.append(InlineKeyboardButton(
                text=f"{product_name2} ({calories2})", 
                callback_data=f"product_{product_name2}"
            ))
        
        keyboard.append(row)
    
    # Добавляем кнопки навигации
    keyboard.append([
        InlineKeyboardButton(text="🔙 К категориям", callback_data="back_to_categories"),
        InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_main")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# Обработчики команд
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    if not message.from_user:
        return
    user_id = message.from_user.id
    
    # Пытаемся загрузить пользователя из базы данных
    user = await load_user_from_db(user_id)
    
    if user:
        # Загружаем продукты пользователя
        await load_user_products_from_db(user_id)
        
        await message.answer(
            "👋 С возвращением!\n\n" + get_main_menu_text(),
            reply_markup=get_main_menu_inline_keyboard()
        )
    else:
        await message.answer(
            "👋 Добро пожаловать в бота 'Умный рацион'!\n\n"
            "Я помогу вам составить план питания на основе ваших целей и доступных продуктов.\n\n"
            "Для начала давайте познакомимся. Выберите ваш пол:",
            reply_markup=get_gender_keyboard()
        )
        await state.set_state(UserStates.waiting_for_gender)



@dp.message(Command("plan"))
async def cmd_plan(message: types.Message):
    if not message.from_user:
        return
    user_id = message.from_user.id
    
    # Загружаем пользователя из базы данных
    user = await load_user_from_db(user_id)
    if not user:
        await message.answer("Сначала пройдите регистрацию с помощью /start")
        return
    
    # Загружаем продукты пользователя
    products = await load_user_products_from_db(user_id)
    if not products:
        await message.answer(
            "У вас нет добавленных продуктов. Сначала добавьте продукты, выбрав их из категорий.",
            reply_markup=get_main_menu_inline_keyboard()
        )
        return
    
    await generate_meal_plan(message, user_id)

@dp.message(Command("settings"))
async def cmd_settings(message: types.Message):
    if not message.from_user:
        return
    user_id = message.from_user.id
    
    # Загружаем пользователя из базы данных
    user = await load_user_from_db(user_id)
    if not user:
        await message.answer("Сначала пройдите регистрацию с помощью /start")
        return
    
    # Преобразуем названия активности в понятные пользователю
    activity_names = {
        Activity.SEDENTARY: "Сидячий образ жизни",
        Activity.LIGHT: "Легкая активность", 
        Activity.MODERATE: "Умеренная активность",
        Activity.ACTIVE: "Высокая активность",
        Activity.VERY_ACTIVE: "Очень высокая активность"
    }
    
    # Преобразуем названия целей в понятные пользователю
    goal_names = {
        Goal.WEIGHT_LOSS: "Похудение",
        Goal.BALANCE: "Баланс",
        Goal.WEIGHT_GAIN: "Набор массы"
    }
    
    await message.answer(
        f"�� Ваши данные:\n\n"
        f"Пол: {'Мужской' if user.gender == Gender.MALE else 'Женский'}\n"
        f"Возраст: {user.age} лет\n"
        f"Вес: {user.weight} кг\n"
        f"Рост: {user.height} см\n"
        f"Активность: {activity_names[user.activity]}\n"
        f"Цель: {goal_names[user.goal]}\n\n"
        f"Суточная норма:\n"
        f"• Калории: {user.daily_calories} ккал\n"
        f"• Белки: {user.protein:.1f} г\n"
        f"• Жиры: {user.fat:.1f} г\n"
        f"• Углеводы: {user.carbs:.1f} г\n"
        f"• Приемов пищи: {user.meals_count}"
    )

@dp.message(Command("newday"))
async def cmd_newday(message: types.Message):
    await new_day(message)

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(get_main_menu_text())

# Обработчики callback'ов
@dp.callback_query(lambda c: c.data.startswith('gender_'))
async def process_gender_selection(callback: types.CallbackQuery, state: FSMContext):
    if not callback.data:
        return
    gender_str = callback.data.split('_')[1]
    gender = Gender.MALE if gender_str == 'male' else Gender.FEMALE
    
    await state.update_data(gender=gender)
    if callback.message and isinstance(callback.message, Message):
        await callback.message.answer("Введите ваш возраст (полных лет):")
    await state.set_state(UserStates.waiting_for_age)
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith('activity_'))
async def process_activity_selection(callback: types.CallbackQuery, state: FSMContext):
    if not callback.data:
        return
    activity_value = float(callback.data.split('_')[1])
    activity = Activity(activity_value)
    
    await state.update_data(activity=activity)
    if callback.message and isinstance(callback.message, Message):
        await callback.message.answer("Выберите вашу цель:", reply_markup=get_goal_keyboard())
    await state.set_state(UserStates.waiting_for_goal)
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith('goal_'))
async def process_goal_selection(callback: types.CallbackQuery, state: FSMContext):
    if not callback.data:
        return
    # Убираем префикс 'goal_' и получаем полное название цели
    goal_str = callback.data.replace('goal_', '')
    
    if goal_str == 'weight_loss':
        goal = Goal.WEIGHT_LOSS
    elif goal_str == 'balance':
        goal = Goal.BALANCE
    elif goal_str == 'weight_gain':
        goal = Goal.WEIGHT_GAIN
    else:
        # Если что-то пошло не так, логируем для отладки
        print(f"Неизвестная цель: {goal_str}")
        goal = Goal.WEIGHT_LOSS  # По умолчанию похудение
    
    # Получаем все данные пользователя
    data = await state.get_data()
    
    # Создаем профиль пользователя
    user_id = callback.from_user.id
    user = UserProfile(
        user_id=user_id,
        gender=data['gender'],
        age=data['age'],
        weight=data['weight'],
        height=data['height'],
        activity=data['activity'],
        goal=goal
    )
    
    # Рассчитываем КБЖУ
    bmr = calculate_bmr(user.gender, user.weight, user.height, user.age)
    user.daily_calories = calculate_daily_calories(bmr, user.activity, user.goal)
    user.protein, user.fat, user.carbs = calculate_macros(user.daily_calories, user.goal)
    user.meals_count = get_meals_count(user.goal)
    
    # Сохраняем пользователя в базу данных
    await save_user_to_db(user)
    user_products[user_id] = []
    
    # Преобразуем названия целей в понятные пользователю
    goal_names = {
        Goal.WEIGHT_LOSS: "Похудение",
        Goal.BALANCE: "Баланс", 
        Goal.WEIGHT_GAIN: "Набор массы"
    }
    
    if callback.message and isinstance(callback.message, Message):
        await callback.message.answer(
            f"✅ Регистрация завершена!\n\n"
            f"Ваша цель: {goal_names[user.goal]}\n"
            f"Приемов пищи в день: {user.meals_count}\n\n"
            f"Ваши суточные нормы:\n"
            f"• Калории: {user.daily_calories} ккал\n"
            f"• Белки: {user.protein:.1f} г\n"
            f"• Жиры: {user.fat:.1f} г\n"
            f"• Углеводы: {user.carbs:.1f} г\n\n"
            f"Выберите действие:",
            reply_markup=get_main_menu_inline_keyboard()
        )
    await state.clear()
    await callback.answer()

# Обработчики состояний
@dp.message(UserStates.waiting_for_age)
async def process_age(message: types.Message, state: FSMContext):
    if not message.text:
        return
    try:
        age = int(message.text)
        if age < 10 or age > 100:
            await message.answer("Пожалуйста, введите корректный возраст (10-100 лет):")
            return
        
        await state.update_data(age=age)
        await message.answer("Введите ваш вес (в кг):")
        await state.set_state(UserStates.waiting_for_weight)
    except ValueError:
        await message.answer("Пожалуйста, введите число:")

@dp.message(UserStates.waiting_for_weight)
async def process_weight(message: types.Message, state: FSMContext):
    if not message.text:
        return
    try:
        weight = float(message.text)
        if weight < 30 or weight > 300:
            await message.answer("Пожалуйста, введите корректный вес (30-300 кг):")
            return
        
        await state.update_data(weight=weight)
        await message.answer("Введите ваш рост (в см):")
        await state.set_state(UserStates.waiting_for_height)
    except ValueError:
        await message.answer("Пожалуйста, введите число:")

@dp.message(UserStates.waiting_for_height)
async def process_height(message: types.Message, state: FSMContext):
    if not message.text:
        return
    try:
        height = float(message.text)
        if height < 100 or height > 250:
            await message.answer("Пожалуйста, введите корректный рост (100-250 см):")
            return
        
        await state.update_data(height=height)
        await message.answer("Выберите уровень вашей физической активности:", reply_markup=get_activity_keyboard())
        await state.set_state(UserStates.waiting_for_activity)
    except ValueError:
        await message.answer("Пожалуйста, введите число:")



# Обработчики callback'ов для категорий и продуктов
@dp.callback_query(lambda c: c.data.startswith('category_'))
async def process_category_selection(callback: types.CallbackQuery):
    if not callback.data or not callback.message or not isinstance(callback.message, Message):
        return
    category = callback.data.replace('category_', '')
    
    await callback.message.edit_text(
        f"Выберите продукт из категории '{category}':",
        reply_markup=get_products_inline_keyboard(category)
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "back_to_categories")
async def back_to_categories(callback: types.CallbackQuery):
    if not callback.message or not isinstance(callback.message, Message):
        return
    await callback.message.edit_text(
        "Выберите категорию продуктов:",
        reply_markup=get_category_inline_keyboard()
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "back_to_main")
async def back_to_main_menu(callback: types.CallbackQuery):
    if not callback.message or not isinstance(callback.message, Message):
        return
    await callback.message.edit_text(
        "👋 " + get_main_menu_text(),
        reply_markup=get_main_menu_inline_keyboard()
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "back_to_compose_plan")
async def back_to_compose_plan(callback: types.CallbackQuery):
    if not callback.message or not isinstance(callback.message, Message):
        return
    await callback.message.edit_text(
        "📋 Составление плана питания\n\nВыберите действие:",
        reply_markup=get_compose_plan_menu_keyboard()
    )
    await callback.answer()

# Обработчики callback'ов для продуктов
@dp.callback_query(lambda c: c.data.startswith('product_'))
async def process_product_selection(callback: types.CallbackQuery, state: FSMContext):
    if not callback.data or not callback.message or not isinstance(callback.message, Message):
        return
    product_name = callback.data.split('_', 1)[1]
    
    # Проверяем, есть ли продукт в базе
    if product_name.lower() in PRODUCTS_DB:
        await state.update_data(product_name=product_name)
        await callback.message.answer(f"Введите количество в граммах:")
        await state.set_state(UserStates.waiting_for_product_weight)
    else:
        # Если продукт не найден в базе, предлагаем выбрать из категорий
        await callback.message.answer(
            f"Продукт '{product_name}' не найден в базе. Попробуйте выбрать продукт из категорий."
        )
    
    await callback.answer()

@dp.callback_query(lambda c: c.data == "bulk_add_products")
async def bulk_add_products_callback(callback: types.CallbackQuery, state: FSMContext):
    # Кнопка больше не используется — возвращаем в меню составления плана
    if not callback.message or not isinstance(callback.message, Message):
        return
    await callback.message.edit_text(
        "📋 Составление плана питания\n\nВыберите действие:",
        reply_markup=get_compose_plan_menu_keyboard()
    )
    await callback.answer()

# Обработчики для нового дня
@dp.callback_query(lambda c: c.data == "clear_products")
async def clear_products_callback(callback: types.CallbackQuery):
    if not callback.message or not isinstance(callback.message, Message):
        return
    user_id = callback.from_user.id
    await clear_user_products(user_id)
    
    await callback.message.edit_text(
        "🔄 Новый день начат! Корзина очищена.\n\n" + get_main_menu_text(),
        reply_markup=get_main_menu_inline_keyboard()
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "get_plan_first")
async def get_plan_first_callback(callback: types.CallbackQuery):
    if not callback.message or not isinstance(callback.message, Message):
        return
    user_id = callback.from_user.id
    await callback.message.delete()
    await generate_meal_plan(callback.message, user_id)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "cancel_new_day")
async def cancel_new_day_callback(callback: types.CallbackQuery):
    if not callback.message or not isinstance(callback.message, Message):
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")]
    ])
    
    await callback.message.edit_text(
        "❌ Действие отменено. Ваши продукты остались без изменений.",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "new_day_inline")
async def new_day_inline_callback(callback: types.CallbackQuery):
    if not callback.message or not isinstance(callback.message, Message):
        return
    user_id = callback.from_user.id
    
    # Загружаем пользователя из базы данных
    user = await load_user_from_db(user_id)
    if not user:
        await callback.message.edit_text("Сначала пройдите регистрацию с помощью /start")
        await callback.answer()
        return
    
    # Загружаем продукты и проверяем, есть ли текущие продукты
    products = await load_user_products_from_db(user_id)
    current_products_count = len(products)
    
    if current_products_count > 0:
        # Показываем текущие продукты и предлагаем очистить
        summary = get_user_products_summary(user_id)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Очистить и начать новый день", callback_data="clear_products")],
            [InlineKeyboardButton(text="📋 Сначала получить план", callback_data="get_plan_first")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_new_day")]
        ])
        
        await callback.message.edit_text(
            f"🔄 Начать новый день?\n\n{summary}\n\n"
            f"Выберите действие:",
            reply_markup=keyboard
        )
    else:
        # Если продуктов нет, сразу очищаем и предлагаем добавить
        await clear_user_products(user_id)
        await callback.message.edit_text(
            "🔄 Новый день начат! Корзина очищена.\n\n" + get_main_menu_text(),
            reply_markup=get_main_menu_inline_keyboard()
        )
    
    await callback.answer()

# Обработчики для нового меню
@dp.callback_query(lambda c: c.data == "view_profile")
async def view_profile_callback(callback: types.CallbackQuery):
    if not callback.message or not isinstance(callback.message, Message):
        return
    user_id = callback.from_user.id
    
    # Загружаем пользователя из базы данных
    user = await load_user_from_db(user_id)
    if not user:
        await callback.message.edit_text("Сначала пройдите регистрацию с помощью /start")
        await callback.answer()
        return
    
    # Преобразуем названия активности в понятные пользователю
    activity_names = {
        Activity.SEDENTARY: "Сидячий образ жизни",
        Activity.LIGHT: "Легкая активность", 
        Activity.MODERATE: "Умеренная активность",
        Activity.ACTIVE: "Высокая активность",
        Activity.VERY_ACTIVE: "Очень высокая активность"
    }
    
    # Преобразуем названия целей в понятные пользователю
    goal_names = {
        Goal.WEIGHT_LOSS: "Похудение",
        Goal.BALANCE: "Баланс",
        Goal.WEIGHT_GAIN: "Набор массы"
    }
    
    profile_text = (
        f"📊 Ваши данные:\n\n"
        f"Пол: {'Мужской' if user.gender == Gender.MALE else 'Женский'}\n"
        f"Возраст: {user.age} лет\n"
        f"Вес: {user.weight} кг\n"
        f"Рост: {user.height} см\n"
        f"Активность: {activity_names[user.activity]}\n"
        f"Цель: {goal_names[user.goal]}\n\n"
        f"Суточная норма:\n"
        f"• Калории: {user.daily_calories} ккал\n"
        f"• Белки: {user.protein:.1f} г\n"
        f"• Жиры: {user.fat:.1f} г\n"
        f"• Углеводы: {user.carbs:.1f} г\n"
        f"• Приемов пищи: {user.meals_count}"
    )
    
    await callback.message.edit_text(
        profile_text,
        reply_markup=get_profile_menu_keyboard()
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "edit_profile")
async def edit_profile_callback(callback: types.CallbackQuery, state: FSMContext):
    if not callback.message or not isinstance(callback.message, Message):
        return
    await callback.message.edit_text(
        "✏️ Изменение данных профиля\n\n"
        "Выберите ваш пол:",
        reply_markup=get_gender_keyboard()
    )
    await state.set_state(UserStates.waiting_for_gender)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "compose_plan_menu")
async def compose_plan_menu_callback(callback: types.CallbackQuery):
    if not callback.message or not isinstance(callback.message, Message):
        return
    await callback.message.edit_text(
        "📋 Составление плана питания\n\n"
        "Выберите действие:",
        reply_markup=get_compose_plan_menu_keyboard()
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "select_products")
async def select_products_callback(callback: types.CallbackQuery):
    if not callback.message or not isinstance(callback.message, Message):
        return
    await callback.message.edit_text(
        "🗂 Выберите категорию продуктов:",
        reply_markup=get_category_inline_keyboard()
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "get_plan")
async def get_plan_callback(callback: types.CallbackQuery):
    if not callback.message or not isinstance(callback.message, Message):
        return
    user_id = callback.from_user.id
    
    # Загружаем пользователя из базы данных
    user = await load_user_from_db(user_id)
    if not user:
        await callback.message.edit_text(
            "❌ Ваши данные не найдены. Пожалуйста, пройдите регистрацию заново с помощью /start",
            reply_markup=get_main_menu_inline_keyboard()
        )
        await callback.answer()
        return
    
    # Загружаем продукты пользователя
    products = await load_user_products_from_db(user_id)
    if not products:
        await callback.message.edit_text(
            "📋 У вас нет добавленных продуктов.\n\n"
            "Сначала добавьте продукты, выбрав их из категорий.",
            reply_markup=get_plan_menu_keyboard()
        )
        await callback.answer()
        return
    
    await callback.message.delete()
    await generate_meal_plan(callback.message, user_id)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "view_cart")
async def view_cart_callback(callback: types.CallbackQuery):
    if not callback.message or not isinstance(callback.message, Message):
        return
    user_id = callback.from_user.id
    
    # Загружаем продукты пользователя из базы данных
    products = await load_user_products_from_db(user_id)
    if not products:
        await callback.message.edit_text(
            "🛒 Ваша корзина пуста.\n\n"
            "📋 Продолжайте составление плана питания:",
            reply_markup=get_plan_context_keyboard()
        )
        await callback.answer()
        return
    
    summary = get_user_products_summary(user_id)
    keyboard = get_products_management_keyboard(user_id)
    
    await callback.message.edit_text(
        f"{summary}\n\n"
        f"Выберите продукт для удаления или очистите всю корзину:",
        reply_markup=keyboard
    )
    await callback.answer()

# Обработчики для управления продуктами
@dp.callback_query(lambda c: c.data.startswith('remove_'))
async def remove_product_callback(callback: types.CallbackQuery):
    if not callback.data or not callback.message or not isinstance(callback.message, Message):
        return
    user_id = callback.from_user.id
    product_name = callback.data.replace('remove_', '')
    
    if await remove_product_from_user(user_id, product_name):
        if product_name.lower() in PRODUCTS_DB:
            product = PRODUCTS_DB[product_name.lower()]
            await callback.answer(f"✅ {product.name} удален из корзины")
        else:
            await callback.answer("✅ Продукт удален из корзины")
        
        # Обновляем сообщение
        if user_id in user_products and user_products[user_id]:
            summary = get_user_products_summary(user_id)
            keyboard = get_products_management_keyboard(user_id)
            
            await callback.message.edit_text(
                f"{summary}\n\n"
                f"Выберите продукт для удаления или очистите всю корзину:",
                reply_markup=keyboard
            )
        else:
            await callback.message.edit_text(
                "🛒 Ваша корзина пуста.\n\n"
                "�� Продолжайте составление плана питания:",
                reply_markup=get_plan_context_keyboard()
            )
    else:
        await callback.answer("❌ Продукт не найден")

@dp.callback_query(lambda c: c.data == "clear_all_products")
async def clear_all_products_callback(callback: types.CallbackQuery):
    if not callback.message or not isinstance(callback.message, Message):
        return
    user_id = callback.from_user.id
    await clear_user_products(user_id)
    
    await callback.message.edit_text(
        "🗑️ Корзина очищена!\n\n"
        "📋 Продолжайте составление плана питания:",
        reply_markup=get_plan_context_keyboard()
    )
    await callback.answer()

@dp.message(UserStates.waiting_for_product_weight)
async def process_product_weight(message: types.Message, state: FSMContext):
    if not message.text or not message.from_user:
        return
    try:
        weight = float(message.text)
        if weight <= 0 or weight > 10000:
            await message.answer("Пожалуйста, введите корректное количество (1-10000 г):")
            return
        
        data = await state.get_data()
        product_name = data['product_name']
        user_id = message.from_user.id
        
        # Добавляем продукт с объединением одинаковых
        await add_product_to_user(user_id, product_name, weight)
        
        product = PRODUCTS_DB[product_name.lower()]
        # Проверяем, был ли продукт объединен или добавлен новый
        total_weight = 0.0
        for name, existing_weight in user_products[user_id]:
            if name.lower() == product_name.lower():
                total_weight = existing_weight
                break
        
        if total_weight == weight:
            # Новый продукт
            message_text = f"✅ Добавлен продукт: {product.name} - {weight} г\n"
        else:
            # Продукт объединен
            message_text = f"✅ Добавлен продукт: {product.name} - {weight} г\n"
            message_text += f"📊 Общий вес {product.name}: {total_weight} г\n"
        
        message_text += f"Калории: {product.calories * weight / 100:.1f} ккал\n\n"
        message_text += f"Всего продуктов: {len(user_products[user_id])}\n\n"
        message_text += f"📋 Продолжайте составление плана питания:"
        
        await message.answer(
            message_text,
            reply_markup=get_plan_context_keyboard()
        )
        await state.clear()
    except ValueError:
        await message.answer("Пожалуйста, введите число:")

# Обработчики текстовых сообщений
@dp.message(F.text == "📊 Мои данные")
async def show_profile(message: types.Message):
    if not message.from_user:
        return
    user_id = message.from_user.id
    if user_id not in users:
        await message.answer("Сначала пройдите регистрацию с помощью /start")
        return
    
    await message.answer(
        "📊 Управление профилем\n\n"
        "Выберите действие:",
        reply_markup=get_profile_menu_keyboard()
    )



@dp.message(F.text == "📋 Составить план")
async def compose_plan(message: types.Message):
    if not message.from_user:
        return
    user_id = message.from_user.id
    if user_id not in users:
        await message.answer("Сначала пройдите регистрацию с помощью /start")
        return
    
    await message.answer(
        "📋 Составление плана питания\n\n"
        "Выберите действие:",
        reply_markup=get_compose_plan_menu_keyboard()
    )





@dp.message(F.text == "🔄 Новый день")
async def new_day(message: types.Message):
    if not message.from_user:
        return
    user_id = message.from_user.id
    if user_id not in users:
        await message.answer("Сначала пройдите регистрацию с помощью /start")
        return
    
    # Проверяем, есть ли текущие продукты
    current_products_count = len(user_products.get(user_id, []))
    
    if current_products_count > 0:
        # Показываем текущие продукты и предлагаем очистить
        summary = get_user_products_summary(user_id)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Очистить и начать новый день", callback_data="clear_products")],
            [InlineKeyboardButton(text="📋 Сначала получить план", callback_data="get_plan_first")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_new_day")]
        ])
        
        await message.answer(
            f"🔄 Начать новый день?\n\n{summary}\n\n"
            f"Выберите действие:",
            reply_markup=keyboard
        )
    else:
        # Если продуктов нет, сразу очищаем и предлагаем добавить
        await clear_user_products(user_id)
        await message.answer(
            "🔄 Новый день начат! Корзина очищена.\n\n" + get_main_menu_text(),
            reply_markup=get_main_menu_inline_keyboard()
        )

# Функция генерации плана питания
async def generate_meal_plan(message: types.Message, user_id: int):
    # Проверяем, существует ли пользователь
    if user_id not in users:
        await message.answer(
            "❌ Ваши данные не найдены. Пожалуйста, пройдите регистрацию заново с помощью /start",
            reply_markup=get_main_menu_inline_keyboard()
        )
        return
    
    user = users[user_id]
    products = user_products.get(user_id, [])
    print(f"[DEBUG] user_id: {user_id}")
    print(f"[DEBUG] user: {user}")
    print(f"[DEBUG] products: {products}")
    
    # Вычисляем общие калории
    total_calories = calculate_total_calories(products)
    print(f"[DEBUG] total_calories: {total_calories}")
    
    # Преобразуем названия целей в понятные пользователю
    goal_names = {
        Goal.WEIGHT_LOSS: "Похудение",
        Goal.BALANCE: "Баланс",
        Goal.WEIGHT_GAIN: "Набор массы"
    }
    
    # Проверяем, не слишком ли много продуктов
    multi_day_plan_created = False
    if total_calories > user.daily_calories * 1.5:
        # Формируем план на несколько дней без избытка калорий на день
        daily_plans = create_multi_day_plan(
            products=products,
            daily_calories=user.daily_calories,
            meals_count=user.meals_count,
            calorie_excess_cap=0,
        )
        suggested_days = len(daily_plans)
        print(f"[DEBUG] suggested_days: {suggested_days}")

        if suggested_days > 1:
            plan_text = f"⚠️ У вас слишком много продуктов!\n\n"
            plan_text += f"Общие калории: {total_calories:.1f} ккал\n"
            plan_text += f"Ваша дневная норма: {user.daily_calories} ккал\n\n"
            plan_text += f"Распределяю продукты на {suggested_days} дн. без избытка калорий в день.\n\n"

            multi_day_plan_created = True

            for day in range(suggested_days):
                plan_text += f"📅 День {day + 1}:\n"
                plan_text += f"Цель: {goal_names[user.goal]}\n"
                plan_text += f"Приемов пищи: {user.meals_count}\n\n"
                
                daily_plan = daily_plans[day]
                print(f"[DEBUG] daily_plan[{day}]: {daily_plan}")
                
                # Завтрак
                plan_text += "🌅 Завтрак:\n"
                if daily_plan.breakfast:
                    for product_name, weight in daily_plan.breakfast:
                        db_product_name = find_similar_product(product_name)
                        if db_product_name:
                            product = PRODUCTS_DB[db_product_name]
                            calories = product.calories * weight / 100
                            plan_text += f"• {product.name} - {weight:.0f}г ({calories:.1f} ккал)\n"
                        else:
                            calories = 100 * weight / 100
                            plan_text += f"• {product_name} - {weight:.0f}г (~{calories:.1f} ккал)\n"
                else:
                    plan_text += "• Нет продуктов\n"
                
                # Перекус
                plan_text += "\n🍎 Перекус:\n"
                if daily_plan.snack:
                    for product_name, weight in daily_plan.snack:
                        db_product_name = find_similar_product(product_name)
                        if db_product_name:
                            product = PRODUCTS_DB[db_product_name]
                            calories = product.calories * weight / 100
                            plan_text += f"• {product.name} - {weight:.0f}г ({calories:.1f} ккал)\n"
                        else:
                            calories = 100 * weight / 100
                            plan_text += f"• {product_name} - {weight:.0f}г (~{calories:.1f} ккал)\n"
                else:
                    plan_text += "• Нет продуктов\n"
                
                # Обед
                plan_text += "\n🍽 Обед:\n"
                if daily_plan.lunch:
                    for product_name, weight in daily_plan.lunch:
                        db_product_name = find_similar_product(product_name)
                        if db_product_name:
                            product = PRODUCTS_DB[db_product_name]
                            calories = product.calories * weight / 100
                            plan_text += f"• {product.name} - {weight:.0f}г ({calories:.1f} ккал)\n"
                        else:
                            calories = 100 * weight / 100
                            plan_text += f"• {product_name} - {weight:.0f}г (~{calories:.1f} ккал)\n"
                else:
                    plan_text += "• Нет продуктов\n"
                
                # Ужин
                plan_text += "\n🌙 Ужин:\n"
                if daily_plan.dinner:
                    for product_name, weight in daily_plan.dinner:
                        db_product_name = find_similar_product(product_name)
                        if db_product_name:
                            product = PRODUCTS_DB[db_product_name]
                            calories = product.calories * weight / 100
                            plan_text += f"• {product.name} - {weight:.0f}г ({calories:.1f} ккал)\n"
                        else:
                            calories = 100 * weight / 100
                            plan_text += f"• {product_name} - {weight:.0f}г (~{calories:.1f} ккал)\n"
                else:
                    plan_text += "• Нет продуктов\n"
                
                # Второй перекус (если 5 приемов пищи)
                if daily_plan.second_snack:
                    plan_text += "\n🍎 Второй перекус:\n"
                    for product_name, weight in daily_plan.second_snack:
                        db_product_name = find_similar_product(product_name)
                        if db_product_name:
                            product = PRODUCTS_DB[db_product_name]
                            calories = product.calories * weight / 100
                            plan_text += f"• {product.name} - {weight:.0f}г ({calories:.1f} ккал)\n"
                        else:
                            calories = 100 * weight / 100
                            plan_text += f"• {product_name} - {weight:.0f}г (~{calories:.1f} ккал)\n"
                
                # Калории за день
                daily_calories = int(sum(
                    (PRODUCTS_DB[db_name].calories if (db_name := find_similar_product(product_name)) else 100) * weight / 100
                    for product_name, weight in daily_plan.breakfast + daily_plan.snack + daily_plan.lunch + daily_plan.dinner + (daily_plan.second_snack or [])
                ))
                
                # Показываем избыток/недостаток калорий для этого дня
                if daily_calories < user.daily_calories - 100:
                    plan_text += f"\n📊 Калорий за день: {daily_calories:.1f} / {user.daily_calories} (недостаточно)\n"
                elif daily_calories > user.daily_calories + 200:
                    excess = daily_calories - user.daily_calories
                    plan_text += f"\n📊 Калорий за день: {daily_calories:.1f} / {user.daily_calories} (избыток: {excess:.1f} ккал)\n"
                else:
                    plan_text += f"\n📊 Калорий за день: {daily_calories:.1f} / {user.daily_calories}\n"
                
                if day < suggested_days - 1:
                    plan_text += "\n" + "─" * 32 + "\n\n"
            
            # Пояснение
            plan_text += "\n💡 Продукты распределены так, что избытка калорий по дням нет; весь остаток перенесен на последний день."
            
        else:
            # Если все еще слишком много, показываем предупреждение
            plan_text = f"⚠️ Слишком много продуктов!\n\n"
            plan_text += f"Общие калории: {total_calories:.1f} ккал\n"
            plan_text += f"Ваша дневная норма: {user.daily_calories} ккал\n\n"
            plan_text += "Рекомендую уменьшить количество продуктов."
        
        await message.answer(plan_text, reply_markup=get_meal_plan_keyboard())
        return
    
    # Обычный план на один день
    meal_plan = distribute_products(products, user.meals_count)
    print(f"[DEBUG] meal_plan: {meal_plan}")
    
    # Формируем сообщение
    plan_text = f"🍽 План питания на день\n\n"
    plan_text += f"Цель: {goal_names[user.goal]}\n"
    plan_text += f"Ваша норма: {user.daily_calories} ккал\n"
    plan_text += f"Приемов пищи: {user.meals_count}\n\n"
    
    # Завтрак
    print(f"[DEBUG] meal_plan.breakfast: {meal_plan.breakfast}")
    plan_text += "🌅 Завтрак:\n"
    if meal_plan.breakfast:
        for product_name, weight in meal_plan.breakfast:
            db_product_name = find_similar_product(product_name)
            if db_product_name:
                product = PRODUCTS_DB[db_product_name]
                calories = product.calories * weight / 100
                plan_text += f"• {product.name} - {weight:.0f}г ({calories:.1f} ккал)\n"
            else:
                calories = 100 * weight / 100
                plan_text += f"• {product_name} - {weight:.0f}г (~{calories:.1f} ккал)\n"
    else:
        plan_text += "• Нет продуктов\n"
    
    # Перекус
    print(f"[DEBUG] meal_plan.snack: {meal_plan.snack}")
    plan_text += "\n🍎 Перекус:\n"
    if meal_plan.snack:
        for product_name, weight in meal_plan.snack:
            db_product_name = find_similar_product(product_name)
            if db_product_name:
                product = PRODUCTS_DB[db_product_name]
                calories = product.calories * weight / 100
                plan_text += f"• {product.name} - {weight:.0f}г ({calories:.1f} ккал)\n"
            else:
                calories = 100 * weight / 100
                plan_text += f"• {product_name} - {weight:.0f}г (~{calories:.1f} ккал)\n"
    else:
        plan_text += "• Нет продуктов\n"
    
    # Обед
    print(f"[DEBUG] meal_plan.lunch: {meal_plan.lunch}")
    plan_text += "\n🍽 Обед:\n"
    if meal_plan.lunch:
        for product_name, weight in meal_plan.lunch:
            db_product_name = find_similar_product(product_name)
            if db_product_name:
                product = PRODUCTS_DB[db_product_name]
                calories = product.calories * weight / 100
                plan_text += f"• {product.name} - {weight:.0f}г ({calories:.1f} ккал)\n"
            else:
                calories = 100 * weight / 100
                plan_text += f"• {product_name} - {weight:.0f}г (~{calories:.1f} ккал)\n"
    else:
        plan_text += "• Нет продуктов\n"
    
    # Ужин
    print(f"[DEBUG] meal_plan.dinner: {meal_plan.dinner}")
    plan_text += "\n🌙 Ужин:\n"
    if meal_plan.dinner:
        for product_name, weight in meal_plan.dinner:
            db_product_name = find_similar_product(product_name)
            if db_product_name:
                product = PRODUCTS_DB[db_product_name]
                calories = product.calories * weight / 100
                plan_text += f"• {product.name} - {weight:.0f}г ({calories:.1f} ккал)\n"
            else:
                calories = 100 * weight / 100
                plan_text += f"• {product_name} - {weight:.0f}г (~{calories:.1f} ккал)\n"
    else:
        plan_text += "• Нет продуктов\n"
    
    # Второй перекус (если 5 приемов пищи)
    if meal_plan.second_snack:
        plan_text += "\n🍎 Второй перекус:\n"
        for product_name, weight in meal_plan.second_snack:
            db_product_name = find_similar_product(product_name)
            if db_product_name:
                product = PRODUCTS_DB[db_product_name]
                calories = product.calories * weight / 100
                plan_text += f"• {product.name} - {weight:.0f}г ({calories:.1f} ккал)\n"
            else:
                calories = 100 * weight / 100
                plan_text += f"• {product_name} - {weight:.0f}г (~{calories:.1f} ккал)\n"
    
    plan_text += f"\n📊 Итого калорий: {total_calories:.1f} / {user.daily_calories}"
    
    # Не показываем предупреждения, если уже был создан многодневный план
    if not multi_day_plan_created:
        if total_calories < user.daily_calories - 100:
            plan_text += "\n⚠️ Недостаточно калорий! Добавьте еще продуктов для разнообразия."
        elif total_calories > user.daily_calories + 200:
            plan_text += "\n⚠️ Слишком много калорий! Рекомендую уменьшить количество продуктов."
    
    await message.answer(plan_text, reply_markup=get_meal_plan_keyboard())

# Запуск бота
async def main():
    try:
        # Инициализируем базу данных
        await db_service.initialize()
        
        # Запускаем бота
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"Ошибка запуска бота: {e}")
    finally:
        # Закрываем соединение с базой данных
        await db_service.close()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main()) 
