cmake_minimum_required(VERSION 3.8)
project(mission_manager)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

include_directories(include)

# find dependencies
find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclcpp_lifecycle REQUIRED)
find_package(rclcpp_action REQUIRED)
find_package(std_msgs REQUIRED)
find_package(custom_interfaces REQUIRED)
find_package(nav2_msgs REQUIRED)
find_package(behaviortree_cpp REQUIRED)
find_package(ament_index_cpp REQUIRED)


add_executable(mission_manager_node src/mission_manager_node.cpp)

target_include_directories(mission_manager_node PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>
)

ament_target_dependencies(mission_manager_node
  rclcpp
  rclcpp_lifecycle
  rclcpp_action
  std_msgs
  custom_interfaces
  nav2_msgs
  ament_index_cpp
  behaviortree_cpp
)

install(TARGETS
  mission_manager_node 
  DESTINATION lib/${PROJECT_NAME}
)

install(DIRECTORY
  behavior_trees
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
