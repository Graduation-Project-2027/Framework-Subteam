#!/bin/bash
# ──────────────────────────────────────────────────────────────────────
# AGV Mission Control — Dependency Installer
# Run once after cloning or on a fresh system.
# Usage:  bash src/agv_mission_control/install_deps.sh
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

echo "═══════════════════════════════════════════════════════════════"
echo "  AGV Mission Control — Installing Dependencies"
echo "═══════════════════════════════════════════════════════════════"

# ── 1. ROS 2 Packages (apt) ──────────────────────────────────────────
echo ""
echo "▸ Installing ROS 2 apt packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
  ros-jazzy-rosbridge-suite \
  ros-jazzy-nav2-msgs \
  ros-jazzy-geometry-msgs \
  ros-jazzy-std-msgs \
  ros-jazzy-sensor-msgs \
  ros-jazzy-nav-msgs \
  ros-jazzy-tf2-ros \
  ros-jazzy-tf2-geometry-msgs

echo "  ✓ ROS 2 packages installed"

# ── 2. Node.js & npm ─────────────────────────────────────────────────
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
    echo "  ERROR: npm not found even after Node.js install"
    exit 1
else
    echo "  ✓ npm $(npm --version) found"
fi

# ── 3. Vite GUI Dependencies (npm) ───────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUI_DIR="${SCRIPT_DIR}/gui"

if [ -f "${GUI_DIR}/package.json" ]; then
    echo ""
    echo "▸ Installing GUI npm dependencies..."
    cd "${GUI_DIR}"
    npm install --silent
    echo "  ✓ GUI npm dependencies installed"
else
    echo ""
    echo "  ⚠ GUI not yet scaffolded (no package.json). Skipping npm install."
    echo "    Run this script again after the GUI is created."
fi

# ── 4. Python packages (pip, if any extras needed) ────────────────────
echo ""
echo "▸ Checking Python dependencies..."
python3 -c "import rclpy" 2>/dev/null && echo "  ✓ rclpy available" || echo "  ⚠ rclpy not found — source ROS 2 first"

# ── 5. Convert PGM map to PNG for web serving ────────────────────────
echo ""
echo "▸ Checking for map conversion tools..."
if command -v convert &> /dev/null; then
    echo "  ✓ ImageMagick available"
elif command -v ffmpeg &> /dev/null; then
    echo "  ✓ ffmpeg available (fallback for image conversion)"
else
    echo "  Installing ImageMagick for PGM→PNG conversion..."
    sudo apt-get install -y -qq imagemagick
    echo "  ✓ ImageMagick installed"
fi

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  ✓ All dependencies installed successfully!"
echo ""
echo "  Next steps:"
echo "    1. source /opt/ros/jazzy/setup.bash"
echo "    2. colcon build --symlink-install --packages-select agv_mission_control"
echo "    3. source install/setup.bash"
echo "    4. ros2 launch agv_mission_control mission_control.launch.py"
echo "═══════════════════════════════════════════════════════════════"
