#!/bin/bash

# Скрипт мониторинга Smart Ration Bot
# Использование: ./monitor.sh

set -e

# Цвета
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Определяем docker-compose команду
if command -v docker-compose &> /dev/null; then
    DOCKER_COMPOSE="docker-compose"
else
    DOCKER_COMPOSE="docker compose"
fi

echo -e "${BLUE}📊 Smart Ration Bot - Мониторинг${NC}"
echo "=================================="

# Функция проверки статуса контейнера
check_container() {
    local container_name=$1
    local service_name=$2
    
    if docker ps --format "table {{.Names}}\t{{.Status}}" | grep -q "$container_name"; then
        local status=$(docker ps --format "table {{.Names}}\t{{.Status}}" | grep "$container_name" | awk '{print $2}')
        if [[ $status == "Up" ]]; then
            echo -e "✅ $service_name: ${GREEN}Работает${NC}"
        else
            echo -e "⚠️ $service_name: ${YELLOW}$status${NC}"
        fi
    else
        echo -e "❌ $service_name: ${RED}Остановлен${NC}"
    fi
}

# Проверка всех сервисов
echo "🔍 Статус сервисов:"
check_container "smart_ration_bot" "Telegram Bot"
check_container "smart_ration_db" "PostgreSQL"
check_container "smart_ration_redis" "Redis"
check_container "smart_ration_nginx" "Nginx"

echo

# Использование ресурсов
echo "💾 Использование ресурсов:"
docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}" | grep -E "(smart_ration|CONTAINER)"

echo

# Проверка логов на ошибки
echo "🔍 Последние ошибки в логах:"
if $DOCKER_COMPOSE logs --tail=50 bot 2>/dev/null | grep -i "error\|exception\|failed" | tail -5; then
    echo -e "${RED}Найдены ошибки в логах!${NC}"
else
    echo -e "${GREEN}Ошибок в логах не найдено${NC}"
fi

echo

# Проверка подключения к базе данных
echo "🗄️ Проверка базы данных:"
if $DOCKER_COMPOSE exec -T postgres pg_isready -U bot_user -d telegram_bot &>/dev/null; then
    echo -e "✅ База данных: ${GREEN}Доступна${NC}"
    
    # Количество пользователей
    USER_COUNT=$($DOCKER_COMPOSE exec -T postgres psql -U bot_user -d telegram_bot -t -c "SELECT COUNT(*) FROM users;" 2>/dev/null | tr -d ' ')
    if [[ $USER_COUNT =~ ^[0-9]+$ ]]; then
        echo -e "👥 Пользователей в базе: ${BLUE}$USER_COUNT${NC}"
    fi
else
    echo -e "❌ База данных: ${RED}Недоступна${NC}"
fi

echo

# Проверка дискового пространства
echo "💿 Дисковое пространство:"
df -h / | tail -1 | awk '{print "Использовано: " $3 " из " $2 " (" $5 ")"}'

# Размер логов
LOG_SIZE=$(du -sh logs 2>/dev/null | awk '{print $1}' || echo "0K")
echo "📋 Размер логов: $LOG_SIZE"

echo

# Время работы контейнеров
echo "⏱️ Время работы:"
docker ps --format "table {{.Names}}\t{{.Status}}" | grep smart_ration | while read line; do
    container=$(echo $line | awk '{print $1}')
    uptime=$(echo $line | awk '{$1=""; print $0}' | sed 's/^ *//')
    echo "$container: $uptime"
done

echo

# Быстрые команды
echo "🛠️ Быстрые команды:"
echo "  Перезапуск бота:    $DOCKER_COMPOSE restart bot"
echo "  Просмотр логов:     $DOCKER_COMPOSE logs -f bot"
echo "  Вход в контейнер:   $DOCKER_COMPOSE exec bot bash"
echo "  Остановка всего:    $DOCKER_COMPOSE down"
echo "  Запуск всего:       $DOCKER_COMPOSE up -d"

echo
echo "🔄 Для автоматического обновления каждые 5 секунд:"
echo "   watch -n 5 ./monitor.sh"
