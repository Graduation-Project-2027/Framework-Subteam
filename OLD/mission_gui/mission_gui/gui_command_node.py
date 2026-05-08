import rclpy
from rclpy.node import Node
import threading
import sys

# Import our custom message and standard string messages
from custom_interfaces.msg import MissionRequest
from std_msgs.msg import String

class GuiCommandNode(Node):
    def __init__(self):
        super().__init__('gui_command_node')

        # Publisher for sending missions
        self.mission_pub = self.create_publisher(MissionRequest, '/mission_request', 10)

        # Subscribers for receiving status updates
        self.state_sub = self.create_subscription(String, '/robot_state', self.state_callback, 10)
        self.feedback_sub = self.create_subscription(String, '/mission_feedback', self.feedback_callback, 10)
    
        # Log that the node has started
        self.get_logger().info('GUI Command Node Started. Ready to send missions.')

        # Start a separate thread for user input so it doesn't block incoming messages
        self.input_thread = threading.Thread(target=self.user_input_loop)
        self.input_thread.daemon = True
        self.input_thread.start()

    # Callbacks for receiving robot state and mission feedback
    def state_callback(self, msg):
        self.get_logger().info(f'\n---> [ROBOT STATE]: {msg.data}')

    # This callback receives feedback from the robot about the mission status and logs it
    def feedback_callback(self, msg):
        self.get_logger().info(f'\n---> [FEEDBACK]: {msg.data}')

    # This method runs in a separate thread and continuously prompts the user for input to send mission commands
    def user_input_loop(self):
        while rclpy.ok():
            print("\n--- Mission Command Menu ---")
            print("Enter target location (A, B, X (Home Point), Y (Exit Point)) or 'Q' to quit:")
            
            # Read user input
            user_input = input("> ").strip().upper()

            if user_input == 'Q':
                self.get_logger().info("Shutting down GUI node...")
                rclpy.shutdown()
                sys.exit(0)
            elif user_input in ['A', 'B', 'X', 'Y']:
                # Create the message and send it
                msg = MissionRequest()
                msg.target_location = user_input
                self.mission_pub.publish(msg)
                self.get_logger().info(f'Mission sent: Navigate to Pick Location {user_input}')
            else:
                print("Invalid input. Please enter A, B, X, or Y.")

def main(args=None):
    rclpy.init(args=args)
    node = GuiCommandNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if rclpy.ok():
            rclpy.shutdown()

if __name__ == '__main__':
    main()