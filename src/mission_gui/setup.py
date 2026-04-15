from setuptools import find_packages, setup

package_name = 'mission_gui'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='youssef-adel',
    maintainer_email='youssefsaeed11004@gmail.com',
    description='Prompts the user for a target location and publishes the command.',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'gui_command_node = mission_gui.gui_command_node:main',
            'gui = mission_gui.gp_gui:main',
        ],
    },
)
