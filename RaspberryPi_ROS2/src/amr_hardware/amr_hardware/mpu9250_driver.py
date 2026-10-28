"""MPU9250 IMU driver (MPU6500 accel/gyro + AK8963 magnetometer) over I2C.

Publishes:   imu/data_raw     sensor_msgs/Imu            accel [m/s^2] + gyro [rad/s], no orientation
             imu/mag          sensor_msgs/MagneticField  [T], expressed in the accel/gyro axes
             imu/temperature  sensor_msgs/Temperature    die temperature [degC]
Services:    imu/calibrate_gyro  std_srvs/Trigger       re-estimate the gyro bias (robot must be still)

Orientation is estimated downstream by imu_filter_madgwick (imu/data_raw -> imu/data).
"""

import math
import time

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from sensor_msgs.msg import Imu, MagneticField, Temperature
from smbus2 import SMBus
from std_srvs.srv import Trigger

GRAVITY = 9.80665

# MPU6500 registers
SMPLRT_DIV = 0x19
CONFIG = 0x1A
GYRO_CONFIG = 0x1B
ACCEL_CONFIG = 0x1C
ACCEL_CONFIG2 = 0x1D
INT_PIN_CFG = 0x37
ACCEL_XOUT_H = 0x3B
USER_CTRL = 0x6A
PWR_MGMT_1 = 0x6B
PWR_MGMT_2 = 0x6C
WHO_AM_I = 0x75
MPU9250_IDS = (0x71, 0x73)  # MPU9250, MPU9255

# AK8963 magnetometer (reached through the MPU's I2C bypass)
AK8963_ADDRESS = 0x0C
AK8963_WIA = 0x00
AK8963_ID = 0x48
AK8963_ST1 = 0x02
AK8963_HXL = 0x03
AK8963_CNTL1 = 0x0A
AK8963_ASAX = 0x10
AK8963_HOFL = 0x08
AK8963_UT_PER_LSB = 0.15  # 16-bit output

ACCEL_RANGES_G = {2: 0, 4: 1, 8: 2, 16: 3}
GYRO_RANGES_DPS = {250: 0, 500: 1, 1000: 2, 2000: 3}


def _int16_be(data, i):
    v = (data[i] << 8) | data[i + 1]
    return v - 65536 if v & 0x8000 else v


def _int16_le(data, i):
    v = data[i] | (data[i + 1] << 8)
    return v - 65536 if v & 0x8000 else v


class Mpu9250:
    """Register-level access to the chip. All methods raise OSError on I2C failure."""

    def __init__(self, bus, address, accel_range_g, gyro_range_dps, rate_hz):
        self.bus = SMBus(bus)
        self.address = address
        self.chip_id = self.bus.read_byte_data(address, WHO_AM_I)

        accel_fs = ACCEL_RANGES_G[accel_range_g]
        gyro_fs = GYRO_RANGES_DPS[gyro_range_dps]
        self.accel_scale = GRAVITY / (16384.0 / (1 << accel_fs))
        self.gyro_scale = math.radians(1.0) / (131.0 / (1 << gyro_fs))

        self._write(PWR_MGMT_1, 0x80)  # reset
        time.sleep(0.1)
        self._write(PWR_MGMT_1, 0x01)  # wake, PLL clock
        time.sleep(0.05)
        self._write(PWR_MGMT_2, 0x00)  # all axes on
        self._write(CONFIG, 0x03)  # gyro low-pass 41 Hz
        self._write(SMPLRT_DIV, max(0, min(255, round(1000.0 / rate_hz) - 1)))
        self._write(GYRO_CONFIG, gyro_fs << 3)
        self._write(ACCEL_CONFIG, accel_fs << 3)
        self._write(ACCEL_CONFIG2, 0x03)  # accel low-pass 45 Hz
        self._write(USER_CTRL, 0x00)  # I2C master off ...
        self._write(INT_PIN_CFG, 0x02)  # ... and bypass on, so the AK8963 is on the bus
        time.sleep(0.01)
        self.mag_adjust = self._init_magnetometer()

    def _write(self, reg, value, address=None):
        self.bus.write_byte_data(self.address if address is None else address, reg, value)

    def _init_magnetometer(self):
        """Returns per-axis sensitivity adjustment, or None if no AK8963 answers."""
        try:
            if self.bus.read_byte_data(AK8963_ADDRESS, AK8963_WIA) != AK8963_ID:
                return None
        except OSError:
            return None
        self._write(AK8963_CNTL1, 0x00, AK8963_ADDRESS)  # power down
        time.sleep(0.01)
        self._write(AK8963_CNTL1, 0x0F, AK8963_ADDRESS)  # fuse ROM access
        time.sleep(0.01)
        asa = self.bus.read_i2c_block_data(AK8963_ADDRESS, AK8963_ASAX, 3)
        self._write(AK8963_CNTL1, 0x00, AK8963_ADDRESS)
        time.sleep(0.01)
        self._write(AK8963_CNTL1, 0x16, AK8963_ADDRESS)  # 16 bit, continuous 100 Hz
        time.sleep(0.01)
        return [(a - 128) / 256.0 + 1.0 for a in asa]

    def read_motion(self):
        """Returns (accel xyz [m/s^2], gyro xyz [rad/s], temperature [degC])."""
        d = self.bus.read_i2c_block_data(self.address, ACCEL_XOUT_H, 14)
        accel = [_int16_be(d, i) * self.accel_scale for i in (0, 2, 4)]
        temperature = _int16_be(d, 6) / 333.87 + 21.0
        gyro = [_int16_be(d, i) * self.gyro_scale for i in (8, 10, 12)]
        return accel, gyro, temperature

    def read_magnetometer(self):
        """Returns field xyz [T] in the accel/gyro axes, or None if no new sample."""
        if self.mag_adjust is None:
            return None
        if not self.bus.read_byte_data(AK8963_ADDRESS, AK8963_ST1) & 0x01:
            return None
        # Reading through ST2 (7 bytes) releases the data registers for the next sample.
        d = self.bus.read_i2c_block_data(AK8963_ADDRESS, AK8963_HXL, 7)
        if d[6] & AK8963_HOFL:
            return None
        raw = [_int16_le(d, i) * AK8963_UT_PER_LSB * 1e-6 * adj
               for i, adj in zip((0, 2, 4), self.mag_adjust)]
        # AK8963 axes: X = accel Y, Y = accel X, Z = -accel Z.
        return [raw[1], raw[0], -raw[2]]

    def close(self):
        self.bus.close()


class Mpu9250Driver(Node):
    def __init__(self):
        super().__init__("mpu9250")

        def param(name, default):
            return self.declare_parameter(name, default).value

        self.frame_id = param("frame_id", "imu_link")
        rate = param("rate", 100.0)
        self.calibration_samples = param("gyro_calibration_samples", 200)
        accel_std = param("linear_acceleration_stddev", 0.05)
        gyro_std = param("angular_velocity_stddev", 0.005)
        mag_std = param("magnetic_field_stddev", 1.0e-6)
        self.accel_cov = [accel_std ** 2, 0.0, 0.0, 0.0, accel_std ** 2, 0.0, 0.0, 0.0, accel_std ** 2]
        self.gyro_cov = [gyro_std ** 2, 0.0, 0.0, 0.0, gyro_std ** 2, 0.0, 0.0, 0.0, gyro_std ** 2]
        self.mag_cov = [mag_std ** 2, 0.0, 0.0, 0.0, mag_std ** 2, 0.0, 0.0, 0.0, mag_std ** 2]

        bus = param("i2c_bus", 1)
        address = param("i2c_address", 104)
        self.imu = Mpu9250(
            bus,
            address,
            param("accel_range_g", 4),
            param("gyro_range_dps", 500),
            rate,
        )
        if self.imu.chip_id not in MPU9250_IDS:
            self.get_logger().warning(
                f"WHO_AM_I = 0x{self.imu.chip_id:02X}, expected an MPU9250 (0x71/0x73)")
        if self.imu.mag_adjust is None:
            self.get_logger().warning("AK8963 magnetometer not found: imu/mag will not be published")

        self.dt = 1.0 / rate
        self.gyro_bias = [0.0, 0.0, 0.0]
        self._last_mag = None
        self._calibrate_gyro()

        self.imu_pub = self.create_publisher(Imu, "imu/data_raw", 10)
        self.mag_pub = self.create_publisher(MagneticField, "imu/mag", 10)
        self.temp_pub = self.create_publisher(Temperature, "imu/temperature", 10)
        self.create_service(Trigger, "imu/calibrate_gyro", self._on_calibrate)
        self.create_timer(self.dt, self._read)
        self.get_logger().info(f"MPU9250 on /dev/i2c-{bus} at 0x{address:02X}, {rate:.0f} Hz")

    def _calibrate_gyro(self):
        """Average the gyro while the robot is standing still."""
        self.get_logger().info("Calibrating gyro bias, keep the robot still...")
        total = [0.0, 0.0, 0.0]
        for _ in range(self.calibration_samples):
            _, gyro, _ = self.imu.read_motion()
            total = [t + g for t, g in zip(total, gyro)]
            time.sleep(self.dt)
        n = max(1, self.calibration_samples)
        self.gyro_bias = [t / n for t in total]
        self.get_logger().info(
            "Gyro bias [rad/s]: " + ", ".join(f"{b:+.5f}" for b in self.gyro_bias))

    def _on_calibrate(self, request, response):
        try:
            self._calibrate_gyro()
            response.success = True
            response.message = "Gyro bias: " + ", ".join(f"{b:+.5f}" for b in self.gyro_bias)
        except OSError as e:
            response.success = False
            response.message = f"I2C error: {e}"
        return response

    def _read(self):
        try:
            accel, gyro, temperature = self.imu.read_motion()
            mag = self.imu.read_magnetometer()
        except OSError as e:
            self.get_logger().warning(f"I2C read failed: {e}", throttle_duration_sec=2.0)
            return
        if mag is not None:
            self._last_mag = mag

        header_stamp = self.get_clock().now().to_msg()

        imu = Imu()
        imu.header.stamp = header_stamp
        imu.header.frame_id = self.frame_id
        imu.orientation_covariance[0] = -1.0  # no orientation in the raw message
        imu.linear_acceleration.x, imu.linear_acceleration.y, imu.linear_acceleration.z = accel
        imu.linear_acceleration_covariance = self.accel_cov
        gx, gy, gz = (g - b for g, b in zip(gyro, self.gyro_bias))
        imu.angular_velocity.x, imu.angular_velocity.y, imu.angular_velocity.z = gx, gy, gz
        imu.angular_velocity_covariance = self.gyro_cov
        self.imu_pub.publish(imu)

        # Same stamp as imu/data_raw so imu_filter_madgwick can pair them.
        if self._last_mag is not None:
            field = MagneticField()
            field.header = imu.header
            field.magnetic_field.x, field.magnetic_field.y, field.magnetic_field.z = self._last_mag
            field.magnetic_field_covariance = self.mag_cov
            self.mag_pub.publish(field)

        temp = Temperature()
        temp.header = imu.header
        temp.temperature = temperature
        self.temp_pub.publish(temp)

    def destroy_node(self):
        self.imu.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    try:
        node = Mpu9250Driver()
    except OSError as e:
        rclpy.logging.get_logger("mpu9250").fatal(
            f"Cannot talk to the MPU9250: {e}. See docs/hardware.md (IMU).")
        rclpy.try_shutdown()
        raise SystemExit(1)
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
