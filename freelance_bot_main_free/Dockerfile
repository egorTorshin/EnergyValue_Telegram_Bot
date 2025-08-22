# Используем Python 3.11 как базовый образ
FROM python:3.11-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Копируем файл зависимостей
COPY requirements.txt .

# Устанавливаем Python зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходный код приложения
COPY . .

# Создаем пользователя для безопасности (не root)
RUN useradd --create-home --shell /bin/bash bot_user && \
    chown -R bot_user:bot_user /app
USER bot_user

# Устанавливаем переменные окружения
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Экспонируем порт (для здоровья контейнера)
EXPOSE 8000

# Команда запуска
CMD ["python", "bot.py"]
