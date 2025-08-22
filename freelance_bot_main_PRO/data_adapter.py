"""
Адаптер для преобразования данных между старыми и новыми моделями
"""
from typing import Optional
from dataclasses import dataclass
from enum import Enum

# Импорт старых моделей из bot.py для совместимости
from models import User as SQLUser, Gender, Activity, Goal


@dataclass
class UserProfile:
    """Старая модель UserProfile для совместимости"""
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

    @classmethod
    def from_sql_user(cls, sql_user: SQLUser) -> 'UserProfile':
        """Создает UserProfile из SQLAlchemy модели User"""
        return cls(
            user_id=sql_user.user_id,
            gender=sql_user.gender,
            age=sql_user.age,
            weight=sql_user.weight,
            height=sql_user.height,
            activity=sql_user.activity,
            goal=sql_user.goal,
            daily_calories=sql_user.daily_calories,
            protein=sql_user.protein,
            fat=sql_user.fat,
            carbs=sql_user.carbs,
            meals_count=sql_user.meals_count
        )

    def to_dict(self) -> dict:
        """Преобразует UserProfile в словарь для SQLAlchemy"""
        return {
            'user_id': self.user_id,
            'gender': self.gender,
            'age': self.age,
            'weight': self.weight,
            'height': self.height,
            'activity': self.activity,
            'goal': self.goal,
            'daily_calories': self.daily_calories,
            'protein': self.protein,
            'fat': self.fat,
            'carbs': self.carbs,
            'meals_count': self.meals_count
        }


def sql_user_to_user_profile(sql_user: Optional[SQLUser]) -> Optional[UserProfile]:
    """Преобразует SQLAlchemy User в UserProfile"""
    if sql_user is None:
        return None
    return UserProfile.from_sql_user(sql_user)
