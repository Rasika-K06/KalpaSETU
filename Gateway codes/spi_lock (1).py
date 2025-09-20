import threading
import spidev
import logging

# Configure logging to show thread names for clarity in debugging concurrent access.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(threadName)s - %(levelname)s - %(message)s'
)

class SPILock:
    """
    A thread-safe context manager for the shared SPI bus on a Raspberry Pi.

    This class ensures that only one thread can access the SPI bus at a time,
    preventing data corruption from simultaneous transmissions. It also handles
    the low-level details of opening, configuring, and closing the correct spidev
    device (CE0 or CE1) for each transaction.

    Its primary design feature is the guaranteed release of the underlying lock
    and the SPI device, even if hardware I/O errors occur, thus preventing
    system-wide deadlocks.

    Usage:
        spi_lock = SPILock()
        with spi_lock.acquire(device=0, max_speed_hz=5000000) as spi:
            # spi is now a configured spidev.SpiDev() object
            # This block has exclusive access to the SPI bus with device 0 (CE0)
            spi.xfer2([0x01, 0x02, 0x03])
    """

    def __init__(self):
        """Initializes the SPILock with a single threading.Lock."""
        self._lock = threading.Lock()
        self._spi = spidev.SpiDev()

    class _SPIDevice:
        """
        Inner class that acts as the actual context manager.
        This class is not intended to be instantiated directly.
        """
        def __init__(self, lock: threading.Lock, spi_instance: spidev.SpiDev,
                     bus: int, device: int, max_speed_hz: int):
            self._lock = lock
            self._spi = spi_instance
            self._bus = bus
            self._device = device
            self._max_speed_hz = max_speed_hz

        def __enter__(self):
            """
            Acquires the mutex and configures the SPI device.

            This method will block until the lock is available. Once acquired, it
            opens the specified spidev device and configures its speed.
            If any error occurs during device opening, it releases the lock
            to prevent a deadlock before raising the exception.
            """
            self._lock.acquire()
            try:
                logging.debug(f"Acquired SPI lock for device {self._device}.")
                self._spi.open(self._bus, self._device)
                self._spi.max_speed_hz = self._max_speed_hz
                return self._spi
            except Exception as e:
                # CRITICAL: If opening the device fails, we must release the lock
                # to prevent other threads from stalling indefinitely.
                self._lock.release()
                logging.error(f"Failed to open SPI device {self._device}: {e}")
                raise

        def __exit__(self, exc_type, exc_val, exc_tb):
            """
            Guarantees that the SPI device is closed and the lock is released.

            This method is called automatically when exiting the 'with' block.
            The 'finally' block ensures that the lock is always released,
            regardless of whether an exception occurred within the block. This is
            the core of the deadlock prevention mechanism.
            """
            try:
                self._spi.close()
            except Exception as e:
                logging.error(f"Error closing SPI device {self._device}: {e}")
            finally:
                # This lock release is guaranteed to execute, preventing livelocks.
                self._lock.release()
                logging.debug(f"Released SPI lock for device {self._device}.")

    def acquire(self, device: int, bus: int = 0, max_speed_hz: int = 1000000):
        """
        Returns a context manager for a specific SPI device.

        Args:
            device: The chip select device ID (0 for CE0, 1 for CE1).
            bus: The SPI bus ID (usually 0 on a Raspberry Pi).
            max_speed_hz: The clock speed for the SPI transaction.

        Returns:
            An instance of the _SPIDevice inner context manager.
        """
        return self._SPIDevice(self._lock, self._spi, bus, device, max_speed_hz)