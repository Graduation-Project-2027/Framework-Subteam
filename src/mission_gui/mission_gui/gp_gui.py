import sys
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from custom_interfaces.msg import MissionRequest
from lifecycle_msgs.srv import ChangeState
from lifecycle_msgs.msg import Transition
from PyQt5.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox
from PyQt5.QtCore import QTimer, Qt

class GuiRosNode(Node):
    def __init__(self):
        super().__init__('gui_command_node')
        self.gui = None  
    
        # Publisher for sending missions
        self.mission_pub = self.create_publisher(MissionRequest, '/mission_request', 10)

        # Subscribers for receiving status updates
        self.state_sub = self.create_subscription(String, '/robot_state', self.state_callback, 10)
        self.feedback_sub = self.create_subscription(String, '/mission_feedback', self.feedback_callback, 10)
        self.lift_sub = self.create_subscription(String, '/lift_state', self.lift_callback, 10)
        
        # Service client for lifecycle management
        self.client = self.create_client(ChangeState, '/mission_manager_node/change_state')
        self.get_logger().info('GUI Command Node Started. Ready to send missions.')

    # Custom method to send location from GUI buttons
    def send_mission(self, location):
        msg = MissionRequest()
        msg.target_location = location
        self.mission_pub.publish(msg)
        self.get_logger().info(f'Mission sent: Navigate to {location}')

    # Callbacks to update the GUI labels dynamically
    def state_callback(self, msg):
        self.get_logger().info(f'[ROBOT STATE]: {msg.data}')
        if self.gui:
            self.gui.state_label.setText(f"Robot State: {msg.data}")

    def feedback_callback(self, msg):
        self.get_logger().info(f'[FEEDBACK]: {msg.data}')
        if self.gui:
            self.gui.feedback_label.setText(f"Feedback: {msg.data}")

    def lift_callback(self, msg):
        self.get_logger().info(f'[LIFT STATUS]: {msg.data}')
        if self.gui:
            self.gui.lift_label.setText(f"Lift Status: {msg.data}")

    # Lifecycle methods
    def send_lifecycle_transition(self, transition_id):
        if not self.client.wait_for_service(timeout_sec=2.0):
            self.get_logger().error('Lifecycle service not available.')
            return

        req = ChangeState.Request()
        req.transition.id = transition_id
        future = self.client.call_async(req)
        future.add_done_callback(self.transition_callback)

    def transition_callback(self, future):
        try:
            response = future.result()
            if response.success:
                self.get_logger().info('State transition successful!')
            else:
                self.get_logger().warning('State transition failed.')
        except Exception as e:
            self.get_logger().error(f'Service call failed: {e}')


# 2. Define the PyQt GUI
class MainWindow(QWidget):
    def __init__(self, ros_node):
        super().__init__()
        self.ros_node = ros_node
        self.ros_node.gui = self 
        self.init_ui()

        # 3. The Timer to spin ROS 2
        self.ros_timer = QTimer(self)
        self.ros_timer.timeout.connect(self.spin_ros)
        self.ros_timer.start(10) 

    def init_ui(self):
        self.setWindowTitle('Mission Control Dashboard')
        self.resize(500, 300)
        main_layout = QVBoxLayout()

        # --- Status Display Area ---
        status_group = QGroupBox("Live Status")
        status_layout = QVBoxLayout()
        
        self.state_label = QLabel('Robot State: Waiting...', self)
        self.state_label.setStyleSheet("color: blue; font-weight: bold; font-size: 14px;")
        status_layout.addWidget(self.state_label)

        self.feedback_label = QLabel('Feedback: Waiting...', self)
        self.feedback_label.setStyleSheet("color: green; font-weight: bold; font-size: 14px;")
        status_layout.addWidget(self.feedback_label)
        
        self.lift_label = QLabel('Lift Status: Waiting...', self)
        self.lift_label.setStyleSheet("color: red; font-weight: bold; font-size: 14px;")
        status_layout.addWidget(self.lift_label)

        status_group.setLayout(status_layout)
        main_layout.addWidget(status_group)

        # --- Mission Command Buttons (Replacing Terminal Input) ---
        mission_group = QGroupBox("Send Mission Command")
        mission_layout = QHBoxLayout()

        self.btn_a = QPushButton('Location A', self)
        self.btn_a.clicked.connect(lambda: self.ros_node.send_mission('A'))
        mission_layout.addWidget(self.btn_a)

        self.btn_b = QPushButton('Location B', self)
        self.btn_b.clicked.connect(lambda: self.ros_node.send_mission('B'))
        mission_layout.addWidget(self.btn_b)

        self.btn_x = QPushButton('Location X (Home)', self)
        self.btn_x.clicked.connect(lambda: self.ros_node.send_mission('X'))
        mission_layout.addWidget(self.btn_x)

        self.btn_y = QPushButton('Location Y (Exit)', self)
        self.btn_y.clicked.connect(lambda: self.ros_node.send_mission('Y'))
        mission_layout.addWidget(self.btn_y)

        mission_group.setLayout(mission_layout)
        main_layout.addWidget(mission_group)

        # --- Lifecycle Management Buttons ---
        lifecycle_group = QGroupBox("Lifecycle Management")
        lifecycle_layout = QVBoxLayout()
        
        self.config_btn = QPushButton('Configure Lifecycle Node', self)
        self.config_btn.clicked.connect(lambda: self.ros_node.send_lifecycle_transition(Transition.TRANSITION_CONFIGURE))
        lifecycle_layout.addWidget(self.config_btn)

        self.activate_btn = QPushButton('Activate Lifecycle Node', self)
        self.activate_btn.clicked.connect(lambda: self.ros_node.send_lifecycle_transition(Transition.TRANSITION_ACTIVATE))
        lifecycle_layout.addWidget(self.activate_btn)

        lifecycle_group.setLayout(lifecycle_layout)
        main_layout.addWidget(lifecycle_group)

        self.setLayout(main_layout)

    def spin_ros(self):
        # Allow ROS to process incoming/outgoing messages
        rclpy.spin_once(self.ros_node, timeout_sec=0)


# 4. Main Execution
def main(args=None):
    rclpy.init(args=args)
    ros_node = GuiRosNode()

    app = QApplication(sys.argv)
    window = MainWindow(ros_node)
    window.show()

    # When the GUI is closed, clean up ROS
    sys.exit_code = app.exec_()
    ros_node.destroy_node()
    rclpy.shutdown()
    sys.exit(sys.exit_code)

if __name__ == '__main__':
    main()