import threading
import logging
import queue
import time
from spi_lock import SPILock

# Import the actual radio libraries. These provide the interfaces to the hardware.
# In a real deployment, these would need to be installed via pip.
from LoRaRF import SX127x
from circuitpython_nrf24l01.rf24 import RF24
import spidev

# Define hardware-specific pin numbers based on the project documents.
# These are used for initializing the radio objects.
LORA_NSS_PIN = 8
NRF_CSN_PIN = 7
NRF_CE_PIN = 23

class LoRaWorkerThread(threading.Thread):
    """
    Worker thread for handling high-priority data from the LoRa Ra-02 module.

    This thread remains in a power-efficient blocked state until woken by a
    hardware interrupt on GPIO 25. It then acquires exclusive access to the
    SPI bus, reads the incoming packet, and places it into the high-priority queue.
    """
    def __init__(self, packet_event: threading.Event, data_queue: queue.Queue,
                 spi_lock: SPILock, shutdown_event: threading.Event):
        super().__init__(name="LoRaWorker")
        self.packet_event = packet_event
        self.data_queue = data_queue
        self.spi_lock = spi_lock
        self.shutdown_event = shutdown_event
        self.lora = None

        try:
            # Initialize the LoRa radio object. The actual SPI configuration
            # will be passed later inside the run loop.
            self.lora = SX127x()
            logging.info("LoRa radio object created.")
        except Exception as e:
            logging.critical(f"LoRa Worker failed to initialize radio object: {e}")

    def setup_lora(self, spi: spidev.SpiDev) -> bool:
        """Configures the LoRa radio for reception using an active SPI object."""
        try:
            self.lora.setSpi(spi)
            self.lora.setNss(LORA_NSS_PIN)
            # Note: The Reset pin is not used in this hardware design.
            if not self.lora.begin():
                logging.error("Failed to initialize LoRa radio.")
                return False
            self.lora.setFrequency(433000000)
            # Configure the radio to trigger DIO0 on packet reception (RxDone)
            self.lora.setDio0Irq(self.lora.DIO0_RX_DONE)
            self.lora.setRx()  # Set radio to continuous receive mode
            logging.info("LoRa radio configured for reception.")
            return True
        except Exception as e:
            logging.error(f"Exception during LoRa setup: {e}")
            return False

    def run(self):
        if not self.lora:
            logging.error("LoRaWorker cannot start, initialization failed.")
            return

        logging.info("LoRa Worker started.")
        is_lora_setup = False

        while not self.shutdown_event.is_set():
            # Wait for the interrupt to signal a new packet.
            # A timeout allows the thread to periodically check the shutdown_event.
            signaled = self.packet_event.wait(timeout=1.0)

            if signaled:
                # Clear the event immediately to be ready for the next interrupt.
                self.packet_event.clear()
                logging.debug("LoRa Worker woken by interrupt.")

                try:
                    # Acquire the SPI bus lock for device 0 (CE0).
                    with self.spi_lock.acquire(device=0, max_speed_hz=8000000) as spi:
                        if not is_lora_setup:
                            is_lora_setup = self.setup_lora(spi)
                        if not is_lora_setup:
                            time.sleep(5)  # Wait before retrying setup
                            continue

                        # Check if the interrupt was for a received packet
                        if self.lora.getIrqFlags() & self.lora.IRQ_RX_DONE_MASK:
                            self.lora.clearIrqFlags()
                            packet_payload, rssi, snr = self.lora.read()
                            logging.info(f"LoRa packet received! RSSI: {rssi}, SNR: {snr}")

                            try:
                                # Use a non-blocking put with a timeout.
                                self.data_queue.put(packet_payload, timeout=0.5)
                            except queue.Full:
                                logging.warning("High-priority queue is full. LoRa packet dropped.")
                        # Re-arm the receiver for the next packet
                        self.lora.setRx()
                except Exception as e:
                    logging.error(f"An error occurred in the LoRa worker loop: {e}", exc_info=True)
                    is_lora_setup = False  # Force re-setup on next attempt

        logging.info("LoRa Worker shutting down.")

class nRFWorkerThread(threading.Thread):
    """
    Worker thread for handling low-priority data from the nRF24L01+ module.

    This thread remains blocked until woken by a hardware interrupt on GPIO 22.
    It then acquires exclusive access to the SPI bus, reads the incoming packet,
    and places it into the low-priority queue.
    """
    def __init__(self, packet_event: threading.Event, data_queue: queue.Queue,
                 spi_lock: SPILock, shutdown_event: threading.Event):
        super().__init__(name="nRFWorker")
        self.packet_event = packet_event
        self.data_queue = data_queue
        self.spi_lock = spi_lock
        self.shutdown_event = shutdown_event
        self.nrf = None
        # The nRF library needs the SPI object passed during initialization.
        # We will handle this inside the run loop.

    def setup_nrf(self, spi: spidev.SpiDev) -> bool:
        """Configures the nRF24 radio for reception."""
        try:
            self.nrf = RF24(spi, NRF_CSN_PIN, NRF_CE_PIN)
            if not self.nrf.begin():
                logging.error("Failed to initialize nRF24 radio.")
                return False
            self.nrf.open_rx_pipe(1, b'\xac\xac\xac\xac\xac')
            self.nrf.listen = True
            logging.info("nRF24 radio configured for reception.")
            return True
        except Exception as e:
            logging.error(f"Exception during nRF24 setup: {e}")
            return False

    def run(self):
        logging.info("nRF Worker started.")
        is_nrf_setup = False

        while not self.shutdown_event.is_set():
            signaled = self.packet_event.wait(timeout=1.0)

            if signaled:
                self.packet_event.clear()
                logging.debug("nRF Worker woken by interrupt.")
                try:
                    # Acquire the SPI bus lock for device 1 (CE1).
                    with self.spi_lock.acquire(device=1, max_speed_hz=10000000) as spi:
                        if not is_nrf_setup:
                            is_nrf_setup = self.setup_nrf(spi)
                        if not is_nrf_setup:
                            time.sleep(5)
                            continue

                        # The nRF library handles IRQ clearing internally.
                        while self.nrf.available():
                            packet_payload = self.nrf.read()
                            logging.info(f"nRF packet received! Payload: {packet_payload}")
                            try:
                                self.data_queue.put(packet_payload, timeout=0.5)
                            except queue.Full:
                                logging.warning("Low-priority queue is full. nRF packet dropped.")

                except Exception as e:
                    logging.error(f"An error occurred in the nRF worker loop: {e}", exc_info=True)
                    is_nrf_setup = False

        logging.info("nRF Worker shutting down.")