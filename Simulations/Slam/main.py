import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation

# --- 1. Environment Settings ---
MAP_SIZE = 50
GRID_RES = 0.5
grid_dims = int(MAP_SIZE / GRID_RES)
LIDAR_RANGE = 12.0

# Obstacles: (x, y, radius)
obstacles = [(10, 10, 4), (35, 15, 5), (15, 35, 6), (40, 40, 3), (5, 25, 2)]

# The "Mental Map" (0.5 = Unknown, 0 = Empty, 1 = Wall)
internal_map = np.full((grid_dims, grid_dims), 0.5)


class Explorer:
    def __init__(self):
        self.x, self.y = 25.0, 25.0
        self.yaw = 0.0
        self.v = 1.0

    def scan_and_move(self):
        # 1. LiDAR Scanning
        readings = []
        num_rays = 36
        angles = np.linspace(0, 2 * np.pi, num_rays, endpoint=False)

        for a in angles:
            ray_angle = self.yaw + a
            dist = LIDAR_RANGE
            dx, dy = np.cos(ray_angle), np.sin(ray_angle)

            # Check for hits
            for ox, oy, r in obstacles:
                fx, fy = self.x - ox, self.y - oy
                b = 2 * (dx * fx + dy * fy)
                c = fx ** 2 + fy ** 2 - r ** 2
                disc = b ** 2 - 4 * c
                if disc >= 0:
                    t = (-b - np.sqrt(disc)) / 2
                    if 0 < t < dist:
                        dist = t
            readings.append((ray_angle, dist))

            # Update Map (Ray-casting)
            for r_step in np.arange(0, dist + 0.5, GRID_RES):
                gx = int((self.x + r_step * np.cos(ray_angle)) / GRID_RES)
                gy = int((self.y + r_step * np.sin(ray_angle)) / GRID_RES)
                if 0 <= gx < grid_dims and 0 <= gy < grid_dims:
                    if r_step < dist - 0.2:
                        internal_map[gy, gx] = max(0, internal_map[gy, gx] - 0.1)  # Clear
                    elif dist < LIDAR_RANGE:
                        internal_map[gy, gx] = min(1, internal_map[gy, gx] + 0.3)  # Wall

        # 2. Movement & Avoidance
        turn = 0
        for angle, dist in readings:
            if dist < 4.0:
                diff = (angle - self.yaw + np.pi) % (2 * np.pi) - np.pi
                turn -= (1.0 / dist) * np.sign(diff) * 0.5

        self.yaw += turn + np.random.uniform(-0.2, 0.2)

        # Keep inside bounds
        if self.x < 5 or self.x > MAP_SIZE - 5 or self.y < 5 or self.y > MAP_SIZE - 5:
            self.yaw += np.pi / 2

        self.x += self.v * np.cos(self.yaw)
        self.y += self.v * np.sin(self.yaw)


# --- 2. Visualization Setup ---
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 6))
fig.canvas.manager.set_window_title("Live Robot Mapping (SLAM)")

# Left side: Ground Truth
ax1.set_title("Physical World")
ax1.set_xlim(0, MAP_SIZE)
ax1.set_ylim(0, MAP_SIZE)
for ox, oy, r in obstacles:
    ax1.add_patch(plt.Circle((ox, oy), r, color='lightgray'))
car_viz, = ax1.plot([], [], 'ro', markersize=8)

# Right side: The Discovered Map
ax2.set_title("Robot's Internal Map")
# vmin and vmax are crucial here so the colors don't wash out!
map_viz = ax2.imshow(internal_map, origin='lower', extent=[0, MAP_SIZE, 0, MAP_SIZE],
                     cmap='bone_r', vmin=0, vmax=1)

explorer = Explorer()


# --- 3. The Animation Loop ---
def update(frame):
    explorer.scan_and_move()

    # Update visual data
    car_viz.set_data([explorer.x], [explorer.y])
    map_viz.set_data(internal_map)
    return car_viz, map_viz


# blit=False prevents the "empty frame" bug on many operating systems
ani = animation.FuncAnimation(fig, update, frames=400, interval=40, blit=False)

# This command opens the window and runs the animation live
plt.show()