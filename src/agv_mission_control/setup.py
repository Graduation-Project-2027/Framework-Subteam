from setuptools import setup
import os
from glob import glob

package_name = "agv_mission_control"


def find_data_files(source, target_base):
    """Walk a directory tree and produce (target_dir, [files]) pairs for data_files."""
    result = []
    for root, dirs, files in os.walk(source):
        if not files:
            continue
        rel = os.path.relpath(root, source)
        target = os.path.join(target_base, rel) if rel != "." else target_base
        result.append((target, [os.path.join(root, f) for f in files]))
    return result


gui_data = find_data_files("gui/dist", os.path.join("share", package_name, "gui", "dist")) if os.path.isdir("gui/dist") else []

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "launch"), glob("launch/*.py")),
        (os.path.join("share", package_name, "config"), glob("config/*")),
    ] + gui_data,
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="AGV Team",
    maintainer_email="team@agv.local",
    description="AGV Mission Control — Web GUI + ROS 2 mission manager",
    license="MIT",
    entry_points={
        "console_scripts": [
            "mission_manager = agv_mission_control.mission_manager_node:main",
        ],
    },
)
