#include "SwitchBotPlugin.h"

#include <Xinput.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <filesystem>
#include <string>
#include <vector>

#include "bakkesmod/wrappers/GameEvent/ServerWrapper.h"
#include "bakkesmod/wrappers/GameObject/BallWrapper.h"
#include "bakkesmod/wrappers/GameObject/CarWrapper.h"
#include "imgui/imgui.cpp"
#include "imgui/imgui.h"
#include "imgui/imgui_draw.cpp"
#include "imgui/imgui_widgets.cpp"

using namespace BakkesMod::Plugin;

// ============================================================
//  BOOST PAD LOCATIONS
// ============================================================

static const float BOOST_LOCS[34][3] = {
    {0.0f, -4240.0f, 70.0f},     {-1792.0f, -4184.0f, 70.0f},
    {1792.0f, -4184.0f, 70.0f},  {-3072.0f, -4096.0f, 73.0f},
    {3072.0f, -4096.0f, 73.0f},  {-940.0f, -3308.0f, 70.0f},
    {940.0f, -3308.0f, 70.0f},   {0.0f, -2816.0f, 70.0f},
    {-3584.0f, -2484.0f, 70.0f}, {3584.0f, -2484.0f, 70.0f},
    {-1788.0f, -2300.0f, 70.0f}, {1788.0f, -2300.0f, 70.0f},
    {-2048.0f, -1036.0f, 70.0f}, {0.0f, -1024.0f, 70.0f},
    {2048.0f, -1036.0f, 70.0f},  {-3584.0f, 0.0f, 73.0f},
    {-1024.0f, 0.0f, 70.0f},     {1024.0f, 0.0f, 70.0f},
    {3584.0f, 0.0f, 73.0f},      {-2048.0f, 1036.0f, 70.0f},
    {0.0f, 1024.0f, 70.0f},      {2048.0f, 1036.0f, 70.0f},
    {-1788.0f, 2300.0f, 70.0f},  {1788.0f, 2300.0f, 70.0f},
    {-3584.0f, 2484.0f, 70.0f},  {3584.0f, 2484.0f, 70.0f},
    {0.0f, 2816.0f, 70.0f},      {-940.0f, 3310.0f, 70.0f},
    {940.0f, 3308.0f, 70.0f},    {-3072.0f, 4096.0f, 73.0f},
    {3072.0f, 4096.0f, 73.0f},   {-1792.0f, 4184.0f, 70.0f},
    {1792.0f, 4184.0f, 70.0f},   {0.0f, 4240.0f, 70.0f}};

// ============================================================
//  OBSERVATION SLOT INDICES
// ============================================================

static constexpr int IS_SELF = 0;
static constexpr int IS_MATE = 1;
static constexpr int IS_OPP = 2;
static constexpr int IS_BALL = 3;
static constexpr int IS_BOOST = 4;
static constexpr int POS_X = 5;
static constexpr int POS_Y = 6;
static constexpr int POS_Z = 7;
static constexpr int VEL_X = 8;
static constexpr int VEL_Y = 9;
static constexpr int VEL_Z = 10;
static constexpr int FW_X = 11;
static constexpr int FW_Y = 12;
static constexpr int FW_Z = 13;
static constexpr int UP_X = 14;
static constexpr int UP_Y = 15;
static constexpr int UP_Z = 16;
static constexpr int ANG_X = 17;
static constexpr int ANG_Y = 18;
static constexpr int ANG_Z = 19;
static constexpr int BOOST_AMT = 20;
static constexpr int DEMO = 21;
static constexpr int ON_GROUND = 22;
static constexpr int HAS_FLIP = 23;

static constexpr int ENTITY_BALL = 6;
static constexpr int ENTITY_BOOSTS_START = 7;
static constexpr int N_ENTITIES = 41;

// ============================================================
//  BUTTON HELPER
// ============================================================

static WORD GetXboxButtonMask(int index) {
  switch (index) {
    case 0:
      return XINPUT_GAMEPAD_A;
    case 1:
      return XINPUT_GAMEPAD_B;
    case 2:
      return XINPUT_GAMEPAD_X;
    case 3:
      return XINPUT_GAMEPAD_Y;
    case 4:
      return XINPUT_GAMEPAD_LEFT_SHOULDER;
    case 5:
      return XINPUT_GAMEPAD_RIGHT_SHOULDER;
    case 6:
      return XINPUT_GAMEPAD_BACK;
    case 7:
      return XINPUT_GAMEPAD_START;
    case 8:
      return XINPUT_GAMEPAD_LEFT_THUMB;
    case 9:
      return XINPUT_GAMEPAD_RIGHT_THUMB;
    case 10:
      return XINPUT_GAMEPAD_DPAD_UP;
    case 11:
      return XINPUT_GAMEPAD_DPAD_DOWN;
    case 12:
      return XINPUT_GAMEPAD_DPAD_LEFT;
    case 13:
      return XINPUT_GAMEPAD_DPAD_RIGHT;
    default:
      return XINPUT_GAMEPAD_RIGHT_SHOULDER;
  }
}

BAKKESMOD_PLUGIN(SwitchBot, "SwitchBot AI", "4.0", 0)

// ============================================================
//  LOOKUP TABLE
// ============================================================

std::vector<NextoAction> SwitchBot::GenerateNextoLookupTable() {
  std::vector<NextoAction> actions;

  for (int throttle : {-1, 0, 1}) {
    for (int steer : {-1, 0, 1}) {
      for (int boost : {0, 1}) {
        for (int handbrake : {0, 1}) {
          if (boost == 1 && throttle != 1) continue;
          float t = (throttle != 0) ? (float)throttle : (float)boost;
          actions.push_back({t, (float)steer, 0.0f, (float)steer, 0.0f, 0.0f,
                             (float)boost, (float)handbrake});
        }
      }
    }
  }

  for (int pitch : {-1, 0, 1}) {
    for (int yaw : {-1, 0, 1}) {
      for (int roll : {-1, 0, 1}) {
        for (int jump : {0, 1}) {
          for (int boost : {0, 1}) {
            if (jump == 1 && yaw != 0) continue;
            if (pitch == 0 && roll == 0 && jump == 0) continue;
            float hb = (jump == 1 && (pitch != 0 || yaw != 0 || roll != 0))
                           ? 1.0f
                           : 0.0f;
            actions.push_back({(float)boost, (float)yaw, (float)pitch,
                               (float)yaw, (float)roll, (float)jump,
                               (float)boost, hb});
          }
        }
      }
    }
  }

  cvarManager->log("[SwitchBot] Lookup table size: " +
                   std::to_string(actions.size()));
  return actions;
}

// ============================================================
//  BOOST PAD INDEX LOOKUP
// ============================================================

int SwitchBot::FindBoostPadIndex(Vector loc) {
  float bestDist = 150.0f * 150.0f;
  int bestIdx = -1;
  for (int i = 0; i < 34; i++) {
    float dx = loc.X - BOOST_LOCS[i][0];
    float dy = loc.Y - BOOST_LOCS[i][1];
    float dz = loc.Z - BOOST_LOCS[i][2];
    float d2 = dx * dx + dy * dy + dz * dz;
    if (d2 < bestDist) {
      bestDist = d2;
      bestIdx = i;
    }
  }
  return bestIdx;
}

// ============================================================
//  RESET STATE
// ============================================================

void SwitchBot::ResetState() {
  tickCounter = 0;
  lastPhysTime = -1.0f;
  smoothedSteer = 0.0f;
  controlBlend = 0.0f;
  wasActiveLogged = false;
  previous_action.fill(0.0f);
  boostPadActive.fill(1.0f);
}

// ============================================================
//  CACHE ONNX I/O NAMES  (called once after model loads)
// ============================================================

void SwitchBot::CacheIONames() {
  if (namesLoaded || !session_nexto) return;
  Ort::AllocatorWithDefaultOptions alloc;
  for (size_t i = 0; i < session_nexto->GetInputCount(); i++) {
    auto ptr = session_nexto->GetInputNameAllocated(i, alloc);
    inNamesStr.push_back(ptr.get());
  }
  for (const auto& s : inNamesStr) inNames.push_back(s.c_str());
  for (size_t i = 0; i < session_nexto->GetOutputCount(); i++) {
    auto ptr = session_nexto->GetOutputNameAllocated(i, alloc);
    outNamesStr.push_back(ptr.get());
  }
  for (const auto& s : outNamesStr) outNames.push_back(s.c_str());
  namesLoaded = true;
}

// ============================================================
//  IMGUI
// ============================================================

void SwitchBot::SetImGuiContext(uintptr_t ctx) {
  ImGui::SetCurrentContext(reinterpret_cast<ImGuiContext*>(ctx));
}

std::string SwitchBot::GetPluginName() { return "SwitchBot Settings"; }

void SwitchBot::RenderSettings() {
  ImGui::TextUnformatted("SwitchBot v4.0 - Nexto Perceiver Injection");
  ImGui::Separator();

  // Model status indicator
  if (modelLoaded)
    ImGui::TextColored(ImVec4(0.0f, 1.0f, 0.0f, 1.0f), "Model: LOADED");
  else
    ImGui::TextColored(
        ImVec4(1.0f, 0.0f, 0.0f, 1.0f),
        "Model: NOT FOUND  (place nexto_fixed.onnx in BakkesMod/data/)");

  ImGui::Spacing();

  ImGui::Checkbox("Master Enable", &isPluginEnabled);
  if (!isPluginEnabled)
    ImGui::TextColored(ImVec4(1.0f, 0.4f, 0.0f, 1.0f), "Plugin is DISABLED.");

  // AI active status
  ImGui::Spacing();
  if (isPluginEnabled && modelLoaded) {
    if (wasActiveLogged)
      ImGui::TextColored(ImVec4(0.0f, 1.0f, 0.4f, 1.0f),
                         "Status: AI ACTIVE  (blend: %.0f%%)",
                         controlBlend * 100.0f);
    else
      ImGui::TextColored(ImVec4(0.8f, 0.8f, 0.8f, 1.0f),
                         "Status: Human Control");
  }

  ImGui::Spacing();
  ImGui::Separator();

  const char* buttons[] = {"A",     "B",  "X",  "Y",  "LB",   "RB",   "Back",
                           "Start", "L3", "R3", "Up", "Down", "Left", "Right"};
  ImGui::Combo("Activation Button", &activationButton, buttons,
               IM_ARRAYSIZE(buttons));
  ImGui::TextDisabled("Keyboard 'V' is always active as a fallback.");

  ImGui::Spacing();
  ImGui::Separator();
  ImGui::TextUnformatted("Tuning");
  ImGui::Separator();

  ImGui::SliderFloat("Steer Smoothing", &steerSmoothing, 0.0f, 0.98f, "%.2f");
  ImGui::TextDisabled("Smooths discrete steer oscillation. Recommended: 0.90");

  ImGui::Spacing();
  ImGui::SliderFloat("Blend Time (s)", &blendTime, 0.01f, 0.3f, "%.3f");
  ImGui::TextDisabled("How fast AI takes/releases control. Recommended: 0.05");

  ImGui::Spacing();
  ImGui::SliderFloat("Binary Input Threshold", &binaryThreshold, 0.0f, 1.0f,
                     "%.2f");
  ImGui::TextDisabled(
      "Blend level before jump/boost/handbrake fire. Recommended: 0.10");

  ImGui::Spacing();
  ImGui::Separator();

  // Reset to defaults button
  if (ImGui::Button("Reset to Defaults")) {
    steerSmoothing = 0.90f;
    blendTime = 0.05f;
    binaryThreshold = 0.10f;
  }

  ImGui::Spacing();
  ImGui::Separator();
  ImGui::Text("Lookup table size: %zu", lookup_table.size());
}

// ============================================================
//  ON LOAD
// ============================================================

void SwitchBot::onLoad() {
  std::filesystem::path dataFolder = gameWrapper->GetDataFolder();

  Ort::SessionOptions sessionOptions;
  sessionOptions.SetIntraOpNumThreads(1);
  sessionOptions.SetGraphOptimizationLevel(
      GraphOptimizationLevel::ORT_ENABLE_ALL);

  lookup_table = GenerateNextoLookupTable();

  q_data.assign(32, 0.0f);
  kv_data.assign(N_ENTITIES * 24, 0.0f);
  m_data.assign(N_ENTITIES, 1.0f);

  ResetState();

  std::filesystem::path p = dataFolder / "nexto_fixed.onnx";
  if (std::filesystem::exists(p)) {
    try {
      session_nexto = std::make_unique<Ort::Session>(env, p.wstring().c_str(),
                                                     sessionOptions);
      CacheIONames();
      modelLoaded = true;
      cvarManager->log("[SwitchBot] Model loaded: nexto_fixed.onnx");
    } catch (const std::exception& e) {
      modelLoaded = false;
      cvarManager->log(std::string("[SwitchBot] ONNX load failed: ") +
                       e.what());
    }
  } else {
    modelLoaded = false;
    cvarManager->log("[SwitchBot] nexto_fixed.onnx not found in: " +
                     p.string());
  }

  gameWrapper->HookEventWithCaller<ActorWrapper>(
      "Function TAGame.VehiclePickup_Boost_TA.Pickup",
      [this](ActorWrapper caller, void* params, std::string) {
        if (caller.IsNull()) return;
        int idx = FindBoostPadIndex(caller.GetLocation());
        if (idx >= 0) boostPadActive[idx] = 0.0f;
      });

  gameWrapper->HookEventWithCaller<ActorWrapper>(
      "Function TAGame.VehiclePickup_TA.SetReady",
      [this](ActorWrapper caller, void* params, std::string) {
        if (caller.IsNull()) return;
        int idx = FindBoostPadIndex(caller.GetLocation());
        if (idx >= 0) boostPadActive[idx] = 1.0f;
      });

  gameWrapper->HookEvent("Function GameEvent_Soccar_TA.Active.StartRound",
                         [this](std::string) { boostPadActive.fill(1.0f); });

  gameWrapper->HookEvent(
      "Function TAGame.GameEvent_Soccar_TA.InitField", [this](std::string) {
        ResetState();
        cvarManager->log("[SwitchBot] State reset: new field.");
      });

  gameWrapper->HookEvent(
      "Function GameEvent_Soccar_TA.Countdown.BeginState", [this](std::string) {
        ResetState();
        cvarManager->log("[SwitchBot] State reset: countdown.");
      });

  gameWrapper->HookEvent(
      "Function TAGame.GameEvent_TA.Destroyed", [this](std::string) {
        ResetState();
        cvarManager->log("[SwitchBot] State reset: game destroyed.");
      });

  gameWrapper->HookEventWithCaller<CarWrapper>(
      "Function TAGame.Car_TA.SetVehicleInput",
      [this](CarWrapper caller, void* params, std::string) {
        this->OnSetVehicleInput(caller, params);
      });
}

// ============================================================
//  FILL OBSERVATION
// ============================================================

void SwitchBot::FillNextoObs(ServerWrapper server, CarWrapper myCar) {
  std::fill(q_data.begin(), q_data.end(), 0.0f);
  std::fill(kv_data.begin(), kv_data.end(), 0.0f);
  std::fill(m_data.begin(), m_data.end(), 1.0f);

  int myTeam = myCar.GetTeamNum2();
  auto cars = server.GetCars();

  auto FillCar = [&](CarWrapper c, int e_idx) {
    m_data[e_idx] = 0.0f;
    int b = e_idx * 24;

    Vector loc = c.GetLocation();
    Vector vel = c.GetVelocity();
    Vector ang = c.GetAngularVelocity();
    Rotator rot = c.GetRotation();

    float P = rot.Pitch * 3.14159265f / 32768.f;
    float Yw = rot.Yaw * 3.14159265f / 32768.f;
    float R = rot.Roll * 3.14159265f / 32768.f;
    float CP = std::cos(P), SP = std::sin(P);
    float CY = std::cos(Yw), SY = std::sin(Yw);
    float CR = std::cos(R), SR = std::sin(R);

    float fw_x = CP * CY, fw_y = CP * SY, fw_z = SP;
    float up_x = -CR * CY * SP - SR * SY, up_y = -CR * SY * SP + SR * CY,
          up_z = CP * CR;

    kv_data[b + POS_X] = loc.X / 2300.f;
    kv_data[b + POS_Y] = loc.Y / 2300.f;
    kv_data[b + POS_Z] = loc.Z / 2300.f;
    kv_data[b + VEL_X] = vel.X / 2300.f;
    kv_data[b + VEL_Y] = vel.Y / 2300.f;
    kv_data[b + VEL_Z] = vel.Z / 2300.f;
    kv_data[b + FW_X] = fw_x;
    kv_data[b + FW_Y] = fw_y;
    kv_data[b + FW_Z] = fw_z;
    kv_data[b + UP_X] = up_x;
    kv_data[b + UP_Y] = up_y;
    kv_data[b + UP_Z] = up_z;
    kv_data[b + ANG_X] = ang.X / 5.5f;
    kv_data[b + ANG_Y] = ang.Y / 5.5f;
    kv_data[b + ANG_Z] = ang.Z / 5.5f;

    auto bc = c.GetBoostComponent();
    float rawBoost = bc.IsNull() ? 0.0f : bc.GetCurrentBoostAmount();
    kv_data[b + BOOST_AMT] = (rawBoost > 1.0f) ? (rawBoost / 100.0f) : rawBoost;

    kv_data[b + DEMO] = 0.0f;
    kv_data[b + ON_GROUND] = c.IsOnGround() ? 1.0f : 0.0f;
    kv_data[b + HAS_FLIP] = c.HasFlip() ? 1.0f : 0.0f;
  };

  // Self
  for (int i = 0; i < cars.Count(); i++) {
    auto c = cars.Get(i);
    if (c.IsNull() || c.memory_address != myCar.memory_address) continue;
    FillCar(c, 0);
    kv_data[IS_SELF] = 1.0f;
    break;
  }

  // Teammates (slots 1-3) and opponents (slots 4-5)
  int teammate_idx = 1;
  int opponent_idx = 4;
  for (int i = 0; i < cars.Count(); i++) {
    auto c = cars.Get(i);
    if (c.IsNull() || c.memory_address == myCar.memory_address) continue;
    int e_idx;
    if (c.GetTeamNum2() == myTeam) {
      if (teammate_idx > 3) continue;
      e_idx = teammate_idx++;
    } else {
      if (opponent_idx > 5) continue;
      e_idx = opponent_idx++;
    }
    FillCar(c, e_idx);
    int b = e_idx * 24;
    kv_data[b + IS_MATE] = (c.GetTeamNum2() == myTeam) ? 1.0f : 0.0f;
    kv_data[b + IS_OPP] = (c.GetTeamNum2() != myTeam) ? 1.0f : 0.0f;
  }

  // Ball
  auto ball = server.GetBall();
  if (!ball.IsNull()) {
    m_data[ENTITY_BALL] = 0.0f;
    int b = ENTITY_BALL * 24;
    Vector bLoc = ball.GetLocation();
    Vector bVel = ball.GetVelocity();
    Vector bAng = ball.GetAngularVelocity();
    kv_data[b + IS_BALL] = 1.0f;
    kv_data[b + POS_X] = bLoc.X / 2300.f;
    kv_data[b + POS_Y] = bLoc.Y / 2300.f;
    kv_data[b + POS_Z] = bLoc.Z / 2300.f;
    kv_data[b + VEL_X] = bVel.X / 2300.f;
    kv_data[b + VEL_Y] = bVel.Y / 2300.f;
    kv_data[b + VEL_Z] = bVel.Z / 2300.f;
    kv_data[b + ANG_X] = bAng.X / 5.5f;
    kv_data[b + ANG_Y] = bAng.Y / 5.5f;
    kv_data[b + ANG_Z] = bAng.Z / 5.5f;
  }

  // Boost pads
  for (int i = 0; i < 34; i++) {
    int e_idx = ENTITY_BOOSTS_START + i;
    int b = e_idx * 24;
    bool full = (BOOST_LOCS[i][2] > 72.0f);
    m_data[e_idx] = 0.0f;
    kv_data[b + IS_BOOST] = 1.0f;
    kv_data[b + POS_X] = BOOST_LOCS[i][0] / 2300.f;
    kv_data[b + POS_Y] = BOOST_LOCS[i][1] / 2300.f;
    kv_data[b + POS_Z] = BOOST_LOCS[i][2] / 2300.f;
    kv_data[b + BOOST_AMT] = full ? 1.0f : 0.12f;
    kv_data[b + DEMO] = boostPadActive[i];
  }

  // Flip axes for orange team
  if (myTeam == 1) {
    for (int e = 0; e < N_ENTITIES; e++) {
      int b = e * 24;
      for (int slot : {POS_X, VEL_X, FW_X, UP_X, ANG_X}) {
        kv_data[b + slot] = -kv_data[b + slot];
        kv_data[b + slot + 1] = -kv_data[b + slot + 1];
      }
      std::swap(kv_data[b + IS_MATE], kv_data[b + IS_OPP]);
    }
  }

  // Build query from self entity + previous action
  for (int i = 0; i < 24; i++) q_data[i] = kv_data[i];
  for (int i = 0; i < 8; i++) q_data[24 + i] = previous_action[i];

  // Ego-centric transform: translate and rotate into self frame
  float self_px = q_data[POS_X];
  float self_py = q_data[POS_Y];
  float self_pz = q_data[POS_Z];
  float theta = std::atan2(q_data[FW_X], q_data[FW_Y]);
  float ct = std::cos(theta);
  float st = std::sin(theta);

  for (int e = 0; e < N_ENTITIES; e++) {
    int b = e * 24;
    kv_data[b + POS_X] -= self_px;
    kv_data[b + POS_Y] -= self_py;
    kv_data[b + POS_Z] -= self_pz;
    for (int slot : {POS_X, VEL_X, FW_X, UP_X, ANG_X}) {
      float x = kv_data[b + slot];
      float y = kv_data[b + slot + 1];
      kv_data[b + slot] = ct * x - st * y;
      kv_data[b + slot + 1] = st * x + ct * y;
    }
  }
}

// ============================================================
//  ON SET VEHICLE INPUT
// ============================================================

void SwitchBot::OnSetVehicleInput(CarWrapper car, void* params) {
  if (!isPluginEnabled || !modelLoaded || !gameWrapper->IsInGame() ||
      car.IsNull() || params == nullptr || !session_nexto)
    return;

  auto localCar = gameWrapper->GetLocalCar();
  if (localCar.IsNull() || car.memory_address != localCar.memory_address)
    return;

  auto server = gameWrapper->GetGameEventAsServer();
  if (server.IsNull()) return;

  float currentTime = server.GetSecondsElapsed();
  if ((currentTime - lastPhysTime) < (0.5f / 120.0f)) return;
  lastPhysTime = currentTime;

  bool isPressed = (GetAsyncKeyState('V') & 0x8000) != 0;
  XINPUT_STATE xstate;
  for (DWORD i = 0; i < XUSER_MAX_COUNT; i++) {
    ZeroMemory(&xstate, sizeof(XINPUT_STATE));
    if (XInputGetState(i, &xstate) == ERROR_SUCCESS) {
      if (xstate.Gamepad.wButtons & GetXboxButtonMask(activationButton)) {
        isPressed = true;
        break;
      }
    }
  }

  if (isPressed && !wasActiveLogged) {
    cvarManager->log("[SwitchBot] AI Engaged.");
    wasActiveLogged = true;
    tickCounter = 0;
  } else if (!isPressed && wasActiveLogged) {
    cvarManager->log("[SwitchBot] Human Control.");
    wasActiveLogged = false;
    previous_action.fill(0.0f);
    smoothedSteer = 0.0f;
  }

  float blendSpeed = 1.0f / (120.0f * blendTime);
  controlBlend = std::clamp(
      controlBlend + (isPressed ? blendSpeed : -blendSpeed), 0.0f, 1.0f);
  if (controlBlend == 0.0f) return;

  if (tickCounter == 0) {
    FillNextoObs(server, car);

    try {
      Ort::MemoryInfo mem =
          Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);

      std::vector<int64_t> q_shape = {1, 1, 32};
      std::vector<int64_t> kv_shape = {1, N_ENTITIES, 24};
      std::vector<int64_t> m_shape = {1, N_ENTITIES};

      std::vector<Ort::Value> inputs;
      inputs.push_back(Ort::Value::CreateTensor<float>(
          mem, q_data.data(), q_data.size(), q_shape.data(), q_shape.size()));
      inputs.push_back(
          Ort::Value::CreateTensor<float>(mem, kv_data.data(), kv_data.size(),
                                          kv_shape.data(), kv_shape.size()));
      inputs.push_back(Ort::Value::CreateTensor<float>(
          mem, m_data.data(), m_data.size(), m_shape.data(), m_shape.size()));

      auto outT = session_nexto->Run(Ort::RunOptions{nullptr}, inNames.data(),
                                     inputs.data(), inputs.size(),
                                     outNames.data(), outNames.size());

      float* logits = outT[0].GetTensorMutableData<float>();
      int actionIdx = (int)std::distance(
          logits, std::max_element(logits, logits + (int)lookup_table.size()));

      currentAction = lookup_table[actionIdx];

      previous_action[0] = currentAction.throttle;
      previous_action[1] = currentAction.steer;
      previous_action[2] = currentAction.pitch;
      previous_action[3] = currentAction.yaw;
      previous_action[4] = currentAction.roll;
      previous_action[5] = currentAction.jump;
      previous_action[6] = currentAction.boost;
      previous_action[7] = currentAction.handbrake;

    } catch (const std::exception& e) {
      cvarManager->log(std::string("[SwitchBot] Inference error: ") + e.what());
    } catch (...) {
      cvarManager->log("[SwitchBot] Inference error: unknown exception.");
    }
  }

  tickCounter++;
  if (tickCounter >= 8) tickCounter = 0;

  ControllerInput* input = (ControllerInput*)params;

  input->Throttle = input->Throttle * (1.0f - controlBlend) +
                    currentAction.throttle * controlBlend;

  smoothedSteer = smoothedSteer * steerSmoothing +
                  currentAction.steer * (1.0f - steerSmoothing);
  input->Steer =
      input->Steer * (1.0f - controlBlend) + smoothedSteer * controlBlend;

  input->Pitch =
      input->Pitch * (1.0f - controlBlend) + currentAction.pitch * controlBlend;
  input->Yaw =
      input->Yaw * (1.0f - controlBlend) + currentAction.yaw * controlBlend;
  input->Roll =
      input->Roll * (1.0f - controlBlend) + currentAction.roll * controlBlend;

  if (controlBlend > binaryThreshold) {
    input->Jump = (currentAction.jump > 0.0f);
    input->ActivateBoost = (currentAction.boost > 0.0f);
    input->HoldingBoost = (currentAction.boost > 0.0f);
    input->Handbrake = (currentAction.handbrake > 0.0f);
  }
}

// ============================================================
void SwitchBot::onUnload() { cvarManager->log("[SwitchBot] Unloaded."); }
