#!/bin/bash

# Скрипт развертывания Telegram бота на сервере
# Использование: ./deploy.sh

set -e  # Выход при ошибке

echo "🚀 Начинаем развертывание Smart Ration Bot..."

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Функция для вывода сообщений
log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] $1${NC}"
}

warn() {
    echo -e "${YELLOW}[WARNING] $1${NC}"
}

error() {
    echo -e "${RED}[ERROR] $1${NC}"
    exit 1
}

# Проверка, что мы в правильной директории
if [ ! -f "docker-compose.yml" ]; then
    error "docker-compose.yml не найден. Убедитесь, что вы в правильной директории."
fi

# Проверка переменных окружения
if [ ! -f ".env" ]; then
    warn ".env файл не найден."
    echo "Создайте .env файл на основе env.production.template:"
    echo "cp env.production.template .env"
    echo "Затем отредактируйте .env файл с вашими данными"
    exit 1
fi

# Проверка Docker
if ! command -v docker &> /dev/null; then
    error "Docker не установлен. Установите Docker сначала."
fi

# Предпочитаем Docker Compose v2 (docker compose). Если его нет — пробуем v1 (docker-compose)
if docker compose version &> /dev/null; then
    DOCKER_COMPOSE="docker compose"
elif command -v docker-compose &> /dev/null; then
    DOCKER_COMPOSE="docker-compose"
else
    error "Docker Compose не установлен."
fi

log "Используем: $DOCKER_COMPOSE"

# Создание необходимых директорий
log "Создаем необходимые директории..."
mkdir -p logs
mkdir -p ssl
chmod 755 logs

# Остановка существующих контейнеров
log "Остановка существующих контейнеров..."
$DOCKER_COMPOSE down || true

# Очистка старых образов (опционально)
read -p "Очистить старые Docker образы? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    log "Очистка старых образов..."
    docker image prune -f || true
fi

# Сборка образов
log "Сборка Docker образов..."
$DOCKER_COMPOSE build --no-cache

# Запуск контейнеров
log "Запуск контейнеров..."
$DOCKER_COMPOSE up -d

# Ожидание запуска сервисов
log "Ожидание запуска сервисов..."
sleep 10

# Проверка статуса
log "Проверка статуса контейнеров..."
$DOCKER_COMPOSE ps

# Проверка логов
log "Последние логи бота:"
$DOCKER_COMPOSE logs --tail=20 bot

# Проверка здоровья
log "Проверка подключения к базе данных..."
if $DOCKER_COMPOSE exec -T postgres pg_isready -U bot_user -d telegram_bot; then
    log "✅ База данных доступна"
else
    warn "⚠️ Проблемы с подключением к базе данных"
fi

# Финальная информация
echo
echo "🎉 Развертывание завершено!"
echo
echo "📊 Полезные команды:"
echo "  Просмотр логов:     $DOCKER_COMPOSE logs -f bot"
echo "  Остановка:          $DOCKER_COMPOSE down"
echo "  Рестарт:            $DOCKER_COMPOSE restart bot"
echo "  Статус:             $DOCKER_COMPOSE ps"
echo "  Оболочка бота:      $DOCKER_COMPOSE exec bot bash"
echo "  Оболочка БД:        $DOCKER_COMPOSE exec postgres psql -U bot_user -d telegram_bot"
echo
echo "🌐 Веб-интерфейс: http://62.60.228.107/health"
echo
log "✅ Smart Ration Bot успешно развернут!"
