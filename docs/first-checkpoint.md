# First checkpoint: outputs-disabled ROS diagnostics

Author: Colin Rooney · © 2026 Rooney Industries LLC · MIT licence

Start in the public [robo-bump-truck-ros repository](https://github.com/12mv2/robo-bump-truck-ros). Its documentation maintains the ROS interface setup, commands and troubleshooting; follow those instructions directly.

| What you want to do | Public guide |
|---|---|
| Set up the ROS workspace, build and test the interface, run the publisher/listener examples | [ROS README](https://github.com/12mv2/robo-bump-truck-ros#build-and-test) |
| Send synthetic commands and inspect outputs-disabled diagnostics | [Shadow bridge guide](https://github.com/12mv2/robo-bump-truck-ros/blob/main/truck_bridge/README.md#build-and-run) |
| Prepare a Jetson or Ubuntu host and replay recorded frames through your perception node | [Jetson and perception setup](https://github.com/12mv2/robo-bump-truck-ros/blob/main/docs/jetson-orin-setup.md) |
| Give a coding agent the perception setup procedure | [Agent brief](https://github.com/12mv2/robo-bump-truck-ros/blob/main/AGENT-BRIEF.md) |

The first checkpoint is the interface build/tests plus the synthetic shadow demo. It needs a supported ROS Linux environment, but no truck, phone or trained model. Use the public guide's isolated demo topics. Record the commit you use, test results and observed diagnostics; unit tests alone do not demonstrate topic delivery. The shadow executable has no physical output path. Retain repeated status delivery for normal, stop and steering-handback cases and the publisher/bridge/listener process exits separately from build/test results. Completing this checkpoint is separate from a fresh-follow first drive. See [build checkpoints](build-checkpoints.md) for availability and stop/pass observations at each later stage.

For the later perception replay, the node owner supplies their node package, model and configuration as described in the public setup guide. The ROS repository does not distribute that runtime or its model weights. The [OVAL4 head](../model/README.md) belongs to this build's historical trail stack; it is not a prerequisite for the synthetic ROS checkpoint or a substitute for your node's model.

Return to [the physical build](../README.md#eventual-physical-build--historical-reference) for parts, prints, wiring and calibration. The [historical controller and first-drive boundary](first-drive.md) still applies before powered operation. Robot Eyes remains **CLOSED SOURCE**; its [distribution link](ros.md#robot-eyes-the-phone-app) is separate from the public ROS interface.
