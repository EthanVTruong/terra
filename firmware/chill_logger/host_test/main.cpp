// Host driver: compiles the real sketch, feeds it a temperature per sample from stdin
// (one number per line, "nan" = failed read) and prints its serial output plus a final state line.
#include <iostream>
#include <vector>
#include "Arduino.h"
#include "DHT.h"
#include "EEPROM.h"
#include "LiquidCrystal_I2C.h"

unsigned long sim_ms = 0;
unsigned long sim_tones = 0;
SerialStub Serial;
EEPROMStub EEPROM;
char sim_lcd[2][17] = {"                ", "                "};
static std::vector<float> temps;
static size_t next_i = 0;
float sim_next_temp() { return next_i < temps.size() ? temps[next_i++] : NAN; }

#include "../chill_logger.ino"

int main() {
  std::string tok;
  while (std::cin >> tok) temps.push_back(tok == "nan" ? NAN : std::stof(tok));
  setup();
  while (next_i < temps.size()) {
    sim_ms += 1000;
    loop();
  }
  std::printf("# STATE chill_h=%.1f tones=%lu eeprom_writes=%lu lcd0=[%s] lcd1=[%s]\n", saved.chill_h, sim_tones,
              EEPROM.writes, sim_lcd[0], sim_lcd[1]);
  return 0;
}
