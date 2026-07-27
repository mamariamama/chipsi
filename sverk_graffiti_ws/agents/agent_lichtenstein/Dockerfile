FROM python:3.9-slim

WORKDIR /app

# Установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование кода агента
COPY agent.py .

# Запуск агента
CMD ["python", "-u", "agent.py"]