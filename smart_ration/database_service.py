"""
Сервис для работы с базой данных через SQLAlchemy
"""
import os
import logging
from typing import List, Optional, Tuple
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import func, delete

from models import Base, User, UserProduct, Gender, Activity, Goal

logger = logging.getLogger(__name__)


class DatabaseService:
    """Сервис для работы с PostgreSQL через SQLAlchemy"""
    
    def __init__(self):
        self.engine = None
        self.session_factory = None
        self.database_url = self._get_database_url()
    
    def _get_database_url(self) -> str:
        """Получает URL базы данных из переменных окружения"""
        host = os.getenv('DB_HOST', 'localhost')
        port = os.getenv('DB_PORT', '5432')
        user = os.getenv('DB_USER', 'postgres')
        password = os.getenv('DB_PASSWORD', '')
        database = os.getenv('DB_NAME', 'telegram_bot')
        
        # Для SQLAlchemy с asyncpg используем postgresql+asyncpg://
        return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}"
    
    async def initialize(self):
        """Инициализация движка и фабрики сессий"""
        try:
            self.engine = create_async_engine(
                self.database_url,
                echo=False,  # Установите True для отладки SQL запросов
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True,
                pool_recycle=3600,
            )
            
            self.session_factory = async_sessionmaker(
                bind=self.engine,
                class_=AsyncSession,
                expire_on_commit=False
            )
            
            # Создаем таблицы
            await self._create_tables()
            
            logger.info("SQLAlchemy подключен к базе данных")
            
        except Exception as e:
            logger.error(f"Ошибка инициализации базы данных: {e}")
            raise
    
    async def close(self):
        """Закрытие соединения с базой данных"""
        if self.engine:
            await self.engine.dispose()
            logger.info("Соединение с базой данных закрыто")
    
    async def _create_tables(self):
        """Создание таблиц в базе данных"""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Таблицы базы данных созданы/проверены")
    
    @asynccontextmanager
    async def get_session(self):
        """Контекстный менеджер для получения сессии"""
        if not self.session_factory:
            raise RuntimeError("База данных не инициализирована")
        
        async with self.session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
    
    # Методы для работы с пользователями
    async def save_user(self, user_data: dict) -> bool:
        """
        Сохранение или обновление пользователя в базе данных
        
        Args:
            user_data: Словарь с данными пользователя
        """
        try:
            async with self.get_session() as session:
                # Проверяем, существует ли пользователь
                existing_user = await session.get(User, user_data['user_id'])
                
                if existing_user:
                    # Обновляем существующего пользователя
                    for key, value in user_data.items():
                        if hasattr(existing_user, key):
                            setattr(existing_user, key, value)
                    user = existing_user
                else:
                    # Создаем нового пользователя
                    user = User(**user_data)
                    session.add(user)
                
                await session.commit()
                logger.info(f"Пользователь {user_data['user_id']} сохранен в базе данных")
                return True
                
        except Exception as e:
            logger.error(f"Ошибка сохранения пользователя {user_data.get('user_id')}: {e}")
            return False
    
    async def get_user(self, user_id: int) -> Optional[User]:
        """Получение пользователя из базы данных"""
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    select(User).where(User.user_id == user_id)
                )
                user = result.scalar_one_or_none()
                return user
                
        except Exception as e:
            logger.error(f"Ошибка получения пользователя {user_id}: {e}")
            return None
    
    async def user_exists(self, user_id: int) -> bool:
        """Проверка существования пользователя"""
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    select(func.count(User.user_id)).where(User.user_id == user_id)
                )
                count = result.scalar()
                return count > 0
        except Exception as e:
            logger.error(f"Ошибка проверки существования пользователя {user_id}: {e}")
            return False
    
    async def delete_user(self, user_id: int) -> bool:
        """Удаление пользователя и всех его данных"""
        try:
            async with self.get_session() as session:
                user = await session.get(User, user_id)
                if user:
                    await session.delete(user)
                    await session.commit()
                    logger.info(f"Пользователь {user_id} удален")
                    return True
                return False
        except Exception as e:
            logger.error(f"Ошибка удаления пользователя {user_id}: {e}")
            return False
    
    # Методы для работы с продуктами пользователей
    async def add_user_product(self, user_id: int, product_name: str, weight: float) -> bool:
        """Добавление продукта пользователю с объединением одинаковых"""
        try:
            async with self.get_session() as session:
                # Ищем существующий продукт
                result = await session.execute(
                    select(UserProduct).where(
                        UserProduct.user_id == user_id,
                        func.lower(UserProduct.product_name) == func.lower(product_name)
                    )
                )
                existing_product = result.scalar_one_or_none()
                
                if existing_product:
                    # Обновляем вес существующего продукта
                    existing_product.weight += weight
                else:
                    # Добавляем новый продукт
                    new_product = UserProduct(
                        user_id=user_id,
                        product_name=product_name.lower(),
                        weight=weight
                    )
                    session.add(new_product)
                
                await session.commit()
                logger.info(f"Продукт {product_name} ({weight}г) добавлен пользователю {user_id}")
                return True
                
        except Exception as e:
            logger.error(f"Ошибка добавления продукта пользователю {user_id}: {e}")
            return False
    
    async def get_user_products(self, user_id: int) -> List[Tuple[str, float]]:
        """Получение всех продуктов пользователя"""
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    select(UserProduct)
                    .where(UserProduct.user_id == user_id)
                    .order_by(UserProduct.added_at.desc())
                )
                products = result.scalars().all()
                return [product.to_tuple() for product in products]
                
        except Exception as e:
            logger.error(f"Ошибка получения продуктов пользователя {user_id}: {e}")
            return []
    
    async def get_user_products_with_details(self, user_id: int) -> List[UserProduct]:
        """Получение всех продуктов пользователя с полной информацией"""
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    select(UserProduct)
                    .where(UserProduct.user_id == user_id)
                    .order_by(UserProduct.added_at.desc())
                )
                return result.scalars().all()
                
        except Exception as e:
            logger.error(f"Ошибка получения продуктов с деталями для пользователя {user_id}: {e}")
            return []
    
    async def remove_user_product(self, user_id: int, product_name: str) -> bool:
        """Удаление продукта у пользователя"""
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    delete(UserProduct).where(
                        UserProduct.user_id == user_id,
                        func.lower(UserProduct.product_name) == func.lower(product_name)
                    )
                )
                
                if result.rowcount > 0:
                    await session.commit()
                    logger.info(f"Продукт {product_name} удален у пользователя {user_id}")
                    return True
                return False
                
        except Exception as e:
            logger.error(f"Ошибка удаления продукта {product_name} у пользователя {user_id}: {e}")
            return False
    
    async def clear_user_products(self, user_id: int) -> bool:
        """Очистка всех продуктов пользователя"""
        try:
            async with self.get_session() as session:
                await session.execute(
                    delete(UserProduct).where(UserProduct.user_id == user_id)
                )
                await session.commit()
                logger.info(f"Все продукты пользователя {user_id} удалены")
                return True
                
        except Exception as e:
            logger.error(f"Ошибка очистки продуктов пользователя {user_id}: {e}")
            return False
    
    # Статистические методы
    async def get_users_count(self) -> int:
        """Получение количества пользователей"""
        try:
            async with self.get_session() as session:
                result = await session.execute(select(func.count(User.user_id)))
                return result.scalar() or 0
        except Exception as e:
            logger.error(f"Ошибка получения количества пользователей: {e}")
            return 0
    
    async def get_products_count(self, user_id: int) -> int:
        """Получение количества продуктов у пользователя"""
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    select(func.count(UserProduct.id)).where(UserProduct.user_id == user_id)
                )
                return result.scalar() or 0
        except Exception as e:
            logger.error(f"Ошибка получения количества продуктов пользователя {user_id}: {e}")
            return 0
    
    async def get_user_with_products(self, user_id: int) -> Optional[User]:
        """Получение пользователя со всеми его продуктами"""
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    select(User)
                    .options(selectinload(User.products))
                    .where(User.user_id == user_id)
                )
                return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Ошибка получения пользователя с продуктами {user_id}: {e}")
            return None
    
    # Методы для статистики и аналитики
    async def get_popular_products(self, limit: int = 10) -> List[Tuple[str, int]]:
        """Получение самых популярных продуктов"""
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    select(
                        UserProduct.product_name,
                        func.count(UserProduct.id).label('usage_count')
                    )
                    .group_by(UserProduct.product_name)
                    .order_by(func.count(UserProduct.id).desc())
                    .limit(limit)
                )
                return [(row.product_name, row.usage_count) for row in result]
        except Exception as e:
            logger.error(f"Ошибка получения популярных продуктов: {e}")
            return []
    
    async def get_users_by_goal(self, goal: Goal) -> List[User]:
        """Получение пользователей по цели"""
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    select(User).where(User.goal == goal)
                )
                return result.scalars().all()
        except Exception as e:
            logger.error(f"Ошибка получения пользователей по цели {goal}: {e}")
            return []


# Глобальный экземпляр сервиса базы данных
db_service = DatabaseService()
