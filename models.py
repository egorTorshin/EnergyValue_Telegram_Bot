"""
SQLAlchemy модели для Telegram бота
"""
from datetime import datetime
from typing import List
from sqlalchemy import BigInteger, String, Float, Integer, DateTime, ForeignKey, Enum as SQLEnum
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from enum import Enum as PyEnum


class Gender(PyEnum):
    """Пол пользователя"""
    MALE = "male"
    FEMALE = "female"


class Activity(PyEnum):
    """Уровень физической активности"""
    SEDENTARY = 1.2
    LIGHT = 1.375
    MODERATE = 1.55
    ACTIVE = 1.725
    VERY_ACTIVE = 1.9


class Goal(PyEnum):
    """Цель пользователя"""
    WEIGHT_LOSS = "weight_loss"
    BALANCE = "balance"
    WEIGHT_GAIN = "weight_gain"


class Base(AsyncAttrs, DeclarativeBase):
    """Базовый класс для всех моделей"""
    pass


class User(Base):
    """Модель пользователя Telegram бота"""
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True)
    gender: Mapped[Gender] = mapped_column(SQLEnum(Gender), nullable=False)
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    height: Mapped[float] = mapped_column(Float, nullable=False)
    activity: Mapped[Activity] = mapped_column(SQLEnum(Activity), nullable=False)
    goal: Mapped[Goal] = mapped_column(SQLEnum(Goal), nullable=False)
    
    # Рассчитанные значения КБЖУ
    daily_calories: Mapped[int] = mapped_column(Integer, default=0)
    protein: Mapped[float] = mapped_column(Float, default=0.0)
    fat: Mapped[float] = mapped_column(Float, default=0.0)
    carbs: Mapped[float] = mapped_column(Float, default=0.0)
    meals_count: Mapped[int] = mapped_column(Integer, default=4)
    
    # Временные метки
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Связь с продуктами пользователя
    products: Mapped[List["UserProduct"]] = relationship(
        "UserProduct", 
        back_populates="user", 
        cascade="all, delete-orphan"
    )
    
    # Связь с избранными продуктами
    favorites: Mapped[List["FavoriteProduct"]] = relationship(
        "FavoriteProduct", 
        back_populates="user", 
        cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User(user_id={self.user_id}, gender={self.gender.value}, goal={self.goal.value})>"

    def to_dict(self) -> dict:
        """Преобразование модели в словарь"""
        return {
            "user_id": self.user_id,
            "gender": self.gender.value,
            "age": self.age,
            "weight": self.weight,
            "height": self.height,
            "activity": self.activity.value,
            "goal": self.goal.value,
            "daily_calories": self.daily_calories,
            "protein": self.protein,
            "fat": self.fat,
            "carbs": self.carbs,
            "meals_count": self.meals_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class UserProduct(Base):
    """Модель продукта пользователя"""
    __tablename__ = "user_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, 
        ForeignKey("users.user_id", ondelete="CASCADE"), 
        nullable=False,
        index=True
    )
    product_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    
    # Временная метка
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Связь с пользователем
    user: Mapped["User"] = relationship("User", back_populates="products")

    def __repr__(self) -> str:
        return f"<UserProduct(user_id={self.user_id}, product={self.product_name}, weight={self.weight}g)>"

    def to_dict(self) -> dict:
        """Преобразование модели в словарь"""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "product_name": self.product_name,
            "weight": self.weight,
            "added_at": self.added_at.isoformat() if self.added_at else None,
        }

    def to_tuple(self) -> tuple[str, float]:
        """Преобразование в кортеж для совместимости со старым кодом"""
        return (self.product_name, self.weight)


class FavoriteProduct(Base):
    """Модель избранного продукта пользователя"""
    __tablename__ = "favorite_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, 
        ForeignKey("users.user_id", ondelete="CASCADE"), 
        nullable=False,
        index=True
    )
    product_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    
    # Временная метка
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Связь с пользователем
    user: Mapped["User"] = relationship("User", back_populates="favorites")

    def __repr__(self) -> str:
        return f"<FavoriteProduct(user_id={self.user_id}, product={self.product_name})>"

    def to_dict(self) -> dict:
        """Преобразование модели в словарь"""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "product_name": self.product_name,
            "added_at": self.added_at.isoformat() if self.added_at else None,
        }


# Ограничения на уровне базы данных (дополнительная валидация)
from sqlalchemy import CheckConstraint

# Добавляем ограничения для User
User.__table_args__ = (
    CheckConstraint('age >= 10 AND age <= 100', name='check_age_range'),
    CheckConstraint('weight >= 30 AND weight <= 300', name='check_weight_range'),
    CheckConstraint('height >= 100 AND height <= 250', name='check_height_range'),
    CheckConstraint('daily_calories >= 0', name='check_positive_calories'),
    CheckConstraint('protein >= 0', name='check_positive_protein'),
    CheckConstraint('fat >= 0', name='check_positive_fat'),
    CheckConstraint('carbs >= 0', name='check_positive_carbs'),
    CheckConstraint('meals_count >= 1 AND meals_count <= 10', name='check_meals_count_range'),
)

# Добавляем ограничения для UserProduct
UserProduct.__table_args__ = (
    CheckConstraint('weight > 0', name='check_positive_weight'),
    CheckConstraint('LENGTH(product_name) > 0', name='check_product_name_not_empty'),
)

# Добавляем ограничения для FavoriteProduct
FavoriteProduct.__table_args__ = (
    CheckConstraint('LENGTH(product_name) > 0', name='check_favorite_product_name_not_empty'),
)
