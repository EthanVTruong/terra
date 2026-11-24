#pragma once
#include "Arduino.h"
#define DHT22 22
// Replays temperatures (C) from sim_temps; "nan" entries simulate a failed read.
extern float sim_next_temp();
class DHT {
 public:
  DHT(int, int) {}
  void begin() {}
  float readHumidity() { pending_ = sim_next_temp(); return isnan(pending_) ? NAN : 80.0f; }
  float readTemperature() { return pending_; }
 private:
  float pending_ = NAN;
};
