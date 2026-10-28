import os
from glob import glob

from setuptools import setup

package_name = "amr_hardware"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "launch"), glob("launch/*.launch.py")),
        (os.path.join("share", package_name, "config"), glob("config/*.yaml")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="AMR Team",
    maintainer_email="team@amr.local",
    description="Real-robot drivers: CAN bridge (hoverboard + e-stop controller), MPU9250 IMU and dual RPLidar launch",
    license="MIT",
    entry_points={
        "console_scripts": [
            "can_bridge = amr_hardware.can_bridge:main",
            "mpu9250_driver = amr_hardware.mpu9250_driver:main",
        ],
    },
)
