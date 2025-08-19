# Chess Trainer Bot - Docker Deployment

Полное Docker-решение для анализа шахматных партий и запуска Telegram-бота с тактическими задачами.

## 🚀 Быстрый старт

### 1. Клонирование и настройка

```bash
# Перейдите в директорию проекта
cd /path/to/chess

# Скопируйте файл переменных окружения
cp env.example .env

# Отредактируйте .env файл
nano .env
```

### 2. Настройка переменных окружения

В файле `.env` обязательно укажите:

```bash
# Получите токен от @BotFather в Telegram
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz

# Ваш username на Chess.com
CHESS_USER=your_username

# Опционально: настройте другие параметры
CHESS_MONTHS=2
CHESS_DEPTH=10
CHESS_MAX_POSITIONS=2500
```

### 3. Запуск

```bash
# Запуск бота
docker-compose up -d

# Просмотр логов
docker-compose logs -f
```

## 🤖 Как работает бот

### Команды бота:
- `/start` - Начать решение задач
- `/update username` - Загрузить и проанализировать партии
- `/stats` - Показать статистику дриллов  
- `/help` - Справка

### Процесс использования:
1. **Запуск бота**: `docker-compose up -d`
2. **Загрузка данных**: `/update your_chess_username` в Telegram
3. **Решение задач**: `/start` и выбрать сложность

### Анализ партий через бота:
- Команда `/update username` загружает партии с Chess.com
- Stockfish анализирует позиции и находит бландеры
- Создаются дриллы в SQLite базе
- Все работает асинхронно с progress bar в Telegram

## 💾 Управление данными

### Местоположение данных
- **Контейнер**: `/data/trainer_output.sqlite`
- **Хост**: Docker volume `chess_data`

### Просмотр данных
```bash
# Подключение к базе
docker-compose exec chess-trainer-bot sqlite3 /data/trainer_output.sqlite

# Или через хост (если установлен sqlite3)
docker volume inspect chess_data
# Найдите Mountpoint и подключитесь к базе
```

### Бэкап и восстановление
```bash
# Бэкап базы данных
docker run --rm -v chess_data:/data -v $(pwd):/backup alpine \
  cp /data/trainer_output.sqlite /backup/

# Восстановление
docker run --rm -v chess_data:/data -v $(pwd):/backup alpine \
  cp /backup/trainer_output.sqlite /data/
```

### Очистка данных
```bash
# Удаление тома с данными
docker-compose down -v

# Удаление только базы данных
docker-compose exec chess-trainer-bot rm /data/trainer_output.sqlite
```

## 🔧 Продвинутые настройки

### Кастомные настройки ресурсов
```yaml
# В docker-compose.yml
deploy:
  resources:
    limits:
      memory: 1G      # Увеличить для больших анализов
      cpus: '1.0'     # Больше CPU для Stockfish
```

### Привязка к локальной директории
```yaml
# В docker-compose.yml замените
volumes:
  - chess_data:/data

# На
volumes:
  - ./data:/data    # Локальная директория ./data
```

### Переопределение Stockfish
```yaml
# В docker-compose.yml добавьте
environment:
  STOCKFISH_PATH: /usr/local/bin/stockfish  # Если другой путь
```

## 🐛 Устранение проблем

### Бот не запускается
```bash
# Проверьте токен
docker-compose logs | grep "TOKEN"

# Проверьте базу данных
docker-compose exec chess-trainer-bot ls -la /data/

# Перезапуск с пересборкой
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

### Ошибки команды /update
```bash
# Проверьте доступность Chess.com API
docker-compose exec chess-trainer-bot python -c "import requests; print(requests.get('https://api.chess.com/pub/player/hikaru').status_code)"

# Проверьте Stockfish
docker-compose exec chess-trainer-bot /usr/games/stockfish

# Посмотрите логи анализа
docker-compose logs -f
```

### Проблемы с памятью
```bash
# В команде /update используйте меньшие параметры:
# /update username 1 8  (1 месяц, глубина 8)
```

## 📊 Мониторинг

### Просмотр логов
```bash
# Все логи
docker-compose logs

# Только последние 100 строк
docker-compose logs --tail=100

# Следить за логами в реальном времени
docker-compose logs -f
```

### Статистика контейнера
```bash
# Использование ресурсов
docker stats chess-trainer-bot

# Информация о контейнере
docker-compose ps
docker inspect chess-trainer-bot
```

### Проверка базы данных
```bash
# Количество дриллов
docker-compose exec chess-trainer-bot sqlite3 /data/trainer_output.sqlite \
  "SELECT COUNT(*) as drills_count FROM drills;"

# Последний анализ
docker-compose exec chess-trainer-bot sqlite3 /data/trainer_output.sqlite \
  "SELECT generated_at, user, COUNT(*) as drills FROM run_meta 
   JOIN drills ON run_meta.id = drills.run_id 
   GROUP BY run_meta.id ORDER BY generated_at DESC LIMIT 1;"
```

## 🔐 Безопасность

### Переменные окружения
- Никогда не коммитьте `.env` файл в git
- Используйте Docker secrets в продакшене
- Регулярно обновляйте токен бота

### Сетевая безопасность
```yaml
# Ограничение доступа к порту
ports:
  - "127.0.0.1:8080:8080"  # Только localhost

# Использование кастомной сети
networks:
  chess-network:
    driver: bridge
    internal: true  # Только внутренние соединения
```

## 📦 Обновление

```bash
# Остановка сервиса
docker-compose down

# Получение обновлений кода
git pull

# Пересборка образа
docker-compose build --no-cache

# Запуск с новой версией
docker-compose up -d

# Проверка версии
docker-compose exec chess-trainer-bot python chess_trainer_bot.py --help
```

## ⚡ Полезные команды

```bash
# Интерактивная оболочка для отладки
docker-compose run --rm chess-trainer-bot bash

# Принудительная остановка
docker-compose kill

# Полная очистка (код + данные + образы)
docker-compose down -v --rmi all

# Просмотр размера данных
docker system df
docker volume ls
```

---

💡 **Совет**: Для продакшн использования рекомендуется:
- Использовать отдельный сервер
- Настроить автоматические бэкапы базы данных  
- Мониторить логи и ресурсы
- Использовать reverse proxy (nginx) для дополнительной безопасности
