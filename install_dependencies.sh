#!/bin/bash
# ──────────────────────────────────────────────────────────────────────
# AGV Framework — Full Dependency Installer
# Run once on a fresh system or after cloning the repo on a new PC.
# Usage:  bash install_dependencies.sh
# ──────────────────────────────────────────────────────────────────────
set -eo pipefail

echo "═══════════════════════════════════════════════════════════════"
echo "  AGV Framework — Installing ALL Dependencies"
echo "═══════════════════════════════════════════════════════════════"

# ── 0. Verify ROS 2 Jazzy is installed ────────────────────────────────
echo ""
echo "▸ Checking ROS 2 Jazzy base installation..."
if [ -f /opt/ros/jazzy/setup.bash ]; then
    echo "  ✓ ROS 2 Jazzy found"
    source /opt/ros/jazzy/setup.bash
else
    echo "  ✗ ROS 2 Jazzy not found at /opt/ros/jazzy"
    echo "    Please install ROS 2 Jazzy first:"
    echo "    https://docs.ros.org/en/jazzy/Installation.html"
    exit 1
fi

# ── 1. Core Navigation & Simulation Packages ─────────────────────────
echo ""
echo "▸ Installing core ROS 2 packages (navigation, simulation, SLAM)..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
  ros-jazzy-robot-localization \
  ros-jazzy-nav2-bringup \
  ros-jazzy-nav2-map-server \
  ros-jazzy-nav2-msgs \
  ros-jazzy-laser-filters \
  ros-jazzy-slam-toolbox \
  ros-jazzy-ros-gz-sim \
  ros-jazzy-ros-gz-bridge \
  ros-jazzy-robot-state-publisher \
  ros-jazzy-rviz2 \
  ros-jazzy-tf2-ros \
  ros-jazzy-tf2-geometry-msgs \
  ros-jazzy-geometry-msgs \
  ros-jazzy-nav-msgs \
  ros-jazzy-std-msgs \
  ros-jazzy-sensor-msgs

echo "  ✓ Core ROS 2 packages installed"

# ── 2. Mission Control Packages ──────────────────────────────────────
echo ""
echo "▸ Installing mission control ROS 2 packages..."
sudo apt-get install -y -qq \
  ros-jazzy-rosbridge-suite

echo "  ✓ Mission control ROS 2 packages installed"

# ── 3. rosdep (catch any remaining dependencies) ─────────────────────
echo ""
echo "▸ Running rosdep to catch any remaining dependencies..."
if ! command -v rosdep &> /dev/null; then
    sudo apt-get install -y -qq python3-rosdep
fi

# Initialize rosdep if not already done (ignore errors if already initialized)
sudo rosdep init 2>/dev/null || true
rosdep update --rosdistro=jazzy

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
rosdep install --from-paths "${SCRIPT_DIR}/src" --ignore-src -r -y
echo "  ✓ rosdep dependencies resolved"

# ── 4. Node.js & npm (for Mission Control GUI) ───────────────────────
echo ""
echo "▸ Checking Node.js..."
if ! command -v node &> /dev/null; then
    echo "  Node.js not found. Installing via NodeSource..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y -qq nodejs
else
    echo "  ✓ Node.js $(node --version) found"
fi

if ! command -v npm &> /dev/null; then
    echo "  ⚠ npm not found even after Node.js install"
else
    echo "  ✓ npm $(npm --version) found"
fi

# ── 5. GUI npm dependencies ──────────────────────────────────────────
GUI_DIR="${SCRIPT_DIR}/src/agv_mission_control/gui"
if [ -f "${GUI_DIR}/package.json" ]; then
    echo ""
    echo "▸ Installing GUI npm dependencies..."
    cd "${GUI_DIR}"
    npm install --silent
    echo "  ✓ GUI npm dependencies installed"
    cd "${SCRIPT_DIR}"
else
    echo ""
    echo "  ⚠ GUI not yet scaffolded (no package.json). Skipping npm install."
fi

# ── 6. Map conversion tools ──────────────────────────────────────────
echo ""
echo "▸ Checking image conversion tools..."
if command -v convert &> /dev/null; then
    echo "  ✓ ImageMagick available"
elif command -v ffmpeg &> /dev/null; then
    echo "  ✓ ffmpeg available (fallback)"
else
    echo "  Installing ImageMagick for PGM→PNG conversion..."
    sudo apt-get install -y -qq imagemagick
    echo "  ✓ ImageMagick installed"
fi

# ── 7. Clean stale build artifacts ───────────────────────────────────
echo ""
echo "▸ Cleaning stale build artifacts..."
cd "${SCRIPT_DIR}"
rm -rf build install log
echo "  ✓ Cleaned build/, install/, log/"

# ── Done ──────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  ✓ All dependencies installed successfully!"
echo ""
echo "  Next steps:"
echo "    1. source /opt/ros/jazzy/setup.bash"
echo "    2. colcon build --symlink-install"
echo "    3. source install/setup.bash"
echo "    4. ros2 launch agv_navigation navigation_sim.launch.py mode:=2_lidars"
echo "═══════════════════════════════════════════════════════════════"
