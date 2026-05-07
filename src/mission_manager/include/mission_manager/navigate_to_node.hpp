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
            goal_msg.pose.pose.position.x = 2.00; goal_msg.pose.pose.position.y = 0.50; 
        } else if (location == "B") { 
            goal_msg.pose.pose.position.x = 2.00; goal_msg.pose.pose.position.y = -0.50; 
        } else if (location == "X" || location == "HOME") { 
            goal_msg.pose.pose.position.x = 0.00; goal_msg.pose.pose.position.y = 0.00; 
        } else if (location == "Y" || location == "EXIT") { 
            goal_msg.pose.pose.position.x = 2.00; goal_msg.pose.pose.position.y = -0.50; 
        } else { 
            goal_msg.pose.pose.position.x = 0.00; goal_msg.pose.pose.position.y = 0.00; 
        } 
        goal_msg.pose.pose.orientation.w = 1.0;
        // Note: For simplicity, all orientations are set to 0. Adjust as needed for specific locations.
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