#!/bin/bash
# ──────────────────────────────────────────────────────────────────────
# AMR — dependency installer. Run once per machine, from any directory.
#
#   bash install_dependencies.sh         # robot (Raspberry Pi 5): drivers, Nav2, SLAM, mission control
#   bash install_dependencies.sh --sim   # development PC: everything above + Gazebo + RViz
# ──────────────────────────────────────────────────────────────────────
set -eo pipefail

SIM=false
case "${1:-}" in
    --sim) SIM=true ;;
    "") ;;
    *) echo "Usage: bash install_dependencies.sh [--sim]"; exit 1 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "═══════════════════════════════════════════════════════════════"
if $SIM; then
    echo "  AMR — installing dependencies (development PC, with simulation)"
else
    echo "  AMR — installing dependencies (robot, no simulation)"
fi
echo "═══════════════════════════════════════════════════════════════"

# ── 0. ROS 2 Jazzy ───────────────────────────────────────────────────
if [ -f /opt/ros/jazzy/setup.bash ]; then
    source /opt/ros/jazzy/setup.bash
    echo "  ✓ ROS 2 Jazzy found"
else
    echo "  ✗ ROS 2 Jazzy not found at /opt/ros/jazzy"
    echo "    Install it first: https://docs.ros.org/en/jazzy/Installation.html"
    exit 1
fi

# ── 1. apt packages ──────────────────────────────────────────────────
CORE_PKGS=(
    ros-jazzy-robot-state-publisher
    ros-jazzy-xacro
    ros-jazzy-robot-localization
    ros-jazzy-nav2-bringup
    ros-jazzy-nav2-map-server
    ros-jazzy-nav2-msgs
    ros-jazzy-slam-toolbox
    ros-jazzy-laser-filters
    ros-jazzy-rosbridge-suite
    ros-jazzy-tf2-ros
    ros-jazzy-tf2-geometry-msgs
    python3-rosdep
)
HARDWARE_PKGS=(
    ros-jazzy-rplidar-ros
    ros-jazzy-imu-filter-madgwick
    python3-can
    python3-smbus2
    can-utils
    i2c-tools
)
SIM_PKGS=(
    ros-jazzy-ros-gz-sim
    ros-jazzy-ros-gz-bridge
    ros-jazzy-rviz2
    ros-jazzy-urdf-launch
    ros-jazzy-joint-state-publisher-gui
)

echo ""
echo "▸ Installing apt packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq "${CORE_PKGS[@]}" "${HARDWARE_PKGS[@]}"
if $SIM; then
    sudo apt-get install -y -qq "${SIM_PKGS[@]}"
fi
echo "  ✓ apt packages installed"

# ── 2. rosdep (anything else declared in the package.xml files) ──────
echo ""
echo "▸ Resolving package.xml dependencies with rosdep..."
sudo rosdep init 2>/dev/null || true
rosdep update --rosdistro=jazzy
ROSDEP_ARGS=(--from-paths "${SCRIPT_DIR}/src" --ignore-src -r -y)
if ! $SIM; then
    # Simulation / desktop-only keys: keep Gazebo and RViz off the robot.
    ROSDEP_ARGS+=(--skip-keys "ros_gz_sim ros_gz_bridge rviz2 urdf_launch")
fi
rosdep install "${ROSDEP_ARGS[@]}"
echo "  ✓ rosdep dependencies resolved"

# ── 3. Node.js + mission control GUI ─────────────────────────────────
echo ""
echo "▸ Checking Node.js..."
if ! command -v node &> /dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y -qq nodejs
fi
echo "  ✓ Node.js $(node --version), npm $(npm --version)"

GUI_DIR="${SCRIPT_DIR}/src/amr_mission_control/gui"
echo "▸ Installing GUI npm dependencies..."
(cd "${GUI_DIR}" && npm install --silent)
echo "  ✓ GUI dependencies installed"

# ── 4. Robot only: hardware access ───────────────────────────────────
if ! $SIM; then
    echo ""
    echo "▸ Granting ${USER} access to serial (lidars) and I2C (IMU)..."
    sudo usermod -aG dialout "${USER}"
    if getent group i2c > /dev/null; then
        sudo usermod -aG i2c "${USER}"
    fi
    echo "  ✓ Done — log out and back in for the group change to apply"
    echo "    CAN interface setup (can0) is described in docs/hardware.md"
fi

# ── 5. Clean stale build artifacts ───────────────────────────────────
echo ""
echo "▸ Cleaning build/, install/, log/..."
rm -rf "${SCRIPT_DIR}/build" "${SCRIPT_DIR}/install" "${SCRIPT_DIR}/log"

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  ✓ All dependencies installed"
echo ""
echo "  Next:"
echo "    cd ${SCRIPT_DIR}"
echo "    colcon build --symlink-install && source install/setup.bash"
if $SIM; then
    echo "    ros2 launch amr_navigation navigation.launch.py sim:=true"
else
    echo "    ros2 launch amr_navigation navigation.launch.py"
fi
echo "═══════════════════════════════════════════════════════════════"
