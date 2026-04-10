/*
 * ═══════════════════════════════════════════════════════════════════
 *   ESP32 WiFi Robot Car Controller  —  Production-Ready Firmware
 * ═══════════════════════════════════════════════════════════════════
 *
 *  Hardware : ESP32 (30-pin or 38-pin) + L298N Dual H-Bridge
 *  Protocol : WebSocket (port 81) for low-latency control
 *  Web UI   : Hosted on ESP32 Access Point at 192.168.4.1
 *
 * ─────────────────────────────────────────────────────────────────
 *   PIN MAPPING  (ESP32  →  L298N)
 * ─────────────────────────────────────────────────────────────────
 *   ENA  GPIO 14   PWM speed control  –  Motor A (Left  motor)
 *   IN1  GPIO 26   Direction bit 1    –  Motor A
 *   IN2  GPIO 27   Direction bit 2    –  Motor A
 *   IN3  GPIO 33   Direction bit 1    –  Motor B (Right motor)
 *   IN4  GPIO 32   Direction bit 2    –  Motor B
 *   ENB  GPIO 12   PWM speed control  –  Motor B (Right motor)
 *   GND  GND       Common ground      –  MUST share with battery GND!
 * ─────────────────────────────────────────────────────────────────
 *
 *  Required Library (install via Arduino Library Manager):
 *    →  "WebSockets" by Markus Sattler  (search: arduinoWebSockets)
 *
 * ═══════════════════════════════════════════════════════════════════
 */

#include <WiFi.h>
#include <WebServer.h>
#include <WebSocketsServer.h>

// ── WiFi Access Point Credentials ────────────────────────────────────────────
const char* SSID     = "ESP32-Car";
const char* PASSWORD = "12345678";

// ── Motor Pin Definitions ─────────────────────────────────────────────────────
#define ENA  14   // PWM – Motor A (Left)  speed
#define IN1  26   // Motor A direction pin 1
#define IN2  27   // Motor A direction pin 2
#define IN3  33   // Motor B direction pin 1
#define IN4  32   // Motor B direction pin 2
#define ENB  12   // PWM – Motor B (Right) speed

// ── PWM Configuration (ESP32 LEDC – core v3.x API) ───────────────────────────
// NOTE: core v3.x removed ledcSetup/ledcAttachPin/channel-based ledcWrite.
//       Use ledcAttach(pin, freq, resolution) + ledcWrite(pin, duty) instead.
#define PWM_FREQ        5000   // 5 kHz – inaudible, gentle on motor windings
#define PWM_RESOLUTION     8   // 8-bit resolution → duty values 0–255
#define MOTOR_SPEED      200   // ~78 % duty cycle – safe starting speed (0–255)
#define TURN_SPEED       180   // Slightly lower speed for cleaner pivot turns

// ── Fail-safe Configuration ───────────────────────────────────────────────────
#define FAILSAFE_MS     2000   // Stop motors if no command received for 2 s
unsigned long lastCmdTime = 0;

// ── Server Instances ──────────────────────────────────────────────────────────
WebServer        httpServer(80);   // HTTP on port 80  (serves the web UI)
WebSocketsServer wsServer(81);     // WebSocket on port 81 (receives commands)

// ─────────────────────────────────────────────────────────────────────────────
//  EMBEDDED WEB PAGE  (stored in ESP32 flash via PROGMEM)
//  The page uses a WebSocket to send single-character commands to the ESP32:
//    'F' = Forward  'B' = Backward  'L' = Left  'R' = Right
//    'S' = Stop     'H' = Heartbeat (keeps fail-safe timer alive)
// ─────────────────────────────────────────────────────────────────────────────
const char INDEX_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>ESP32 Car</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: #0f0f1a;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    font-family: -apple-system, 'Segoe UI', sans-serif;
    color: #e0e0e0;
    gap: 28px;
  }

  /* ── Header ── */
  header {
    text-align: center;
    line-height: 1.3;
  }
  header h1 { font-size: 1.5rem; letter-spacing: 3px; color: #ff4d6d; }
  header p  { font-size: 0.75rem; letter-spacing: 1px; color: #555; text-transform: uppercase; margin-top: 4px; }

  /* ── D-Pad grid ── */
  .dpad {
    display: grid;
    grid-template-columns: repeat(3, 88px);
    grid-template-rows:    repeat(3, 88px);
    gap: 10px;
  }

  .dpad button {
    background: #1a1a2e;
    border: 1.5px solid #2a2a4a;
    border-radius: 14px;
    color: #ccc;
    font-size: 1.9rem;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    user-select: none;
    touch-action: none;
    transition: background 0.08s, border-color 0.08s, transform 0.08s;
    outline: none;
  }

  .dpad button:hover          { border-color: #ff4d6d44; }
  .dpad button.pressed        { background: #ff4d6d; border-color: #ff4d6d; transform: scale(0.93); color: #fff; }
  .dpad .empty                { background: transparent; border: none; pointer-events: none; }
  #btn-stop                   { font-size: 0.85rem; letter-spacing: 1.5px; font-weight: 600; }
  #btn-stop.pressed           { background: #e63950; border-color: #e63950; }

  /* ── Speed slider ── */
  .speed-wrap {
    width: 284px;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .speed-wrap label { font-size: 0.72rem; letter-spacing: 1px; color: #666; text-transform: uppercase; white-space: nowrap; }
  .speed-wrap input[type=range] { flex: 1; accent-color: #ff4d6d; }
  .speed-wrap span  { font-size: 0.8rem; color: #888; width: 36px; text-align: right; }

  /* ── Status pill ── */
  .status {
    font-size: 0.72rem;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    padding: 5px 14px;
    border-radius: 20px;
    border: 1px solid #2a2a4a;
    color: #555;
    transition: color 0.3s, border-color 0.3s;
  }
  .status.ok  { color: #4caf50; border-color: #4caf5066; }
  .status.err { color: #ff4d6d; border-color: #ff4d6d66; animation: blink 1.2s ease-in-out infinite; }
  @keyframes blink { 0%,100%{opacity:1} 50%{opacity:.5} }
</style>
</head>
<body>

<header>
  <h1>ESP32 CAR</h1>
  <p>Wireless Controller</p>
</header>

<div class="dpad" id="dpad">
  <div class="empty"></div>
  <button id="btn-fwd"  data-cmd="F" aria-label="Forward">&#9650;</button>
  <div class="empty"></div>

  <button id="btn-lft"  data-cmd="L" aria-label="Left">&#9664;</button>
  <button id="btn-stop" data-cmd="S" aria-label="Stop">STOP</button>
  <button id="btn-rgt"  data-cmd="R" aria-label="Right">&#9654;</button>

  <div class="empty"></div>
  <button id="btn-bwd"  data-cmd="B" aria-label="Backward">&#9660;</button>
  <div class="empty"></div>
</div>

<div class="speed-wrap">
  <label>Speed</label>
  <input type="range" id="speed" min="80" max="255" value="200" step="5">
  <span id="speed-val">78%</span>
</div>

<div class="status err" id="status">Connecting…</div>

<script>
  // ── State ──
  let ws, heartbeatTimer;
  let currentSpeed = 200;
  let activeCmd    = null;

  // ── Speed slider ──
  const speedSlider = document.getElementById('speed');
  const speedLabel  = document.getElementById('speed-val');
  speedSlider.addEventListener('input', () => {
    currentSpeed = parseInt(speedSlider.value);
    speedLabel.textContent = Math.round(currentSpeed / 255 * 100) + '%';
    if (activeCmd) send(activeCmd);   // update speed immediately while moving
  });

  // ── WebSocket ──
  function connect() {
    const statusEl = document.getElementById('status');
    statusEl.textContent = 'Connecting…';
    statusEl.className = 'status err';

    ws = new WebSocket('ws://' + location.hostname + ':81/');

    ws.onopen = () => {
      statusEl.textContent = 'Connected';
      statusEl.className = 'status ok';
      startHeartbeat();
    };

    ws.onclose = () => {
      statusEl.textContent = 'Disconnected';
      statusEl.className = 'status err';
      stopHeartbeat();
      setTimeout(connect, 2000);
    };

    ws.onerror = () => ws.close();
  }

  function send(cmd) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      // Prefix movement commands with current speed byte, e.g. "F200"
      if (cmd === 'S' || cmd === 'H') {
        ws.send(cmd);
      } else {
        ws.send(cmd + currentSpeed);
      }
    }
  }

  // Heartbeat every 800 ms – resets ESP32 fail-safe timer
  function startHeartbeat() { heartbeatTimer = setInterval(() => { if (!activeCmd) send('H'); }, 800); }
  function stopHeartbeat()  { clearInterval(heartbeatTimer); }

  // ── Button Events ──
  document.querySelectorAll('.dpad button[data-cmd]').forEach(btn => {
    const cmd = btn.dataset.cmd;

    const press = () => {
      activeCmd = (cmd !== 'S') ? cmd : null;
      btn.classList.add('pressed');
      send(cmd);
    };

    const release = () => {
      if (cmd !== 'S') {
        activeCmd = null;
        btn.classList.remove('pressed');
        send('S');
      }
    };

    // Mouse
    btn.addEventListener('mousedown',  press);
    btn.addEventListener('mouseup',    release);
    btn.addEventListener('mouseleave', release);

    // Touch (prevent ghost mouse events)
    btn.addEventListener('touchstart', e => { e.preventDefault(); press(); },   { passive: false });
    btn.addEventListener('touchend',   e => { e.preventDefault(); release(); }, { passive: false });
    btn.addEventListener('touchcancel',e => { e.preventDefault(); release(); }, { passive: false });
  });

  // ── Keyboard shortcuts (optional desktop use) ──
  const keyMap = { ArrowUp:'F', ArrowDown:'B', ArrowLeft:'L', ArrowRight:'R', ' ':'S' };
  const keyHeld = {};
  document.addEventListener('keydown', e => {
    const cmd = keyMap[e.key];
    if (!cmd || keyHeld[e.key]) return;
    keyHeld[e.key] = true;
    const btn = document.querySelector(`[data-cmd="${cmd}"]`);
    if (btn) { btn.dispatchEvent(new Event('mousedown')); }
  });
  document.addEventListener('keyup', e => {
    const cmd = keyMap[e.key];
    if (!cmd) return;
    keyHeld[e.key] = false;
    const btn = document.querySelector(`[data-cmd="${cmd}"]`);
    if (btn && cmd !== 'S') { btn.dispatchEvent(new Event('mouseup')); }
  });

  connect();
</script>
</body>
</html>
)rawliteral";


// ─────────────────────────────────────────────────────────────────────────────
//  MOTOR CONTROL FUNCTIONS
// ─────────────────────────────────────────────────────────────────────────────

// Sets PWM duty cycle for both motors
// core v3.x: ledcWrite(pin, duty)  — no channel argument
void setSpeed(uint8_t speed) {
  ledcWrite(ENA, speed);
  ledcWrite(ENB, speed);
}

void motorsStop() {
  ledcWrite(ENA, 0);
  ledcWrite(ENB, 0);
  digitalWrite(IN1, LOW); digitalWrite(IN2, LOW);
  digitalWrite(IN3, LOW); digitalWrite(IN4, LOW);
}

void motorsForward(uint8_t speed) {
  setSpeed(speed);
  digitalWrite(IN1, HIGH); digitalWrite(IN2, LOW);   // Motor A → forward
  digitalWrite(IN3, HIGH); digitalWrite(IN4, LOW);   // Motor B → forward
}

void motorsBackward(uint8_t speed) {
  setSpeed(speed);
  digitalWrite(IN1, LOW); digitalWrite(IN2, HIGH);   // Motor A → backward
  digitalWrite(IN3, LOW); digitalWrite(IN4, HIGH);   // Motor B → backward
}

void motorsLeft(uint8_t speed) {
  // Pivot left: right motor forward, left motor off
  ledcWrite(ENA, 0);                                 // Left  motor – coast
  ledcWrite(ENB, speed);                             // Right motor – drive
  digitalWrite(IN1, LOW);  digitalWrite(IN2, LOW);
  digitalWrite(IN3, HIGH); digitalWrite(IN4, LOW);
}

void motorsRight(uint8_t speed) {
  // Pivot right: left motor forward, right motor off
  ledcWrite(ENA, speed);                             // Left  motor – drive
  ledcWrite(ENB, 0);                                 // Right motor – coast
  digitalWrite(IN1, HIGH); digitalWrite(IN2, LOW);
  digitalWrite(IN3, LOW);  digitalWrite(IN4, LOW);
}


// ─────────────────────────────────────────────────────────────────────────────
//  WEBSOCKET EVENT HANDLER
//  Message format:
//    'S' | 'H'        → Stop / Heartbeat (no speed argument)
//    'F200' | 'B180'  → Movement command + speed value (0–255)
// ─────────────────────────────────────────────────────────────────────────────
void onWebSocketEvent(uint8_t clientNum, WStype_t type,
                      uint8_t* payload, size_t length) {

  switch (type) {

    case WStype_CONNECTED:
      Serial.printf("[WS] Client #%u connected\n", clientNum);
      motorsStop();
      break;

    case WStype_DISCONNECTED:
      Serial.printf("[WS] Client #%u disconnected\n", clientNum);
      motorsStop();
      break;

    case WStype_TEXT: {
      if (length == 0) break;

      lastCmdTime = millis();             // ← resets the fail-safe timer

      char cmd = (char)payload[0];

      // Parse optional speed argument
      uint8_t speed = MOTOR_SPEED;
      if (length > 1) {
        char numBuf[5] = {0};
        size_t numLen = min(length - 1, (size_t)4);
        memcpy(numBuf, payload + 1, numLen);
        int parsed = atoi(numBuf);
        if (parsed > 0 && parsed <= 255) speed = (uint8_t)parsed;
      }

      switch (cmd) {
        case 'F': motorsForward(speed);  break;
        case 'B': motorsBackward(speed); break;
        case 'L': motorsLeft(speed);     break;
        case 'R': motorsRight(speed);    break;
        case 'S': motorsStop();          break;
        case 'H': /* heartbeat – only resets timer */ break;
        default:  motorsStop();
      }
      break;
    }

    default:
      break;
  }
}


// ─────────────────────────────────────────────────────────────────────────────
//  SETUP
// ─────────────────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  Serial.println("\n===  ESP32 WiFi Car  ===");

  // ── Motor direction pins ──
  pinMode(IN1, OUTPUT); pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT); pinMode(IN4, OUTPUT);

  // ── PWM (LEDC) for enable pins – core v3.x API ──
  // ledcAttach(pin, frequency, resolution) — replaces ledcSetup + ledcAttachPin
  ledcAttach(ENA, PWM_FREQ, PWM_RESOLUTION);
  ledcAttach(ENB, PWM_FREQ, PWM_RESOLUTION);

  motorsStop();   // Safe state at boot (must come AFTER ledcAttach)

  // ── WiFi Access Point ──
  WiFi.mode(WIFI_AP);
  WiFi.softAP(SSID, PASSWORD);
  Serial.printf("AP started  SSID: %s  IP: %s\n", SSID, WiFi.softAPIP().toString().c_str());

  // ── HTTP server – serve the control page ──
  httpServer.on("/", HTTP_GET, []() {
    httpServer.send_P(200, "text/html", INDEX_HTML);
  });
  httpServer.onNotFound([]() {
    httpServer.sendHeader("Location", "/", true);
    httpServer.send(302, "text/plain", "");
  });
  httpServer.begin();
  Serial.println("HTTP server started on port 80");

  // ── WebSocket server ──
  wsServer.begin();
  wsServer.onEvent(onWebSocketEvent);
  Serial.println("WebSocket server started on port 81");

  // Initialise fail-safe timer so we don't immediately cut power on boot
  lastCmdTime = millis();
}


// ─────────────────────────────────────────────────────────────────────────────
//  MAIN LOOP
// ─────────────────────────────────────────────────────────────────────────────
void loop() {
  httpServer.handleClient();
  wsServer.loop();

  // ── Fail-safe: cut motors if silent for FAILSAFE_MS ──────────────────────
  if (millis() - lastCmdTime > FAILSAFE_MS) {
    motorsStop();
    // Don't reset lastCmdTime here – we stay stopped until a real command arrives
  }
}
