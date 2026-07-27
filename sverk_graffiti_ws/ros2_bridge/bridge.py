#!/usr/bin/env python3
"""
Мост между агентами (Redis) и четырьмя дронами (ROS 2).
- Принимает художественный консенсус от агентов.
- Распределяет траектории по дронам (каждый рисует своим цветом).
- Реализует синхронный взлёт, рисование и посадку.
- Обрабатывает kill switch (немедленная посадка).
- Мониторит заряд батареи.
"""

import os
import json
import time
import threading
import numpy as np
from typing import Dict, List, Tuple, Optional

import redis
import rclpy
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
from geometry_msgs.msg import PoseStamped, TwistStamped
from std_msgs.msg import Bool, Float32, String
from nav_msgs.msg import Odometry


# -----------------------------------------------------------------------------
# Контроллер одного дрона
# -----------------------------------------------------------------------------
class DroneController:
    """Управление одним дроном: взлёт, перемещение, распыление, посадка."""

    def __init__(self, node: Node, drone_id: str, color: str):
        self.node = node
        self.drone_id = drone_id
        self.color = color

        # Публикаторы
        self.takeoff_pub = node.create_publisher(Bool, f'/{drone_id}/takeoff', 10)
        self.land_pub = node.create_publisher(Bool, f'/{drone_id}/land', 10)
        self.pose_pub = node.create_publisher(PoseStamped, f'/{drone_id}/target_pose', 10)
        self.spray_pub = node.create_publisher(Bool, f'/{drone_id}/spray', 10)

        # Подписчики
        self.odom_sub = node.create_subscription(Odometry, f'/{drone_id}/odom', self.odom_cb, 10)
        self.battery_sub = node.create_subscription(Float32, f'/{drone_id}/battery', self.battery_cb, 10)

        # Состояние
        self.current_pose: Optional[PoseStamped] = None
        self.battery: float = 100.0
        self.status = 'idle'  # idle, flying, painting, landed, emergency
        self.kill_switch = False

    def odom_cb(self, msg: Odometry):
        self.current_pose = msg.pose.pose

    def battery_cb(self, msg: Float32):
        self.battery = msg.data
        min_battery = float(os.getenv('MIN_BATTERY', 40))
        if self.battery < min_battery:
            self.node.get_logger().warn(f'{self.drone_id}: низкий заряд ({self.battery:.1f}%)')

    def takeoff(self, height: float = 1.5):
        if self.kill_switch:
            return False
        self.node.get_logger().info(f'{self.drone_id}: взлёт на {height} м')
        self.takeoff_pub.publish(Bool(data=True))
        time.sleep(3)  # ожидание взлёта (можно заменить на проверку по топикам)
        self.status = 'flying'
        return True

    def land(self):
        self.node.get_logger().info(f'{self.drone_id}: посадка')
        self.land_pub.publish(Bool(data=True))
        self.status = 'landed'

    def emergency_land(self):
        self.node.get_logger().warn(f'{self.drone_id}: ЭКСТРЕННАЯ ПОСАДКА')
        self.land_pub.publish(Bool(data=True))
        self.status = 'emergency_landed'

    def go_to_point(self, x: float, y: float, z: float, yaw: float = 0.0, wait: float = 1.5):
        if self.kill_switch:
            return
        pose = PoseStamped()
        pose.header.frame_id = 'map'
        pose.header.stamp = self.node.get_clock().now().to_msg()
        pose.pose.position.x = x
        pose.pose.position.y = y
        pose.pose.position.z = z
        # Ориентация (простое направление носом вперёд)
        pose.pose.orientation.w = 1.0
        self.pose_pub.publish(pose)
        time.sleep(wait)

    def spray_on(self):
        if self.kill_switch:
            return
        self.spray_pub.publish(Bool(data=True))
        self.status = 'painting'

    def spray_off(self):
        self.spray_pub.publish(Bool(data=False))
        self.status = 'flying'


# -----------------------------------------------------------------------------
# Главный мост
# -----------------------------------------------------------------------------
class DroneArtBridge(Node):
    def __init__(self):
        super().__init__('drone_art_bridge')

        # Параметры из окружения
        self.canvas_width = float(os.getenv('CANVAS_WIDTH', 2.0))
        self.canvas_height = float(os.getenv('CANVAS_HEIGHT', 2.0))
        self.canvas_bottom = float(os.getenv('CANVAS_BOTTOM', 0.5))
        self.min_battery = float(os.getenv('MIN_BATTERY', 40))
        self.kill_switch_topic = os.getenv('KILL_SWITCH_TOPIC', '/kill_switch')
        self.redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379')

        # Подключение к Redis
        self.redis = redis.Redis.from_url(self.redis_url, decode_responses=True)
        self.pubsub = self.redis.pubsub()
        self.pubsub.subscribe('sverk:agents:consensus')

        # Публикация статуса в Redis (для веб-интерфейса)
        self.status_channel = 'sverk:bridge:status'

        # Создание контроллеров дронов
        self.drones: Dict[str, DroneController] = {
            'drone1': DroneController(self, 'drone1', 'red'),
            'drone2': DroneController(self, 'drone2', 'blue'),
            'drone3': DroneController(self, 'drone3', 'yellow'),
            'drone4': DroneController(self, 'drone4', 'black'),
        }

        # Kill switch – подписка и глобальный флаг
        self.kill_switch_global = False
        self.kill_sub = self.create_subscription(Bool, self.kill_switch_topic, self.kill_callback, 10)

        # Загрузка шаблонов рисунков
        self.art_patterns = self.load_art_patterns()

        self.get_logger().info('Drone Art Bridge готов. Ожидание консенсуса агентов...')

    def load_art_patterns(self) -> dict:
        """Загрузка паттернов из общего файла."""
        try:
            with open('/app/shared/art_patterns.json', 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            self.get_logger().warn('Файл art_patterns.json не найден, использую заглушку')
            return {
                "Чёрный квадрат": {
                    "type": "polygon",
                    "points": [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]],
                    "color": "black"
                },
                "default": {
                    "type": "polygon",
                    "points": [[0.3, 0.3], [0.7, 0.3], [0.7, 0.7], [0.3, 0.7]],
                    "color": "black"
                }
            }

    def kill_callback(self, msg: Bool):
        if msg.data and not self.kill_switch_global:
            self.get_logger().error('KILL SWITCH АКТИВИРОВАН!')
            self.kill_switch_global = True
            for drone in self.drones.values():
                drone.kill_switch = True
            # Экстренная посадка в отдельном потоке, чтобы не блокировать callback
            threading.Thread(target=self.emergency_all_land, daemon=True).start()

    def emergency_all_land(self):
        """Немедленная посадка всех дронов."""
        self.get_logger().warn('Выполняется экстренная посадка всех дронов...')
        threads = []
        for drone in self.drones.values():
            t = threading.Thread(target=drone.emergency_land)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        self.publish_status('emergency_landed', 'KILL SWITCH activated')

    def publish_status(self, status: str, message: str = ''):
        """Публикация статуса в Redis для отображения в веб-интерфейсе."""
        data = {
            'status': status,
            'message': message,
            'timestamp': time.time()
        }
        self.redis.publish(self.status_channel, json.dumps(data))

    def listen_for_decisions(self):
        """Основной цикл: слушаем канал консенсуса."""
        self.get_logger().info('Слушаю канал консенсуса...')
        for msg in self.pubsub.listen():
            if msg['type'] != 'message':
                continue
            try:
                data = json.loads(msg['data'])
            except json.JSONDecodeError:
                continue

            if data.get('type') == 'final_decision':
                art_concept = data['content']
                self.get_logger().info(f'Получен консенсус: {art_concept}')
                self.publish_status('painting', f'Рисуем: {art_concept}')

                # Запуск рисования
                success = self.execute_painting(art_concept)
                if success:
                    self.publish_status('completed', f'Завершено: {art_concept}')
                else:
                    self.publish_status('failed', 'Рисование прервано или произошла ошибка')

    def execute_painting(self, art_concept: str) -> bool:
        """
        Основная логика:
        1. Проверить заряд батарей.
        2. Сгенерировать траектории.
        3. Синхронный взлёт.
        4. Параллельное рисование.
        5. Синхронная посадка.
        """
        # Проверка батарей
        for drone in self.drones.values():
            if drone.battery < self.min_battery:
                self.get_logger().error(f'{drone.drone_id}: заряд ниже порога ({drone.battery:.1f}%)')
                self.publish_status('error', f'Низкий заряд у {drone.drone_id}')
                return False

        if self.kill_switch_global:
            self.get_logger().error('Kill switch активен, выполнение невозможно')
            return False

        # Получение траекторий
        paths = self.plan_paths(art_concept)
        if not paths:
            self.get_logger().error('Не удалось сгенерировать траектории')
            return False

        # Синхронный взлёт
        if not self.sync_takeoff():
            return False

        # Рисование
        self.get_logger().info('Начинаем рисование')
        threads = []
        for drone_id, path in paths.items():
            if path:
                drone = self.drones[drone_id]
                t = threading.Thread(target=self.paint_path, args=(drone, path))
                threads.append(t)
                t.start()

        # Ожидание завершения всех потоков рисования
        for t in threads:
            t.join()

        # Если за время рисования сработал kill switch, не садимся
        if self.kill_switch_global:
            return False

        # Синхронная посадка
        self.sync_land()
        return True

    def sync_takeoff(self) -> bool:
        """Одновременный взлёт всех дронов."""
        self.get_logger().info('Синхронный взлёт...')
        takeoff_height = 1.5  # метры над ArUco-метками

        # Проверка, что все дроны на земле (опционально)
        threads = []
        for drone in self.drones.values():
            t = threading.Thread(target=drone.takeoff, args=(takeoff_height,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        # Дополнительное ожидание стабилизации
        time.sleep(2)
        # Проверка успешности (по статусам)
        for drone in self.drones.values():
            if drone.status != 'flying':
                self.get_logger().error(f'{drone.drone_id} не взлетел (статус: {drone.status})')
                return False
        return True

    def sync_land(self):
        """Одновременная посадка всех дронов."""
        self.get_logger().info('Синхронная посадка...')
        threads = []
        for drone in self.drones.values():
            t = threading.Thread(target=drone.land)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

    def plan_paths(self, concept: str) -> Dict[str, List[Tuple[float, float, float]]]:
        """
        Преобразование художественного концепта в траектории.
        Возвращает словарь: {drone_id: [(x, y, z), ...]}.
        """
        # Ищем паттерн в шаблонах
        pattern = self.art_patterns.get(concept, self.art_patterns.get('default'))
        if not pattern:
            return {}

        # Получаем список фигур (может быть одиночная или мульти)
        elements = []
        if pattern.get('type') == 'polygon':
            elements.append({'points': pattern['points'], 'color': pattern.get('color', 'black')})
        elif pattern.get('type') == 'multipolygon':
            elements = pattern.get('elements', [])
        else:
            return {}

        # Сопоставление цветов дронам
        color_to_drone = {drone.color: drone_id for drone_id, drone in self.drones.items()}

        # Распределение точек по дронам
        drone_paths = {drone_id: [] for drone_id in self.drones.keys()}

        for elem in elements:
            color = elem.get('color', 'black')
            drone_id = color_to_drone.get(color)
            if drone_id is None:
                # Если нет дрона такого цвета, используем чёрный (drone4)
                drone_id = 'drone4'

            points_2d = elem['points']  # относительные координаты (0..1)
            path = []
            for pt in points_2d:
                # Преобразование в координаты на полотне
                x = pt[0] * self.canvas_width
                y = pt[1] * self.canvas_height
                z = self.canvas_bottom + self.canvas_height  # основание + высота полотна
                # Небольшой отступ от стены (безопасность)
                path.append((x, y, z))
            drone_paths[drone_id].extend(path)

        # Если у какого-то дрона пустой путь, можно назначить ему дежурную точку ожидания (опционально)
        for drone_id, path in drone_paths.items():
            if not path:
                # Отправим в точку ожидания за пределами полотна
                drone_paths[drone_id] = [( -0.5, -0.5, self.canvas_bottom + 0.5 )]

        return drone_paths

    def paint_path(self, drone: DroneController, path: List[Tuple[float, float, float]]):
        """
        Дрон летит по точкам, включает распыление при достижении первой точки,
        затем движется по траектории с распылением, в конце отключает распыление.
        """
        if self.kill_switch_global:
            return

        self.get_logger().info(f'{drone.drone_id} начинает рисование (цвет: {drone.color})')

        # Перемещение к первой точке без распыления
        first_point = path[0]
        drone.go_to_point(first_point[0], first_point[1], first_point[2], wait=2.0)

        # Включение распыления
        drone.spray_on()

        # Последовательное движение по всем точкам
        for i in range(1, len(path)):
            if self.kill_switch_global:
                break
            pt = path[i]
            drone.go_to_point(pt[0], pt[1], pt[2], wait=1.0)

        # Выключение распыления
        drone.spray_off()

        # Отойти от полотна (безопасное расстояние)
        drone.go_to_point(first_point[0], first_point[1], first_point[2] + 0.5, wait=1.0)
        self.get_logger().info(f'{drone.drone_id} завершил рисование')


# -----------------------------------------------------------------------------
# Точка входа
# -----------------------------------------------------------------------------
def main():
    rclpy.init()
    bridge = DroneArtBridge()

    # Запуск слушателя консенсуса в отдельном потоке
    listener_thread = threading.Thread(target=bridge.listen_for_decisions, daemon=True)
    listener_thread.start()

    # Использование MultiThreadedExecutor для обработки подписок (odom, battery, kill switch)
    executor = MultiThreadedExecutor()
    executor.add_node(bridge)

    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        bridge.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()