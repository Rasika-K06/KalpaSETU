#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <OneWire.h>
#include <DallasTemperature.h>

//=============================================================================
// HARDWARE PIN DEFINITIONS
// These pins are based on the definitive "Master Connection Sheet: Super-Node"
// from the project's design specification.[1]
//=============================================================================

// I2C Bus for MPU-6050 Inertial Measurement Unit
#define MPU6050_SDA_PIN 40
#define MPU6050_SCL_PIN 39

// 1-Wire Bus for DS18B20 Digital Temperature Sensor
#define ONEWIRE_BUS_PIN 17

//=============================================================================
// GLOBAL OBJECTS & VARIABLES
//=============================================================================

// Create an MPU-6050 sensor object
Adafruit_MPU6050 mpu;

// Setup a oneWire instance to communicate with any 1-Wire devices
OneWire oneWire(ONEWIRE_BUS_PIN);

// Pass our oneWire reference to Dallas Temperature sensor 
DallasTemperature tempSensor(&oneWire);

// Timer for non-blocking loop
unsigned long previousMillis = 0;
const long interval = 1000; // Interval at which to print data (milliseconds)


//=============================================================================
// SETUP FUNCTION - Runs once on boot
//=============================================================================
void setup() {
  // 1. START SERIAL COMMUNICATION
  Serial.begin(115200);
  // A small delay is added to allow the Serial Monitor to connect,
  // especially important when using the native USB CDC interface.
  delay(2000); 
  Serial.println("\n\nBooting SETU Super-Node...");
  Serial.println("------------------------------------");

  // 2. INITIALIZE SENSORS SEQUENTIALLY WITH VERBOSE STATUS
  // This approach makes it easy to see exactly which sensor is failing.

  // Initialize I2C bus first
  Wire.begin(MPU6050_SDA_PIN, MPU6050_SCL_PIN);

  // Initialize MPU-6050
  Serial.print("Initializing MPU-6050 (I2C)... ");
  if (!mpu.begin()) {
    Serial.println("FAILED!");
    Serial.println("System Halted. Could not find a valid MPU-6050 sensor.");
    Serial.println("Check I2C wiring (SDA: 40, SCL: 39) and power connections.");
    while (1) { delay(100); } // Halt execution indefinitely
  }
  Serial.println("SUCCESS.");
  
  // Configure MPU-6050 settings
  mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
  mpu.setGyroRange(MPU6050_RANGE_500_DEG);
  mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);

  // Initialize DS18B20
  Serial.print("Initializing DS18B20 (1-Wire)... ");
  tempSensor.begin();
  
  // Check if any 1-Wire devices were found
  if (tempSensor.getDeviceCount() == 0) {
    Serial.println("FAILED!");
    Serial.println("System Halted. No DS18B20 sensors found on the 1-Wire bus.");
    Serial.println("Verify connection to GPIO 17 and ensure the mandatory 4.7k pull-up resistor is connected between the data line and 3.3V.");
    while (1) { delay(100); } // Halt execution indefinitely
  }
  Serial.println("SUCCESS.");
  Serial.print("Found ");
  Serial.print(tempSensor.getDeviceCount(), DEC);
  Serial.println(" device(s).");

  Serial.println("------------------------------------");
  Serial.println("All systems nominal. Starting main loop.");
}


//=============================================================================
// MAIN LOOP - Runs repeatedly
//=============================================================================
void loop() {
  // Get current time
  unsigned long currentMillis = millis();

  // This non-blocking structure ensures the loop runs continuously
  // while only printing data at the specified 'interval'.
  if (currentMillis - previousMillis >= interval) {
    // Save the last time data was printed
    previousMillis = currentMillis;

    // --- Read MPU-6050 Data ---
    sensors_event_t a, g, temp;
    mpu.getEvent(&a, &g, &temp);

    // --- Read DS18B20 Data ---
    tempSensor.requestTemperatures(); // Send the command to get temperatures
    float tempC = tempSensor.getTempCByIndex(0); // Read temperature from the first sensor

    // --- Print Data to Serial Monitor ---
    Serial.println("\n--- New Sensor Reading ---");

    // Print Temperature Data
    Serial.print("Temperature: ");
    if (tempC == DEVICE_DISCONNECTED_C) {
      Serial.println("Error: Could not read temperature data.");
    } else {
      Serial.print(tempC);
      Serial.println(" °C");
    }

    // Print Accelerometer Data
    Serial.print("Acceleration (m/s^2) -> ");
    Serial.print("X: ");
    Serial.print(a.acceleration.x);
    Serial.print(", Y: ");
    Serial.print(a.acceleration.y);
    Serial.print(", Z: ");
    Serial.println(a.acceleration.z);

    // Print Gyroscope Data
    Serial.print("Rotation (rad/s) -> ");
    Serial.print("X: ");
    Serial.print(g.gyro.x);
    Serial.print(", Y: ");
    Serial.print(g.gyro.y);
    Serial.print(", Z: ");
    Serial.println(g.gyro.z);
  }
}