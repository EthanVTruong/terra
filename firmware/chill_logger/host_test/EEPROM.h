#pragma once
#include "Arduino.h"
struct EEPROMStub {
  unsigned char mem[1024];
  unsigned long writes = 0;
  EEPROMStub() { std::memset(mem, 0xFF, sizeof(mem)); }
  template <typename T> T &get(int a, T &v) { std::memcpy(&v, mem + a, sizeof(T)); return v; }
  template <typename T> const T &put(int a, const T &v) { std::memcpy(mem + a, &v, sizeof(T)); writes++; return v; }
};
extern EEPROMStub EEPROM;
