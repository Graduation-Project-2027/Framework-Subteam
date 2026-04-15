#include <rclcpp/rclcpp.hpp>
#include <rclcpp_lifecycle/lifecycle_node.hpp>
#include <std_msgs/msg/string.hpp>
#include <std_msgs/msg/bool.hpp>
#include <custom_interfaces/msg/mission_request.hpp>
#include "lifecycle_msgs/msg/state.hpp"
#include <behaviortree_cpp/bt_factory.h>
#include "mission_manager/lift_object_node.hpp"
#include "mission_manager/navigate_to_node.hpp"
#include <ament_index_cpp/get_package_share_directory.hpp>
#include <thread>

using std::placeholders::_1;
using CallbackReturn = rclcpp_lifecycle::node_interfaces::LifecycleNodeInterface::CallbackReturn;

class MissionManagerNode : public rclcpp_lifecycle::LifecycleNode
{
public:
    MissionManagerNode() : rclcpp_lifecycle::LifecycleNode("mission_manager_node")
    {
        RCLCPP_INFO(get_logger(), "Mission Manager Node Instantiated. State: UNCONFIGURED");
    }

    // --- Lifecycle State Transitions ---
    CallbackReturn on_configure(const rclcpp_lifecycle::State &) override
    {
        RCLCPP_INFO(get_logger(), "Configuring... Setting up publishers and subscribers.");

        // Publishers 
        // State Publisher
        state_pub_ = this->create_publisher<std_msgs::msg::String>("/robot_state", 10);
        feedback_pub_ = this->create_publisher<std_msgs::msg::String>("/mission_feedback", 10);

        // Subscribers 
        // Mission Request Subscriber
        mission_sub_ = this->create_subscription<custom_interfaces::msg::MissionRequest>(
            "/mission_request", 10, std::bind(&MissionManagerNode::mission_callback, this, _1));
        // Emergency Stop Subscriber
        estop_sub_ = this->create_subscription<std_msgs::msg::Bool>(
            "/emergency_stop", 10, std::bind(&MissionManagerNode::estop_callback, this, _1));

        return CallbackReturn::SUCCESS;
    }

    CallbackReturn on_activate(const rclcpp_lifecycle::State &) override
    {
        RCLCPP_INFO(get_logger(), "Activating... System is now IDLE and ready for missions.");
        state_pub_->on_activate();
        feedback_pub_->on_activate();
        publish_state("IDLE");
        return CallbackReturn::SUCCESS;
    }

    CallbackReturn on_deactivate(const rclcpp_lifecycle::State &) override
    {
        RCLCPP_INFO(get_logger(), "Deactivating... Halting missions.");
        state_pub_->on_deactivate();
        feedback_pub_->on_deactivate();
        return CallbackReturn::SUCCESS;
    }

    CallbackReturn on_cleanup(const rclcpp_lifecycle::State &) override
    {
        RCLCPP_INFO(get_logger(), "Cleaning up... Releasing resources.");
        mission_sub_.reset();
        estop_sub_.reset();
        state_pub_.reset();
        feedback_pub_.reset();
        return CallbackReturn::SUCCESS;
    }

private:
    // --- Callbacks ---
    void mission_callback(const custom_interfaces::msg::MissionRequest::SharedPtr msg)
        {
            if (this->get_current_state().id() == lifecycle_msgs::msg::State::PRIMARY_STATE_ACTIVE)
            {
                RCLCPP_INFO(get_logger(), "Received Mission to: %s", msg->target_location.c_str());
                publish_state("MISSION_STARTED");

                std::thread([this, msg]() {
                    
                    BT::BehaviorTreeFactory factory;
                    factory.registerNodeType<LiftObjectNode>("LiftObject", this->shared_from_this());
                    factory.registerNodeType<NavigateToNode>("NavigateTo", this->shared_from_this());

                    std::string pkg_share_dir = ament_index_cpp::get_package_share_directory("mission_manager");
                    std::string xml_file = pkg_share_dir + "/behavior_trees/mission_tree.xml";
                
                    RCLCPP_INFO(get_logger(), "Loading XML from: %s", xml_file.c_str());
                    auto tree = factory.createTreeFromFile(xml_file);
                    
                    tree.rootBlackboard()->set("pick_location", msg->target_location);

                    RCLCPP_INFO(get_logger(), "Executing Mission Tree...");
                    
                    BT::NodeStatus status = tree.tickWhileRunning();

                    if (status == BT::NodeStatus::SUCCESS) {
                        RCLCPP_INFO(get_logger(), "Mission Completed Successfully.");
                        publish_state("IDLE");
                    } else {
                        RCLCPP_ERROR(get_logger(), "Mission Failed.");
                        publish_state("ERROR");
                    }
                    
                }).detach(); 
            }
        }

    void estop_callback(const std_msgs::msg::Bool::SharedPtr msg)
    {
        if (msg->data) {
            RCLCPP_ERROR(get_logger(), "EMERGENCY STOP RECEIVED! Aborting Mission.");
            publish_state("ERROR"); 
            // TODO: Halt Behavior Tree and trigger recovery
        }
    }

    void publish_state(const std::string& state)
    {
        auto msg = std_msgs::msg::String();
        msg.data = state;
        if (state_pub_ && state_pub_->is_activated()) {
            state_pub_->publish(msg);
        }
    }

    // ROS 2 network interfaces
    rclcpp::Subscription<custom_interfaces::msg::MissionRequest>::SharedPtr mission_sub_;
    rclcpp::Subscription<std_msgs::msg::Bool>::SharedPtr estop_sub_;
    rclcpp_lifecycle::LifecyclePublisher<std_msgs::msg::String>::SharedPtr state_pub_;
    rclcpp_lifecycle::LifecyclePublisher<std_msgs::msg::String>::SharedPtr feedback_pub_;
};

int main(int argc, char **argv)
{
    rclcpp::init(argc, argv);
    auto node = std::make_shared<MissionManagerNode>();
    rclcpp::spin(node->get_node_base_interface());
    rclcpp::shutdown();
    return 0;
}