from setuptools import find_packages, setup

package_name = 'lift_control'

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
    description='Action server for the mobile manipulator lift mechanism',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'lift_controller_node = lift_control.lift_controller_node:main'
        ],
    },
)
