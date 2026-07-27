FROM ros:humble-ros-base

WORKDIR /app

# Установка зависимостей
RUN apt-get update && apt-get install -y \
    python3-pip \
    python3-colcon-common-extensions \
    ros-humble-moveit \
    ros-humble-ros2-control \
    && rm -rf /var/lib/apt/lists/*

# Python зависимости
COPY requirements.txt .
RUN pip3 install -r requirements.txt

# Копирование кода
COPY bridge.py .
COPY ../shared/art_patterns.json /app/shared/art_patterns.json

# Настройка ROS 2
RUN echo "source /opt/ros/humble/setup.bash" >> ~/.bashrc

CMD ["python3", "-u", "bridge.py"]