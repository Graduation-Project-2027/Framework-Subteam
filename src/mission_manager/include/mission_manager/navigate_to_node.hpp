#pragma once
#include <behaviortree_cpp/action_node.h>
#include <rclcpp/rclcpp.hpp>
#include <rclcpp_lifecycle/lifecycle_node.hpp>
#include <rclcpp_action/rclcpp_action.hpp>
#include <nav2_msgs/action/navigate_to_pose.hpp>
#include <std_msgs/msg/string.hpp>

class NavigateToNode : public BT::StatefulActionNode
{
public:
    using NavigateAction = nav2_msgs::action::NavigateToPose;

    NavigateToNode(const std::string& name, const BT::NodeConfig& config, std::shared_ptr<rclcpp_lifecycle::LifecycleNode> node)
      : BT::StatefulActionNode(name, config), node_(node)
    {
        client_ = rclcpp_action::create_client<NavigateAction>(node_, "/navigate_to_pose"); 
        feedback_pub_ = node_->create_publisher<std_msgs::msg::String>("/mission_feedback", 10);
        feedback_pub_->on_activate(); 

    }

    static BT::PortsList providedPorts()
    {
        return { BT::InputPort<std::string>("target_pose") }; 
    }

    BT::NodeStatus onStart() override
    {
        if (!client_->wait_for_action_server(std::chrono::seconds(2))) {
            return BT::NodeStatus::FAILURE;
        }

        std::string location;
        getInput("target_pose", location);

        std::string feedback_str = "MOVING";
        if (location == "A") { 
            feedback_str = "GOING_TO_PICK";
        } else if (location == "B") { 
            feedback_str = "GOING_TO_DROP"; 
        } else if (location == "X" || location == "HOME") { 
            feedback_str = "GOING_TO_HOME"; 
        } else if (location == "Y" || location == "EXIT") { 
            feedback_str = "GOING_TO_EXIT"; 
        }
        
        // Publish the starting state
        publish_feedback(feedback_str);

        auto goal_msg = NavigateAction::Goal();
        goal_msg.pose.header.frame_id = "map";
        goal_msg.pose.header.stamp = node_->now();

        ////////////////////////////////////////////////////////////////////////////////
        // Define coordinates for known locations
        if (location == "A") { 
            // Updated with new Frame:map coordinates!
            goal_msg.pose.pose.position.x = 2.60; 
            goal_msg.pose.pose.position.y = -0.199286; 
            goal_msg.pose.pose.orientation.z = 0.01345;
            goal_msg.pose.pose.orientation.w = 0.99991;
        } else if (location == "B") { 
            // NOTE: You still need to capture map-frame coordinates for B!
            // Currently using your old base_footprint ones as a placeholder.
            goal_msg.pose.pose.position.x = -0.01803; 
            goal_msg.pose.pose.position.y = -0.01243; 
            goal_msg.pose.pose.orientation.z = 0.008688;
            goal_msg.pose.pose.orientation.w = 0.999962;
        } else if (location == "X" || location == "HOME") { 
            // Using your default spawn coordinates for HOME
            goal_msg.pose.pose.position.x = 0.0; 
            goal_msg.pose.pose.position.y = 3.5; 
            goal_msg.pose.pose.orientation.z = 0.0;
            goal_msg.pose.pose.orientation.w = 1.0;
        } else if (location == "Y" || location == "EXIT") { 
            // Updated with your new X, Y coordinates. 
            // Orientation defaults to 0.0 rotation (w=1.0) since it wasn't provided.
            goal_msg.pose.pose.position.x = 2.717; 
            goal_msg.pose.pose.position.y = -2.439; 
            goal_msg.pose.pose.orientation.z = 0.0;
            goal_msg.pose.pose.orientation.w = 1.0;
        } else { 
            // Default fallback (Home)
            goal_msg.pose.pose.position.x = 0.0; 
            goal_msg.pose.pose.position.y = 3.5; 
            goal_msg.pose.pose.orientation.z = 0.0;
            goal_msg.pose.pose.orientation.w = 1.0;
        }
        // Ensure x and y orientation are always 0 for 2D planar navigation
        goal_msg.pose.pose.orientation.x = 0.0;
        goal_msg.pose.pose.orientation.y = 0.0;

        auto send_goal_options = rclcpp_action::Client<NavigateAction>::SendGoalOptions();
        auto goal_handle_future = client_->async_send_goal(goal_msg, send_goal_options);
        ////////////////////////////////////////////////////////////////////////////////

        if (goal_handle_future.wait_for(std::chrono::seconds(2)) != std::future_status::ready) {
            RCLCPP_ERROR(node_->get_logger(), "Action Server did not respond to goal request.");
            return BT::NodeStatus::FAILURE;
        }

        goal_handle_ = goal_handle_future.get();
        if (!goal_handle_) return BT::NodeStatus::FAILURE;

        result_future_ = client_->async_get_result(goal_handle_);
        
        RCLCPP_INFO(node_->get_logger(), "Nav2 goal sent to location: %s. Moving...", location.c_str());
        return BT::NodeStatus::RUNNING; 
    }

    BT::NodeStatus onRunning() override
    {
        if (result_future_.wait_for(std::chrono::milliseconds(50)) == std::future_status::ready) {
            auto result = result_future_.get();
            if (result.code == rclcpp_action::ResultCode::SUCCEEDED) {
                RCLCPP_INFO(node_->get_logger(), "Arrived at destination!");
                publish_feedback("REACHED_THE_POINT");
                return BT::NodeStatus::SUCCESS;
            } else {
                RCLCPP_ERROR(node_->get_logger(), "Failed to reach destination.");
                publish_feedback("NAVIGATION_FAILED");
                return BT::NodeStatus::FAILURE;
            }
        }
        return BT::NodeStatus::RUNNING;
    }

    void onHalted() override { RCLCPP_INFO(node_->get_logger(), "Navigation halted."); }

private:
    std::shared_ptr<rclcpp_lifecycle::LifecycleNode> node_; 
    rclcpp_action::Client<NavigateAction>::SharedPtr client_;
    rclcpp_action::ClientGoalHandle<NavigateAction>::SharedPtr goal_handle_;
    std::shared_future<rclcpp_action::ClientGoalHandle<NavigateAction>::WrappedResult> result_future_;

    std::shared_ptr<rclcpp_lifecycle::LifecyclePublisher<std_msgs::msg::String>> feedback_pub_;

    void publish_feedback(const std::string& msg_str) {
        std_msgs::msg::String msg;
        msg.data = msg_str;
        feedback_pub_->publish(msg);
    }
};