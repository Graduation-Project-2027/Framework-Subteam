#!/usr/bin/env python3

from math import atan2, pi, sqrt

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState


CONTROL_JOINT = 'lead_screw_turns'
LEAD_PER_TURN_M = 0.008
NUT_TRAVEL_LIMIT_M = 0.08
SCREW_TURN_LOWER_LIMIT = -2.972
SCREW_TURN_UPPER_LIMIT = 4.162

HOLE_HALF_DY_M = 0.01705
HOLE_HALF_DZ_M = 0.0427235
BAR_END_DISTANCE_M = 2.0 * sqrt(HOLE_HALF_DY_M ** 2 + HOLE_HALF_DZ_M ** 2)

# These anchor values come from the actual table and bracket hole axes in the CAD mesh.
LOWER_ANCHOR_Z_M = 0.0376005
UPPER_ANCHOR_Z0_M = 0.10531
FIXED_ANCHOR_Y_ABS_M = 0.207875
MOVING_ANCHOR_Y0_ABS_M = FIXED_ANCHOR_Y_ABS_M - sqrt(
    BAR_END_DISTANCE_M ** 2 - (UPPER_ANCHOR_Z0_M - LOWER_ANCHOR_Z_M) ** 2
)
CENTER_Y0_ABS_M = 0.5 * (FIXED_ANCHOR_Y_ABS_M + MOVING_ANCHOR_Y0_ABS_M)
CENTER_Z0_M = 0.5 * (LOWER_ANCHOR_Z_M + UPPER_ANCHOR_Z0_M)
GEOMETRIC_ZERO_THETA = atan2(2.0 * HOLE_HALF_DY_M, 2.0 * HOLE_HALF_DZ_M)

MAX_OUTWARD_TRAVEL_M = min(
    NUT_TRAVEL_LIMIT_M,
    FIXED_ANCHOR_Y_ABS_M - MOVING_ANCHOR_Y0_ABS_M - 1e-6,
)
MAX_INWARD_TRAVEL_M = min(
    NUT_TRAVEL_LIMIT_M,
    MOVING_ANCHOR_Y0_ABS_M - (FIXED_ANCHOR_Y_ABS_M - BAR_END_DISTANCE_M) - 1e-6,
)

PAIRS = [
    {
        'side': 1.0,
        'center_y0': CENTER_Y0_ABS_M,
        'center_z0': CENTER_Z0_M,
        'center_y_joint': 'joint_center_front_left_y',
        'center_z_joint': 'joint_center_front_left_z',
        'bar1_joint': 'joint_bar1_front_left',
        'bar2_joint': 'joint_bar2_front_left',
    },
    {
        'side': 1.0,
        'center_y0': CENTER_Y0_ABS_M,
        'center_z0': CENTER_Z0_M,
        'center_y_joint': 'joint_center_back_left_y',
        'center_z_joint': 'joint_center_back_left_z',
        'bar1_joint': 'joint_bar1_back_left',
        'bar2_joint': 'joint_bar2_back_left',
    },
    {
        'side': -1.0,
        'center_y0': -CENTER_Y0_ABS_M,
        'center_z0': CENTER_Z0_M,
        'center_y_joint': 'joint_center_front_right_y',
        'center_z_joint': 'joint_center_front_right_z',
        'bar1_joint': 'joint_bar1_front_right',
        'bar2_joint': 'joint_bar2_front_right',
    },
    {
        'side': -1.0,
        'center_y0': -CENTER_Y0_ABS_M,
        'center_z0': CENTER_Z0_M,
        'center_y_joint': 'joint_center_back_right_y',
        'center_z_joint': 'joint_center_back_right_z',
        'bar1_joint': 'joint_bar1_back_right',
        'bar2_joint': 'joint_bar2_back_right',
    },
]


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


class ScissorMechanismPublisher(Node):
    def __init__(self) -> None:
        super().__init__('scissor_mechanism_publisher')
        self.drive_turns = 0.0
        self.publisher = self.create_publisher(JointState, 'joint_states', 10)
        self.create_subscription(JointState, 'drive_joint_states', self.drive_callback, 10)
        self.create_timer(1.0 / 30.0, self.publish_state)

    def drive_callback(self, msg: JointState) -> None:
        if CONTROL_JOINT not in msg.name or not msg.position:
            return

        index = msg.name.index(CONTROL_JOINT)
        self.drive_turns = clamp(
            msg.position[index],
            SCREW_TURN_LOWER_LIMIT,
            SCREW_TURN_UPPER_LIMIT,
        )

    def pair_state(self, pair: dict[str, float], outward_travel: float) -> dict[str, float]:
        side = pair['side']
        center_y0 = pair['center_y0']
        center_z0 = pair['center_z0']

        moving_y_abs = MOVING_ANCHOR_Y0_ABS_M + outward_travel
        fixed_y_abs = FIXED_ANCHOR_Y_ABS_M
        horizontal_span = fixed_y_abs - moving_y_abs

        upper_z = LOWER_ANCHOR_Z_M + sqrt(max(BAR_END_DISTANCE_M ** 2 - horizontal_span ** 2, 1e-9))
        table_lift = upper_z - UPPER_ANCHOR_Z0_M

        center_y = side * (0.5 * (moving_y_abs + fixed_y_abs))
        center_z = 0.5 * (LOWER_ANCHOR_Z_M + upper_z)
        theta = atan2(side * horizontal_span, upper_z - LOWER_ANCHOR_Z_M)
        delta_theta = theta - side * GEOMETRIC_ZERO_THETA

        return {
            'table_lift': table_lift,
            'center_y_offset': center_y - center_y0,
            'center_z_offset': center_z - center_z0,
            'bar1_angle': -delta_theta,
            'bar2_angle': delta_theta,
        }

    def publish_state(self) -> None:
        turns = clamp(
            self.drive_turns,
            SCREW_TURN_LOWER_LIMIT,
            SCREW_TURN_UPPER_LIMIT,
        )
        screw_angle = turns * 2.0 * pi

        requested_outward = turns * LEAD_PER_TURN_M
        outward_travel = clamp(requested_outward, -MAX_INWARD_TRAVEL_M, MAX_OUTWARD_TRAVEL_M)
        nut_travel = -outward_travel

        pair_states = [self.pair_state(pair, outward_travel) for pair in PAIRS]
        table_lift = sum(state['table_lift'] for state in pair_states) / len(pair_states)

        msg = JointState()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.name = [
            'joint_lead_screw_left',
            'joint_lead_screw_right',
            'joint_nut_left',
            'joint_nut_right',
            'joint_center_front_left_y',
            'joint_center_front_left_z',
            'joint_center_back_left_y',
            'joint_center_back_left_z',
            'joint_center_front_right_y',
            'joint_center_front_right_z',
            'joint_center_back_right_y',
            'joint_center_back_right_z',
            'joint_bar1_front_left',
            'joint_bar2_front_left',
            'joint_bar1_back_left',
            'joint_bar2_back_left',
            'joint_bar1_front_right',
            'joint_bar2_front_right',
            'joint_bar1_back_right',
            'joint_bar2_back_right',
            'joint_table_link',
        ]
        msg.position = [
            screw_angle,
            screw_angle,
            nut_travel,
            nut_travel,
            pair_states[0]['center_y_offset'],
            pair_states[0]['center_z_offset'],
            pair_states[1]['center_y_offset'],
            pair_states[1]['center_z_offset'],
            pair_states[2]['center_y_offset'],
            pair_states[2]['center_z_offset'],
            pair_states[3]['center_y_offset'],
            pair_states[3]['center_z_offset'],
            pair_states[0]['bar1_angle'],
            pair_states[0]['bar2_angle'],
            pair_states[1]['bar1_angle'],
            pair_states[1]['bar2_angle'],
            pair_states[2]['bar1_angle'],
            pair_states[2]['bar2_angle'],
            pair_states[3]['bar1_angle'],
            pair_states[3]['bar2_angle'],
            table_lift,
        ]
        self.publisher.publish(msg)


def main() -> None:
    rclpy.init()
    node = ScissorMechanismPublisher()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
