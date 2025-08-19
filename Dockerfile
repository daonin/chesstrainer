FROM python:3.11-slim

# Обновляем систему и устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    stockfish \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Создаем рабочую директорию
WORKDIR /app

# Копируем requirements
COPY requirements_docker.txt .

# Устанавливаем Python зависимости
RUN pip install --no-cache-dir -r requirements_docker.txt

# Копируем основной скрипт
COPY chess_trainer_bot.py .

# Создаем директорию для данных
RUN mkdir -p /data

# Создаем непривилегированного пользователя
RUN useradd -r -s /bin/false -m -d /app chess && \
    chown -R chess:chess /app /data

USER chess

# Устанавливаем переменные окружения
ENV STOCKFISH_PATH=/usr/games/stockfish
ENV CHESS_DB_PATH=/data/trainer_output.sqlite
ENV PYTHONUNBUFFERED=1

# Проверяем здоровье контейнера
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sqlite3; sqlite3.connect('/data/trainer_output.sqlite').close()" || exit 1

# Том для данных
VOLUME ["/data"]

# По умолчанию запускаем бота
CMD ["python", "chess_trainer_bot.py", "--stockfish", "/usr/games/stockfish", "--db-path", "/data/trainer_output.sqlite"]
