// Orchard chill logger - Arduino Uno firmware (ENG 3 chill monitor)
//
// Hardware (matches the project circuit):
//   DHT22 data  -> D2, with a 10 kOhm pull-up from D2 to 5V
//   Buzzer +    -> D8 (other leg to GND)
//   I2C LCD 16x2 backpack: SDA -> A4, SCL -> A5, VCC -> 5V, GND -> GND (address 0x27; try 0x3F if blank)
//
// Every SAMPLE_MS it reads the sensor, updates the LCD, and prints one wire-v1 record on USB serial:
//   $CM1,<node_id>,<seq>,<uptime_ms>,<temp_c>,<rh_pct>,<status>,<chill_h>*<CS>
// CS = XOR of every character between '$' and '*', as two upper-case hex digits. A laptop or Pi running
// `chillwatch logger bridge` stamps each line with UTC time; `chillwatch logger validate` checks it.
//
// The chill-hour count on the LCD is the device's own running estimate (0-7.2 C hourly means, counted
// on uptime hours, saved in EEPROM). The backend recomputes chill hours and Chill Portions from the
// validated, time-stamped record, so the logger never has to know the date.
//
// Libraries (Arduino Library Manager): "DHT sensor library" (Adafruit) + "Adafruit Unified Sensor",
// "LiquidCrystal I2C" (Frank de Brabander).
//
// Serial commands (9600 baud, newline): R = reset the chill count, ? = print settings.

#include <Arduino.h>
#include <DHT.h>
#include <EEPROM.h>
#include <LiquidCrystal_I2C.h>
#include <Wire.h>

// ---- settings: keep NODE_ID equal to node_id in config/nodes.yaml -------------------------------
#define NODE_ID "orchard-1"
#define SAMPLE_MS 60000UL         // one reading per minute (sample_interval_s: 60)
#define DHT_PIN 2
#define DHT_TYPE DHT22
#define BUZZER_PIN 8
#define LCD_ADDR 0x27
#define CHILL_LO_C 0.0            // chill-hour band, same as the backend's chill_hours()
#define CHILL_HI_C 7.2
#define CHILL_TARGET_H 800.0      // long beep once when the device count reaches this (0 disables)
#define FAIL_ALARM_READS 3        // error beeps after this many failed reads in a row
#define MIN_HOUR_SAMPLES 45       // an uptime hour counts only with >= 75% good samples
#define EEPROM_MAGIC 0xC417       // change to start a new season with a fresh count

static const unsigned long SAMPLES_PER_HOUR = 3600000UL / SAMPLE_MS;

DHT dht(DHT_PIN, DHT_TYPE);
LiquidCrystal_I2C lcd(LCD_ADDR, 16, 2);

struct Saved {
  uint16_t magic;
  float chill_h;
  uint8_t target_beeped;
};

static Saved saved;
static unsigned long seq = 0;
static unsigned long last_sample = 0;
static unsigned long hour_samples = 0;   // samples taken in the current uptime hour
static unsigned long hour_good = 0;      // of those, successful reads
static float hour_sum = 0.0;
static uint8_t fail_streak = 0;

// ---- helpers ----------------------------------------------------------------------------------
static void beep(unsigned int ms) {
  tone(BUZZER_PIN, 2700, ms);   // fine for a passive piezo; an active buzzer just sounds
  delay(ms + 60);
}

static void save() { EEPROM.put(0, saved); }

static void load() {
  EEPROM.get(0, saved);
  if (saved.magic != EEPROM_MAGIC || isnan(saved.chill_h) || saved.chill_h < 0) {
    saved.magic = EEPROM_MAGIC;
    saved.chill_h = 0.0;
    saved.target_beeped = 0;
    save();
  }
}

static uint8_t xor_checksum(const char *s) {
  uint8_t cs = 0;
  while (*s) cs ^= (uint8_t)*s++;
  return cs;
}

// one-decimal number, or an empty field for a failed read
static void fmt1(char *out, float v) {
  if (isnan(v)) {
    out[0] = '\0';
    return;
  }
  dtostrf(v, 0, 1, out);
}

static void send_record(float t, float h, const char *status) {
  char ts[12], hs[12], cs_txt[12], body[96];
  fmt1(ts, t);
  fmt1(hs, h);
  fmt1(cs_txt, saved.chill_h);
  snprintf(body, sizeof(body), "CM1,%s,%lu,%lu,%s,%s,%s,%s", NODE_ID, seq, millis(), ts, hs, status, cs_txt);
  char tail[4];
  snprintf(tail, sizeof(tail), "%02X", xor_checksum(body));
  Serial.print('$');
  Serial.print(body);
  Serial.print('*');
  Serial.println(tail);
}

// Row 0: "T  4.6C  RH 81% " (or "SENSOR ERROR"); row 1: "Chill hours 427 ". Each row is padded to
// 16 characters so old digits are overwritten.
static void lcd_row(uint8_t row, const char *text) {
  char line[17];
  snprintf(line, sizeof(line), "%-16s", text);
  lcd.setCursor(0, row);
  lcd.print(line);
}

static void show(float t, float h, bool ok) {
  char text[40], n1[12], n2[12];
  if (ok) {
    dtostrf(t, 5, 1, n1);
    dtostrf(h, 3, 0, n2);
    snprintf(text, sizeof(text), "T%sC  RH%s%%", n1, n2);
  } else {
    snprintf(text, sizeof(text), "SENSOR ERROR");
  }
  lcd_row(0, text);
  dtostrf(saved.chill_h, 0, 0, n1);
  snprintf(text, sizeof(text), "Chill hours %s", n1);
  lcd_row(1, text);
}

static void close_hour() {
  if (hour_good >= MIN_HOUR_SAMPLES) {
    float mean = hour_sum / hour_good;
    if (mean >= CHILL_LO_C && mean <= CHILL_HI_C) {
      saved.chill_h += 1.0;
      save();
    }
  }
  if (CHILL_TARGET_H > 0 && !saved.target_beeped && saved.chill_h >= CHILL_TARGET_H) {
    saved.target_beeped = 1;
    save();
    beep(1500);
  }
  hour_samples = hour_good = 0;
  hour_sum = 0.0;
}

static void handle_serial() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == 'R') {
      saved.chill_h = 0.0;
      saved.target_beeped = 0;
      save();
      Serial.println(F("# chill count reset"));
    } else if (c == '?') {
      Serial.print(F("# node "));
      Serial.print(F(NODE_ID));
      Serial.print(F(" sample_ms "));
      Serial.print(SAMPLE_MS);
      Serial.print(F(" chill_h "));
      Serial.println(saved.chill_h, 1);
    }
  }
}

static void sample() {
  float h = dht.readHumidity();
  float t = dht.readTemperature();   // Celsius
  bool ok = !isnan(t) && !isnan(h);
  seq++;
  hour_samples++;
  if (ok) {
    fail_streak = 0;
    hour_good++;
    hour_sum += t;
    send_record(t, h, "OK");
  } else {
    if (fail_streak < 255) fail_streak++;
    send_record(NAN, NAN, "ERR_READ");
    if (fail_streak >= FAIL_ALARM_READS) {   // three short beeps each minute until it reads again
      beep(120);
      beep(120);
      beep(120);
    }
  }
  show(t, h, ok);
  if (hour_samples >= SAMPLES_PER_HOUR) close_hour();
}

// ---- Arduino entry points ---------------------------------------------------------------------
void setup() {
  pinMode(BUZZER_PIN, OUTPUT);
  Serial.begin(9600);
  dht.begin();
  lcd.init();
  lcd.backlight();
  load();
  lcd_row(0, "Chill monitor");
  lcd_row(1, NODE_ID);
  Serial.println(F("# chill_logger wire CM1 " NODE_ID));   // '#' lines are ignored by the backend
  beep(80);
  delay(2000);                 // DHT22 needs ~2 s after power-up
  last_sample = millis();
  sample();
}

void loop() {
  handle_serial();
  if (millis() - last_sample >= SAMPLE_MS) {   // unsigned subtraction survives the 49-day wrap
    last_sample += SAMPLE_MS;
    sample();
  }
}
