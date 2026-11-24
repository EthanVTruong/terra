# chill_logger: Arduino Uno firmware

Reads the DHT22 once a minute, shows temperature, humidity and a running chill-hour count on the
I2C LCD, beeps on trouble, and prints one checksummed record per reading on USB serial for the
validation layer ([docs/INTEROP.md](../../docs/INTEROP.md)).

## Wiring

| Part | Uno |
| --- | --- |
| DHT22 pin 1 (VCC) | 5V |
| DHT22 pin 2 (data) | D2, plus a 10 kOhm resistor from D2 to 5V (pull-up) |
| DHT22 pin 4 (GND) | GND (pin 3 unused) |
| Buzzer + | D8 |
| Buzzer - | GND |
| LCD backpack GND / VCC / SDA / SCL | GND / 5V / A4 / A5 |

The pull-up holds the data line high between the sensor's pulses; without it, reads fail.

## Upload

1. Arduino IDE -> Library Manager: install **DHT sensor library** (Adafruit), **Adafruit Unified
   Sensor** and **LiquidCrystal I2C** (Frank de Brabander).
2. Open `chill_logger.ino`. Set `NODE_ID` to the logger's `node_id` in `config/nodes.yaml`.
3. Board: Arduino Uno. Upload. Serial Monitor at 9600 baud shows lines like
   `$CM1,orchard-1,42,2520000,4.6,81.2,OK,12.0*7D`.

If the LCD is lit but blank, turn the backpack's contrast screw; if still blank, set
`LCD_ADDR` to `0x3F`.

## Behaviour

| Event | LCD | Buzzer |
| --- | --- | --- |
| Power on | `Chill monitor` / node id | one short chirp |
| Normal reading | `T  4.6C  RH 81%` / `Chill hours 12` | - |
| Sensor read fails | `SENSOR ERROR` | after 3 failures in a row: three short beeps each minute until it reads again |
| Device count reaches `CHILL_TARGET_H` | - | one long beep (once; remembered in EEPROM) |

The on-device count adds one hour when the mean of an uptime hour (60 readings, at least 45 good)
is between 0 and 7.2 C. It survives power loss (EEPROM, written at most once an hour) and is
advisory: the backend recomputes chill hours and Chill Portions from the time-stamped,
validated record. Send `R` over serial to reset the count for a new season, `?` to print the
settings.

## Settings (top of the sketch)

| Name | Default | Note |
| --- | --- | --- |
| `NODE_ID` | `orchard-1` | must match `config/nodes.yaml` |
| `SAMPLE_MS` | 60000 | keep `sample_interval_s` in the registry equal |
| `CHILL_TARGET_H` | 800 | long beep threshold; 0 disables |
| `FAIL_ALARM_READS` | 3 | failed reads before the error beeps |
| `EEPROM_MAGIC` | `0xC417` | change it to start from zero after an upload |

## Host test

`host_test/` holds tiny stand-ins for the Arduino core, DHT, LCD and EEPROM so the real sketch
compiles with g++ and can be driven from a list of temperatures:

```sh
g++ -std=c++17 -I host_test host_test/main.cpp -o /tmp/chill_host
printf '3.0\n%.0s' {1..120} | /tmp/chill_host
```

`tests/test_interop_tools.py::test_firmware_speaks_wire_v1` does this in CI and decodes every line
with the backend's decoder, so the firmware and the validation layer cannot drift apart.
