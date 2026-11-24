// Minimal host-side stand-ins for the Arduino core, so the sketch can be compiled and exercised with
// g++ (tests/test_firmware_host.py). Only what chill_logger.ino uses is provided.
#pragma once
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

#ifndef NAN
#define NAN (0.0f / 0.0f)
#endif
using std::isnan;

#define F(s) (s)
#define OUTPUT 1

extern unsigned long sim_ms;
extern unsigned long sim_tones;
inline unsigned long millis() { return sim_ms; }
inline void delay(unsigned long ms) { sim_ms += ms; }
inline void pinMode(int, int) {}
inline void tone(int, unsigned int, unsigned long) { sim_tones++; }

inline char *dtostrf(double v, signed char width, unsigned char prec, char *out) {
  std::sprintf(out, "%*.*f", (int)width, (int)prec, v);
  return out;
}

struct SerialStub {
  std::string input;
  void begin(unsigned long) {}
  int available() { return (int)input.size(); }
  int read() {
    if (input.empty()) return -1;
    int c = (unsigned char)input[0];
    input.erase(0, 1);
    return c;
  }
  void print(const char *s) { std::fputs(s, stdout); }
  void print(char c) { std::fputc(c, stdout); }
  void print(unsigned long v) { std::printf("%lu", v); }
  void print(float v, int d) { std::printf("%.*f", d, v); }
  void println(const char *s) { std::printf("%s\n", s); }
  void println(float v, int d) { std::printf("%.*f\n", d, v); }
};
extern SerialStub Serial;
