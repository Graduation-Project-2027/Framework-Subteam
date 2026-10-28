#ifndef MICROROS_CONFIG_H
#define MICROROS_CONFIG_H

#ifndef LED_BUILTIN
#define LED_BUILTIN 2 
#endif

#include <micro_ros_arduino.h>
#include <stdio.h>
#include <rcl/rcl.h>
#include <rcl/error_handling.h>
#include <rclc/rclc.h>
#include <rclc/executor.h>
#include <std_msgs/msg/float64.h>   // matches SIM: /lift_joint/cmd_pos is Float64
#include <WiFi.h>

// Updated RCCHECK to print out exactly which step failed before locking up
#define RCCHECK(fn, msg) { rcl_ret_t temp_rc = fn; if((temp_rc != RCL_RET_OK)){ Serial.print("FAIL: "); Serial.println(msg); error_loop(); }}
#define RCSOFTCHECK(fn) { rcl_ret_t temp_rc = fn; if((temp_rc != RCL_RET_OK)){}}

void error_loop(){
  while(1){
    digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN));
    delay(100);
  }
}

rcl_subscription_t subscriber;
std_msgs__msg__Float64 msg;   // incoming lift height (metres) from the SIM

rclc_executor_t executor;
rclc_support_t support;
rcl_allocator_t allocator;
rcl_node_t node;

void setup_microros(const char* node_name, const char* topic_name, void (*subscription_callback)(const void *)) {
  
  // 1. Force native Wi-Fi connection layer first
  Serial.print("Connecting to Wi-Fi SSID: Ejust");
  WiFi.begin("Ejust", "11111111");
  
  int timeout_counter = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    timeout_counter++;
    if(timeout_counter > 30) {
      Serial.println("\nWi-Fi connection timeout! Restarting...");
      ESP.restart();
    }
  }
  
  Serial.println("\nWi-Fi Connected Successfully!");
  Serial.print("ESP32 IP Address: ");
  Serial.println(WiFi.localIP());

  // 2. Hand off the established connection to the micro-ROS transport layer
  set_microros_wifi_transports("Ejust", "11111111", "192.168.137.35", 8888);
  delay(1000);

  Serial.println("Initializing micro-ROS layer...");
  allocator = rcl_get_default_allocator();
  
  rcl_init_options_t init_options = rcl_get_zero_initialized_init_options();
  RCCHECK(rcl_init_options_init(&init_options, allocator), "Init Options Fail");
  // MUST match the SIM's ROS_DOMAIN_ID (this project runs on domain 0).
  RCCHECK(rcl_init_options_set_domain_id(&init_options, 0), "Domain ID Fail");

  RCCHECK(rclc_support_init_with_options(&support, 0, NULL, &init_options, &allocator), "Support Init Fail");
  RCCHECK(rclc_node_init_default(&node, node_name, "", &support), "Node Init Fail");

  RCCHECK(rclc_subscription_init_default(
    &subscriber, 
    &node, 
    ROSIDL_GET_MSG_TYPE_SUPPORT(std_msgs, msg, Float64),
    topic_name), "Subscription Init Fail");

  // Float64 is a fixed-size message — no buffer allocation needed.

  RCCHECK(rclc_executor_init(&executor, &support.context, 1, &allocator), "Executor Init Fail");
  RCCHECK(rclc_executor_add_subscription(&executor, &subscriber, &msg, subscription_callback, ON_NEW_DATA), "Callback Link Fail");
  
  Serial.println("micro-ROS fully initialized and running!");
}

void spin_microros() {
  RCSOFTCHECK(rclc_executor_spin_some(&executor, RCL_MS_TO_NS(10)));
}

#endif