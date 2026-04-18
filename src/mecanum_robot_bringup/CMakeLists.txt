cmake_minimum_required(VERSION 3.8)
project(mecanum_robot_bringup)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)

install(
    DIRECTORY launch config worlds rviz
    DESTINATION share/${PROJECT_NAME}/
)

install(
    PROGRAMS scripts/rich_teleop_tui.py
    DESTINATION lib/${PROJECT_NAME}
    RENAME rich_teleop_tui
)

ament_package()
