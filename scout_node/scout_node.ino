#include <Arduino.h>
#include <avr/sleep.h>
#include <avr/wdt.h>
#include <avr/power.h>
#include <SPI.h>
#include <RF24.h>
#include <DHT.h>

// ---------------- CONFIGURATION ----------------

// Hardware Pins
#define DHT22_PIN   2
#define DHTTYPE     DHT22
#define NRF_CE_PIN  9
#define NRF_CSN_PIN 10

// Manually set the RF Channel (0-125). Must match the receiver.
#define NRF_CHANNEL 76 

// Node & Communication
const uint8_t NODE_ID = 0x01;
const uint64_t GATEWAY_ADDRESS = 0xE8E8F0F0E1LL;
const uint8_t MAX_RETRIES = 5;
const uint8_t PACKET_TYPE_V1 = 0x01;

// Sleep settings: (5 minutes = 300s) / 8s WDT ≈ 37.5 → 38 cycles
#define WDT_CYCLES_FOR_SLEEP 1
volatile int wdt_cycles = 0; // updated in ISR

// ---------------- OBJECTS ----------------
RF24 radio(NRF_CE_PIN, NRF_CSN_PIN);
DHT dht(DHT22_PIN, DHTTYPE);

// Data packet structure
struct DataPacket {
  uint8_t nodeId;
  uint8_t packetType;
  int16_t temperature; // scaled ×100
  int16_t humidity;    // scaled ×100
};
DataPacket data;

// ---------------- ISR ----------------
// Watchdog Timer Interrupt — fires every 8s
ISR(WDT_vect) {
  wdt_cycles++;
}

// ---------------- FORWARD DECLARATIONS ----------------
void setupWatchdog();
void goToSleep();
void performOperationalCycle();
bool readSensorData();
bool transmitPacket();
// find_best_channel() is no longer used but can be kept for future use
int find_best_channel();

// ---------------- SETUP ----------------
void setup() {
  MCUSR &= ~(1 << WDRF);   // disable WDT reset flag
  wdt_disable();

  Serial.begin(115200);
  Serial.println(F("Scout-Node Booting..."));

  dht.begin();

  data.nodeId = NODE_ID;
  data.packetType = PACKET_TYPE_V1;

  // Initialize radio with the fixed channel
  SPI.begin();
  if (radio.begin()) {
    // Set the manually defined channel
    radio.setChannel(NRF_CHANNEL);
    Serial.print(F("Using fixed RF channel: "));
    Serial.println(NRF_CHANNEL);
    
    radio.powerDown();
  } else {
    Serial.println(F("Radio not responding!"));
  }
  SPI.end();

  setupWatchdog();
  Serial.println(F("Setup complete. Entering sleep..."));
  goToSleep();
}

// ---------------- LOOP ----------------
void loop() {
  if (wdt_cycles >= WDT_CYCLES_FOR_SLEEP) {
    wdt_cycles = 0; // reset counter
    performOperationalCycle();
  }
  goToSleep(); // always sleep after loop
}

// ---------------- CORE FUNCTIONS ----------------
void performOperationalCycle() {
  Serial.println(F("\n--- Wake Cycle Start ---"));
  SPI.begin();
  if (!radio.begin()) {
    Serial.println(F("Radio init failed!"));
    return;
  }
  radio.setPALevel(RF24_PA_LOW);
  radio.stopListening();
  radio.openWritingPipe(GATEWAY_ADDRESS);
  radio.setAutoAck(true);
  radio.setRetries(5, 15);

  if (readSensorData()) {
    transmitPacket();
  }
  radio.powerDown();
  SPI.end();
  Serial.println(F("--- Wake Cycle End ---"));
}

bool readSensorData() {
  Serial.print(F("Sampling sensor... "));
  float temp = dht.readTemperature();
  float hum  = dht.readHumidity();
  if (isnan(temp) || isnan(hum)) {
    Serial.println(F("DHT22 read failed!"));
    return false;
  }
  data.temperature = (int16_t)(temp * 100.0);
  data.humidity    = (int16_t)(hum * 100.0);

  Serial.print(F("Temp: ")); Serial.print(temp);
  Serial.print(F(" °C, Hum: ")); Serial.print(hum);
  Serial.println(F(" %"));
  return true;
}

bool transmitPacket() {
  Serial.print(F("Transmitting... "));
  for (int i = 0; i < MAX_RETRIES; i++) {
    if (radio.write(&data, sizeof(data))) {
      Serial.println(F("Success! ACK received."));
      return true;
    }
    Serial.print(F("Failed, retry "));
    Serial.println(i + 1);
  }
  Serial.println(F("All retries failed."));
  return false;
}

// This function is no longer called in setup but is kept for reference.
int find_best_channel() {
  const int num_channels = 126;
  const int step = 5;
  const int samples = 100;
  uint8_t carrier_counts[num_channels] = {0};

  for (int i = 0; i < num_channels; i += step) {
    radio.setChannel(i);
    radio.startListening();
    for (int j = 0; j < samples; j++) {
      delayMicroseconds(128);
      if (radio.testCarrier()) carrier_counts[i]++;
    }
    radio.stopListening();
  }

  int best_channel = 0, min_count = 255;
  for (int i = 0; i < num_channels; i += step) {
    if (carrier_counts[i] < min_count) {
      min_count = carrier_counts[i];
      best_channel = i;
    }
  }
  return best_channel;
}

// ---------------- POWER MANAGEMENT ----------------
void setupWatchdog() {
  wdt_reset();
  MCUSR &= ~(1 << WDRF);
  WDTCSR |= (1 << WDCE) | (1 << WDE);
  WDTCSR = (1 << WDP3) | (1 << WDP0); // 8s
  WDTCSR |= (1 << WDIE); // interrupt mode
}

void goToSleep() {
  Serial.flush();
  set_sleep_mode(SLEEP_MODE_PWR_DOWN);
  cli();
  sleep_bod_disable();
  sei();
  sleep_enable();
  sleep_cpu();
  sleep_disable();
}