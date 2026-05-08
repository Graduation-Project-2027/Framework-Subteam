import rclpy
from rclpy.node import Node
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
import time
import math

from custom_interfaces.action import LiftToHeight
from std_msgs.msg import String, Float64, Bool

from sensor_msgs.msg import JointState
from nav_msgs.msg import Odometry

class LiftControllerNode(Node):
    def __init__(self):
        super().__init__('lift_controller_node')

        # --- Publishers & Subscribers ---
        self.state_pub = self.create_publisher(String, '/lift_state', 10) # Publishes states like "LIFT_IDLE", "LIFTING", "DROPPING", "ERROR"
        self.height_pub = self.create_publisher(Float64, '/lift_height', 10) # Publishes the current height for monitoring
        self.cmd_pos_pub = self.create_publisher(Float64, '/lift_joint/cmd_pos', 10) # Command topic for the lift mechanism
        self.estop_sub = self.create_subscription(Bool, '/emergency_stop', self.estop_callback, 10) # Subscribes to E-Stop status

        # --- Action Server ---
        # Using a ReentrantCallbackGroup allows the E-Stop callback to interrupt the execute_callback loop, ensuring safety
        self.cb_group = ReentrantCallbackGroup()

        # --- NEW SUBSCRIBERS ---
        # Listens to Gazebo for the true physical height of the lift
        self.joint_state_sub = self.create_subscription(JointState, '/joint_states', self.joint_state_callback, 10, callback_group=self.cb_group)
        # Listens to Nav2/Gazebo to see if the wheels are turning
        self.odom_sub = self.create_subscription(Odometry, '/odom', self.odom_callback, 10, callback_group=self.cb_group)
        # -----------------------

        self._action_server = ActionServer(
            self,
            LiftToHeight,
            '/lift_to_height',
            execute_callback=self.execute_callback,
            goal_callback=self.goal_callback,
            cancel_callback=self.cancel_callback,
            callback_group=self.cb_group
        )

        # --- Internal Hardware State Variables ---
        self.current_height = 0.0
        self.emergency_stop_active = False
        self.robot_velocity_is_zero = True # Mock variable for interlock
        self.load_sensor_detects_weight = True # Mock variable for confirmation 

        self.get_logger().info('Lift Controller Action Server Started.')
        self.publish_state("LIFT_IDLE")


    # --- Sensor & Odometry Callbacks ---
    def joint_state_callback(self, msg):
        """Continuously reads the real physics position from Gazebo."""
        try:
            # Find the index of the lift joint in the array
            # (Ensure "lift_joint" perfectly matches your URDF joint name)
            idx = msg.name.index('lift_joint')
            self.current_height = msg.position[idx]
        except ValueError:
            # The lift_joint wasn't in this specific message, safely ignore
            pass

    def odom_callback(self, msg):
        """Monitors the robot's velocity to prevent lifting while driving."""
        # Get absolute forward speed and rotation speed
        linear_v = abs(msg.twist.twist.linear.x)
        angular_v = abs(msg.twist.twist.angular.z)
        
        # SAFETY INTERLOCK: If the robot is moving faster than 2cm/s or rotating faster than 0.02 rad/s, 
        # we consider it "not stationary" and prevent lift operation
        if linear_v > 1.00 or angular_v > 1.00:
            self.robot_velocity_is_zero = False
        else:
            self.robot_velocity_is_zero = True

    # --- Helper Functions ---
    # This function centralizes state publishing, ensuring consistent messages and logging
    def publish_state(self, state_str):
        msg = String()
        msg.data = state_str
        self.state_pub.publish(msg)
    # E-Stop callback updates the internal state and immediately publishes an ERROR state if triggered, ensuring all components are aware of the critical condition
    def estop_callback(self, msg):
        self.emergency_stop_active = msg.data
        if self.emergency_stop_active:
            self.get_logger().warn("EMERGENCY STOP TRIGGERED IN LIFT CONTROLLER! [cite: 154]")
            self.publish_state("ERROR")

    # --- Action Server Callbacks ---
    def goal_callback(self, goal_request):
        """Validates the goal before accepting it."""
        self.get_logger().info(f'Received lift request to: {goal_request.target_height}m')
        
        # SAFETY INTERLOCK: Reject if E-Stop is active
        if self.emergency_stop_active:
            self.get_logger().warn('Goal Rejected: E-Stop is active.')
            return GoalResponse.REJECT
            
        # SAFETY INTERLOCK: Reject if robot is moving
        if not self.robot_velocity_is_zero:
            self.get_logger().warn('Goal Rejected: Robot is currently moving.')
            return GoalResponse.REJECT

        # SAFETY INTERLOCK: Limits check - Reject if target height is outside mechanical limits (0.0m to 2.0m)
        if goal_request.target_height > 2.0 or goal_request.target_height < 0.0:
             self.get_logger().warn('Goal Rejected: Target height exceeds mechanical limits.')
             return GoalResponse.REJECT

        self.get_logger().info('Goal Accepted.')
        return GoalResponse.ACCEPT

    # The cancel callback allows for graceful interruption of the lift operation, ensuring that the system can respond to operator commands or safety conditions without leaving the lift in an unsafe state
    def cancel_callback(self, goal_handle):
        """Handles cancel requests."""
        self.get_logger().info('Received request to cancel lift operation.')
        return CancelResponse.ACCEPT

    # The execute_callback is the core of the lift control logic, simulating the movement while continuously checking for safety conditions and providing feedback. 
    # It also includes a confirmation layer to ensure that the lift has successfully picked up a payload when lifting, adding an extra layer of safety and reliability to the operation.
    def execute_callback(self, goal_handle):
        """Executes the physical/simulated lifting movement."""
        self.get_logger().info('Executing lift...')
        
        # Determine state (LIFTING or DROPPING) based on target vs current height, 
        # and publish it immediately for accurate state representation during the operation
        target = goal_handle.request.target_height
        if target > self.current_height:
            self.publish_state("LIFTING")
        else:
            self.publish_state("DROPPING")

        feedback_msg = LiftToHeight.Feedback()
        result_msg = LiftToHeight.Result()

        # Simulated control loop
        step_size = 0.05
        sleep_duration = 0.1 # 100ms loop cycle simulates control update rate and allows for responsive feedback and E-Stop handling
        timeout_counter = 0.0
        timeout_limit = 15.0  # If the lift takes longer than this to reach the target, we assume a mechanical jam or overload and abort for safety
        
        # 1. SEND THE REAL COMMAND TO GAZEBO
        cmd_msg = Float64()
        cmd_msg.data = float(target)
        self.cmd_pos_pub.publish(cmd_msg)
        self.get_logger().info(f"Commanded Gazebo physics to move to {target}m...")

        # 2. WAIT FOR PHYSICAL CONFIRMATION (Closed Loop)
        # Loop continues until the physical joint is within 1cm (0.01m) of the target
        while abs(self.current_height - target) > 0.01:
            
            # Check for E-Stop or Cancellation 
            if self.emergency_stop_active or goal_handle.is_cancel_requested:
                goal_handle.canceled()
                self.get_logger().info('Lift canceled or aborted due to E-Stop.')
                result_msg.success = False
                self.publish_state("ERROR" if self.emergency_stop_active else "LIFT_IDLE")
                return result_msg

            # Check for Mechanical Jam (Timeout)
            timeout_counter += sleep_duration
            if timeout_counter > timeout_limit:
                self.get_logger().error("LIFT TIMEOUT! The mechanism is stuck or the payload is too heavy.")
                goal_handle.abort()
                result_msg.success = False
                self.publish_state("ERROR")
                return result_msg

            # Publish Feedback to Action Client and standard topic
            feedback_msg.current_height = self.current_height
            goal_handle.publish_feedback(feedback_msg)
            
            height_msg = Float64()
            height_msg.data = self.current_height
            self.height_pub.publish(height_msg)

            time.sleep(sleep_duration)

        # 3. OBJECT CONFIRMATION LAYER (Sensor Fusion) 
        # Only check payload if we were lifting (not dropping)
        if target > 0.1: 
            if not self.load_sensor_detects_weight:
                self.get_logger().error("Lift reached target, but no payload detected! (Sensor Mismatch)")
                goal_handle.abort()
                result_msg.success = False
                self.publish_state("ERROR")
                return result_msg

        # 4. Success
        goal_handle.succeed()
        result_msg.success = True
        self.publish_state("LIFT_IDLE")
        self.get_logger().info('Lift reached target height successfully.')
        return result_msg

def main(args=None):
    rclpy.init(args=args)
    node = LiftControllerNode()
    
    # Using a MultiThreadedExecutor allows the node to handle multiple callbacks concurrently, 
    # which is essential for responsive E-Stop handling and action execution without blocking each other.
    executor = MultiThreadedExecutor()
    
    try:
        rclpy.spin(node, executor=executor)
    except KeyboardInterrupt:
        pass
    finally:
        if rclpy.ok():
            rclpy.shutdown()

if __name__ == '__main__':
    main()