import rclpy
from rclpy.node import Node
from geometry_msgs.msg import PoseStamped, TwistStamped
from std_msgs.msg import Bool, Float32, String
import threading
import time
import numpy as np

class DroneController(Node):
    def __init__(self, drone_id, color):
        super().__init__(f'drone_{drone_id}_controller')
        self.drone_id = drone_id
        self.color = color
        # Публикаторы
        self.pose_pub = self.create_publisher(PoseStamped, f'/{drone_id}/target_pose', 10)
        self.spray_pub = self.create_publisher(Bool, f'/{drone_id}/spray', 10)
        self.takeoff_pub = self.create_publisher(Bool, f'/{drone_id}/takeoff', 10)
        self.land_pub = self.create_publisher(Bool, f'/{drone_id}/land', 10)
        # Подписчики
        self.kill_sub = self.create_subscription(Bool, '/kill_switch', self.kill_callback, 10)
        self.battery_sub = self.create_subscription(Float32, f'/{drone_id}/battery', self.battery_callback, 10)
        self.status = 'idle'
        self.battery = 100.0
        self.kill_switch = False

    def kill_callback(self, msg):
        if msg.data:
            self.get_logger().warn(f'KILL SWITCH ACTIVATED for {self.drone_id}')
            self.kill_switch = True
            self.emergency_land()

    def battery_callback(self, msg):
        self.battery = msg.data
        if self.battery < float(os.getenv('MIN_BATTERY', 40)):
            self.get_logger().warn(f'Low battery on {self.drone_id}: {self.battery}%')
            # Можно инициировать посадку

    def takeoff(self, height=1.5):
        if self.kill_switch: return
        self.takeoff_pub.publish(Bool(data=True))
        time.sleep(3)  # ожидание взлёта
        self.status = 'flying'

    def land(self):
        self.land_pub.publish(Bool(data=True))
        self.status = 'landed'

    def emergency_land(self):
        self.land_pub.publish(Bool(data=True))
        self.status = 'emergency_landed'

    def go_to_point(self, x, y, z, yaw=0.0):
        if self.kill_switch: return
        pose = PoseStamped()
        pose.header.frame_id = 'map'
        pose.pose.position.x = x
        pose.pose.position.y = y
        pose.pose.position.z = z
        # ориентация
        pose.pose.orientation.w = 1.0
        self.pose_pub.publish(pose)
        time.sleep(1.5)  # ожидание

    def spray_on(self):
        self.spray_pub.publish(Bool(data=True))

    def spray_off(self):
        self.spray_pub.publish(Bool(data=False))