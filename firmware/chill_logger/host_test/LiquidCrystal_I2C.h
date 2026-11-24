#pragma once
#include "Arduino.h"
// Keeps the two LCD rows so the host test can check what the farmer would see.
extern char sim_lcd[2][17];
class LiquidCrystal_I2C {
 public:
  LiquidCrystal_I2C(int, int, int) {}
  void init() {}
  void backlight() {}
  void setCursor(int c, int r) { col_ = c; row_ = r; }
  void print(const char *s) {
    for (; *s && col_ < 16; ++s) sim_lcd[row_][col_++] = *s;
  }
 private:
  int col_ = 0, row_ = 0;
};
