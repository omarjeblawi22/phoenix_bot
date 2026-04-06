Phoenix: Detailed Process Documentation (Phase 1-7)
1. Project Initialization & Software Environment
The project utilizes a modular robotics architecture built on ROS 2 Foxy Fitzroy running on Ubuntu 20.04 (Focal Fossa)
.
Package Configuration: The development began by cloning a template (e.g., articubot_one or phoenix_bot) and renaming all internal references in package.xml and CMake files to maintain registry consistency
.
Coordinate Frames: Following ROS standards, the robot’s main coordinate frame is base_link, oriented with X pointing forward, Y to the left, and Z upward
.
Version Control: A GitHub repository was established to sync code between the development PC and the Raspberry Pi "brain"
.
2. 3D Structural Modeling (URDF/Xacro)
The robot's physical structure is defined using Xacro (XML Macros) to allow for modular file inclusion of sensors like LiDAR and cameras
.
Chassis Dimensions: The core is modeled as a box 300mm x 300mm x 150mm
.
Kinematic Configuration: The robot uses a differential drive setup with two drive wheels (50mm radius, 40mm length) and a single caster wheel at the front for stability
.
Physics Properties: To ensure valid simulation, Inertial Macros were applied to every link based on estimated masses (e.g., 0.5kg for the chassis and 0.1kg per wheel)
.
3. Gazebo Simulation & Control Plugins
The simulation environment in Gazebo serves as the primary validation platform, utilizing use_sim_time to synchronize node clocks
.
Differential Drive Plugin: The lib_gazebo_ros_diff_drive plugin is used to simulate motor behavior
.
Key Parameters: Configured with a wheel separation of 0.35m and wheel diameter of 0.1m
.
Odometry: The plugin publishes the transform from the odom frame to base_link, providing a dead-reckoning position estimate
.
Friction Tuning: To prevent "jittering," the caster wheel friction coefficients (mu1 and mu2) were set to near-zero values to simulate a frictionless slide
.
4. Compute Hardware: The Raspberry Pi "Brain"
A Raspberry Pi 4B (4GB RAM) was selected as the central processor for its ability to handle SLAM-based mapping and real-time decision-making
.
Architecture: Requires a 64-bit OS (ARM64) to support modern ROS 2 libraries
.
Remote Management: Configured via Netplan for a dedicated network, allowing headless operation through SSH
.
Thermal Management: To prevent CPU throttling during SLAM processing, the Pi is housed in a heatsink case with active cooling fans
.
5. Power Architecture Design
The power system is designed for high-current discharge and stable voltage regulation.
Primary Source: A 3S LiPo battery providing a nominal 11.1V (12.6V fully charged)
.
Dual Rail System:
12V Rail: Supplies raw battery power to the DC motors via the L298N driver
.
5V Rail: A buck converter (switching regulator) drops the voltage to a stable 4.85V–5.0V to power the Pi, LiDAR, and USB hub
.
Safety Logic: Calculated total draw of ~5A for electronics led to the installation of a 10A mini blade fuse and an XT60 battery interface
.
6. Power Implementation & Distribution
Theoretical designs were translated into a physical wiring harness using terminal strips for distribution
.
USB Power Management: An external powered USB hub is used to bypass the Raspberry Pi's internal current limits (preventing back-feeding issues)
.
Connector Standards: Used XT60 for the main battery and RCY/JST connectors for low-power logic pins
.
Verification: Voltages were validated with a multimeter at each junction before connecting the Pi's GPIO pins (Pin 4 for 5V, Pin 6 for Ground)
.
7. Motor Control & Feedback Loops
The actuation layer uses a layered "onion" approach to abstract hardware from high-level ROS commands
.
Motor Driver: An L298N H-Bridge handles current amplification, supporting up to 2A per motor
.
Low-Level Controller: An Arduino Nano acts as the bridge, communicating with the Pi via Serial over USB at 57600 baud
.
Encoder Integration: Hall-effect encoders measure motor revolutions; empirical testing determined a resolution of approximately 3450 counts per revolution for the output shaft
.
Control Loop: The Arduino runs a PID feedback loop at 30Hz, allowing the robot to accept target velocities (e.g., revolutions per second) rather than raw PWM values
.
If I wanna do this whole setup espically the Ros2 Gazebo etc steps, how should I start

To start the simulation-driven development for Project Phoenix, you should follow a layered approach that begins with setting up your software environment and moves into 3D modeling and virtual testing. This process ensures your algorithms are validated before you ever touch physical hardware
.
1. Establish the Software Environment
The foundation of this setup is Ubuntu 20.04 (Focal Fossa) paired with ROS 2 Foxy Fitzroy
.
Create a Workspace: Open a terminal in your home directory and create a development workspace (e.g., dev_ws/src)
.
Package Initialization: Rather than starting from scratch, clone a ROS 2 package template from GitHub (such as my_bot or articubot_one) into your src folder
.
Renaming: If you change the package name to "Phoenix," ensure you update all references in the package.xml and CMake files to maintain registry consistency
.
Dependencies: Install essential tools including colcon (for building), xacro (for modeling), and joint_state_publisher_gui (for testing transforms)
.
2. Robot Modeling (URDF and Xacro)
You must describe the robot’s physical structure to ROS using URDF files, utilizing Xacro to keep the code modular
.
Coordinate Frames: Define the base_link as your primary frame. Per ROS standards, X should point forward, Y to the left, and Z upward
.
Modular Design: Create a main file (e.g., robot.urdf.xacro) that "includes" separate files for the core structure, LiDAR, and camera
.
Core Components: Model a differential drive system consisting of two driven wheels and a single caster wheel for stability
.
Physics Properties: For simulation to work, every link needs visual, collision, and inertial tags
. Use inertial macros to simplify the math for calculating the mass and distribution of your chassis and wheels
.
3. Gazebo Simulation Integration
Once your model is built, you transition from visualization in Rviz to a physics-based environment in Gazebo
.
Simulation Time: Always set the parameter use_sim_time:=true when launching nodes to ensure they synchronize their clocks with the Gazebo simulator
.
Unified Launch File: Create a file (e.g., launch_sim.launch.py) that executes three actions in one command:
Runs the robot_state_publisher.
Launches Gazebo.
Spawns your robot entity into the virtual world
.
Control Plugin: To make the robot move, add the lib_gazebo_ros_diff_drive plugin to your URDF. You must specify your wheel separation (e.g., 0.35m) and wheel diameter (e.g., 0.1m) so the simulator can accurately calculate motion
.
4. Tuning and Initial Testing
Friction Management: In the URDF, set the caster wheel friction coefficients (mu1 and mu2) to a near-zero value (e.g., 0.001). This prevents the robot from "jittering" or dragging in simulation
.
Teleoperation: Test your setup by running the teleop_twist_keyboard node. This allows you to drive your virtual Phoenix using your keyboard to ensure the motor plugins are correctly receiving velocity commands
.
World Building: Create an "obstacles world" (a .world file) within Gazebo to test the robot's ability to navigate cluttered environments before moving to autonomous SLAM missions
.
Let's redo the read me more details, and I addded more videos, for Lidar and Cameras

This updated documentation for Project Phoenix incorporates technical specifications from the project proposal and the extended "Articulated Robotics" series, focusing on the integration of advanced sensors like LiDAR and cameras for autonomous search-and-rescue (SAR) missions.

--------------------------------------------------------------------------------
Phoenix: Autonomous Search-and-Rescue System
Simulation-Driven Development Documentation
Project Overview
Phoenix is a modular, autonomous ground vehicle designed for SAR operations in GPS-denied and structurally ambiguous environments
. The project utilizes a simulation-led pipeline to bridge theoretical research with real-world deployment
.
Core Technology Stack
OS: Ubuntu 20.04 (Focal Fossa)
.
Middleware: ROS 2 Foxy Fitzroy
.
Simulation: Gazebo (physics) and RViz (visualization)
.
Hardware: Raspberry Pi 4B (4GB), Arduino Nano, RPLIDAR C1/A1, ESP32-S3, and L298N H-Bridge
.

--------------------------------------------------------------------------------
Technical Process Documentation
1. Coordinate Frameworks & Structural Modeling
The robot follows strict ROS standards for spatial orientation.
Standard Frame: base_link is the primary coordinate frame with X pointing forward, Y to the left, and Z upward
. It is positioned at the center of rotation between the two drive wheels
.
Optical Frame: Vision sensors use a secondary _optical frame (e.g., camera_link_optical) where Z points forward into the scene, X to the right, and Y down
.
URDF/Xacro: The robot is modeled as a 300mm x 300mm x 150mm chassis with differential drive wheels (50mm radius) and a frictionless caster
.
2. Power & Actuation Layer
A dual-voltage architecture ensures stable compute and high-torque motion.
Power Rails: A 3S LiPo battery (11.1V-12.6V) powers the 12V motor rail, while a buck converter drops voltage to 5V for the Raspberry Pi and sensors
.
Safety: The circuit includes a 10A mini-blade fuse and an XT60 battery interface
.
Motor Control: An Arduino Nano handles low-level PID closed-loop control at 30Hz, converting target velocities into PWM signals for the L298N driver
. Encoders provide feedback at approximately 3450 counts per revolution
.
3. LiDAR Integration (2D SLAM)
LiDAR is the primary sensor for Simultaneous Localization and Mapping (SLAM).
Simulation: Integrated via lidar.xacro using the lib_gazebo_ros_ray_sensor plugin
. The virtual sensor scans 360° at 10Hz with a range of 12 meters
.
Data Format: Publishes sensor_msgs/LaserScan messages containing range measurements for each sweep
.
Hardware: Uses the RPLIDAR A1/C1 connected via USB
. The driver node is configured with angle_compensate set to true and publishes to the /scan topic
.
4. Camera Systems & Vision
The system uses both RGB and potentially depth cameras for target identification.
Simulation: A depth type sensor in Gazebo publishes sensor_msgs/Image and camera_info
. The field of view is tuned to match the physical Raspberry Pi Camera v2
.
Target Verification: Phoenix executes a verification step when within 0.5–1m of a candidate location
. It uses a lightweight human-figure detector (fine-tuned via transfer learning) to confirm targets
.
Data Handling: Image transport plugins are used to compress raw images into JPEG format to save network bandwidth during remote teleoperation
.
5. Advanced Localization (Wi-Fi FTM)
To augment SLAM in cluttered environments, Phoenix uses IEEE 802.11mc Fine Timing Measurement (FTM).
Homing Logic: An ESP32-S3 on the robot pings a beacon to measure Round Trip Time (RTT), providing a distance estimate based on the speed of light
.
Particle Filter: Distance data is processed by a probabilistic particle filter on the Raspberry Pi to create a probability distribution of the target's location, helping the robot transition from exploration to goal-directed navigation
.

--------------------------------------------------------------------------------
Testing & Validation Strategy
The system's performance is quantitatively assessed across three maze configurations
:
Baseline Maze: Standard layout to test basic SLAM and navigation metrics.
Reflective Maze: Includes metal surfaces to test LiDAR multipath distortion and Wi-Fi signal interference
.
Congested Maze: Tests the system's ability to localize targets in environments with high 2.4GHz Wi-Fi traffic
.
Success Metrics: Goal completion rate, path optimality (A* algorithm), and target detection accuracy (Precision/Recall)
