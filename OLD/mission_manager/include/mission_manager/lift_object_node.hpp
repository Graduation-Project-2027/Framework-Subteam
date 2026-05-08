#pragma once
#include <behaviortree_cpp/action_node.h>
#include <rclcpp/rclcpp.hpp>
#include <rclcpp_lifecycle/lifecycle_node.hpp>
#include <rclcpp_action/rclcpp_action.hpp>
#include <custom_interfaces/action/lift_to_height.hpp>

class LiftObjectNode : public BT::StatefulActionNode
{
public:
    using LiftAction = custom_interfaces::action::LiftToHeight;

    LiftObjectNode(const std::string& name, const BT::NodeConfig& config, std::shared_ptr<rclcpp_lifecycle::LifecycleNode> node)
      : BT::StatefulActionNode(name, config), node_(node)
    {
        client_ = rclcpp_action::create_client<LiftAction>(node_, "/lift_to_height");
    }

    static BT::PortsList providedPorts()
    {
        return { BT::InputPort<double>("target_height") };
    }

    BT::NodeStatus onStart() override
    {
        if (!client_->wait_for_action_server(std::chrono::seconds(3))) {
            RCLCPP_ERROR(node_->get_logger(), "Lift Action server not available");
            return BT::NodeStatus::FAILURE;
        }

        double target_height = 0.0;
        getInput("target_height", target_height);

        auto goal_msg = LiftAction::Goal();
        goal_msg.target_height = target_height;

        auto send_goal_options = rclcpp_action::Client<LiftAction>::SendGoalOptions();
        auto goal_handle_future = client_->async_send_goal(goal_msg, send_goal_options);
        
        if (goal_handle_future.wait_for(std::chrono::seconds(2)) != std::future_status::ready) {
            RCLCPP_ERROR(node_->get_logger(), "Lift Action Server did not respond.");
            return BT::NodeStatus::FAILURE;
        }

        goal_handle_ = goal_handle_future.get();
        if (!goal_handle_) return BT::NodeStatus::FAILURE;

        result_future_ = client_->async_get_result(goal_handle_);
        
        RCLCPP_INFO(node_->get_logger(), "Lifting to %.2f meters...", target_height);
        return BT::NodeStatus::RUNNING; 
    }

    BT::NodeStatus onRunning() override
    {
        if (result_future_.wait_for(std::chrono::milliseconds(50)) == std::future_status::ready) {
            auto result = result_future_.get();
            if (result.code == rclcpp_action::ResultCode::SUCCEEDED) {
                RCLCPP_INFO(node_->get_logger(), "Lift operation complete!");
                return BT::NodeStatus::SUCCESS;
            } else {
                return BT::NodeStatus::FAILURE;
            }
        }
        return BT::NodeStatus::RUNNING;
    }

    void onHalted() override { RCLCPP_INFO(node_->get_logger(), "Lift halted."); }

private:
    std::shared_ptr<rclcpp_lifecycle::LifecycleNode> node_; 
    rclcpp_action::Client<LiftAction>::SharedPtr client_;
    rclcpp_action::ClientGoalHandle<LiftAction>::SharedPtr goal_handle_;
    std::shared_future<rclcpp_action::ClientGoalHandle<LiftAction>::WrappedResult> result_future_;
};