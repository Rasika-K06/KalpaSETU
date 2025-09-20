import threading
import logging
import queue
import time

# --- Safe import for YAML ---
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    print("⚠️ PyYAML not installed. Continuing without rules.yaml support.")

import RPi.GPIO as GPIO

# Import the custom, thread-safe modules developed for the project
from spi_lock import SPILock
from worker_threads import LoRaWorkerThread, nRFWorkerThread
from processing_thread import DataProcessingThread
from communications_thread import CommunicationsThread

# --- BCM PIN DEFINITIONS (Hardware-Software Contract) ---
LORA_DIO0_PIN = 25  # Interrupt pin for LoRa packet reception
NRF_IRQ_PIN = 22    # Interrupt pin for nRF packet reception
LORA_NSS_PIN = 8    # SPI Chip Select for LoRa (CE0)
NRF_CSN_PIN = 7     # SPI Chip Select for nRF (CE1)
NRF_CE_PIN = 23     # Chip Enable for nRF radio

# --- LOGGING CONFIGURATION ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(threadName)-15s - %(levelname)-8s - %(message)s'
)

def setup_gpio(lora_event: threading.Event, nrf_event: threading.Event):
    """
    Configures GPIO pins for interrupts and chip selects.
    Handles cases where hardware is not connected by skipping edge detection.
    """
    logging.info("Setting up GPIO...")
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    # Configure Chip Select and Chip Enable pins as outputs
    GPIO.setup(LORA_NSS_PIN, GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(NRF_CSN_PIN, GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(NRF_CE_PIN, GPIO.OUT, initial=GPIO.LOW)

    # Configure interrupt pins as inputs with pull-up resistors
    GPIO.setup(LORA_DIO0_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(NRF_IRQ_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    # --- Minimalist Interrupt Callbacks ---
    def lora_interrupt_callback(channel):
        if not lora_event.is_set():
            logging.debug(f"LoRa interrupt detected on channel {channel}.")
            lora_event.set()

    def nrf_interrupt_callback(channel):
        if not nrf_event.is_set():
            logging.debug(f"nRF interrupt detected on channel {channel}.")
            nrf_event.set()

    # Attach interrupt handlers with error handling
    try:
        GPIO.add_event_detect(LORA_DIO0_PIN, GPIO.FALLING,
                              callback=lora_interrupt_callback, bouncetime=50)
        logging.info("LoRa interrupt handler attached.")
    except RuntimeError as e:
        logging.warning(f"LoRa interrupt setup failed: {e}. Running without hardware?")

    try:
        GPIO.add_event_detect(NRF_IRQ_PIN, GPIO.FALLING,
                              callback=nrf_interrupt_callback, bouncetime=50)
        logging.info("nRF interrupt handler attached.")
    except RuntimeError as e:
        logging.warning(f"nRF interrupt setup failed: {e}. Running without hardware?")

    logging.info("GPIO setup complete (with fallbacks).")

def cleanup_gpio():
    """Cleans up GPIO resources on application exit."""
    logging.info("Cleaning up GPIO resources.")
    GPIO.cleanup()

def main():
    """Main application entry point and system supervisor."""
    logging.info("--- Starting SETU Gateway Node application ---")

    # 1. Initialize Shared Concurrency Primitives
    spi_lock = SPILock()
    high_priority_queue = queue.Queue(maxsize=100)
    low_priority_queue = queue.Queue(maxsize=500)
    alert_queue = queue.Queue(maxsize=50)
    lora_packet_event = threading.Event()
    nrf_packet_event = threading.Event()
    shutdown_event = threading.Event()

    # Load alerting rules if YAML is available
    if YAML_AVAILABLE:
        try:
            with open('rules.yaml', 'r') as f:
                alerting_rules = yaml.safe_load(f).get('rules', [])
                logging.info(f"Loaded {len(alerting_rules)} alerting rules.")
        except FileNotFoundError:
            logging.error("rules.yaml not found. Alerting will be disabled.")
            alerting_rules = []
    else:
        logging.warning("YAML not available. Skipping alerting rules.")
        alerting_rules = []

    # 2. Setup Hardware Interfaces
    setup_gpio(lora_packet_event, nrf_packet_event)

    # 3. Create and Start All Worker Threads
    threads = [
        LoRaWorkerThread(lora_packet_event, high_priority_queue, spi_lock, shutdown_event),
        nRFWorkerThread(nrf_packet_event, low_priority_queue, spi_lock, shutdown_event),
        DataProcessingThread(high_priority_queue, low_priority_queue, alert_queue, alerting_rules, shutdown_event),
        CommunicationsThread(alert_queue, shutdown_event)
    ]

    for t in threads:
        t.start()

    # 4. Main Loop (Watchdog and Shutdown Handler)
    try:
        while not shutdown_event.is_set():
            for t in threads:
                if not t.is_alive():
                    logging.critical(f"CRITICAL: Thread '{t.name}' has died unexpectedly!")
                    alert_message = f"FATAL: Gateway software failure. Thread '{t.name}' terminated."
                    try:
                        alert_queue.put(alert_message, block=False)
                    except queue.Full:
                        logging.error("Alert queue full, cannot dispatch watchdog alert.")
                    shutdown_event.set()
                    break
            time.sleep(5.0)

    except KeyboardInterrupt:
        logging.info("Shutdown signal received (Ctrl+C).")
    finally:
        logging.info("Initiating graceful shutdown...")
        shutdown_event.set()
        for t in threads:
            logging.info(f"Waiting for thread '{t.name}' to terminate...")
            t.join()
        cleanup_gpio()
        logging.info("--- SETU Gateway Node application has shut down cleanly. ---")

if __name__ == "__main__":
    main()
