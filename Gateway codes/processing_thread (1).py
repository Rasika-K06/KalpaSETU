import threading
import logging
import queue
import time
import sqlite3
import struct
import yaml
from datetime import datetime

class DataProcessingThread(threading.Thread):
    """
    The analytical core of the gateway. Processes data from high and low priority queues,
    persists it to a database, and runs an alerting engine.
    """
    def __init__(self, high_prio_q: queue.Queue, low_prio_q: queue.Queue,
                 alert_q: queue.Queue, rules: list, shutdown_event: threading.Event):
        super().__init__(name="DataProcessor")
        self.high_prio_q = high_prio_q
        self.low_prio_q = low_prio_q
        self.alert_q = alert_q
        self.rules = rules
        self.shutdown_event = shutdown_event
        self.db_conn = sqlite3.connect('setu_gateway.db')
        logging.info("Data Processor initialized.")

    def run(self):
        """Main processing loop with strict priority queuing."""
        logging.info("Data Processor started.")
        while not self.shutdown_event.is_set():
            try:
                processed_something = False
                # --- HIGH-PRIORITY QUEUE PROCESSING ---
                while not self.high_prio_q.empty():
                    lora_packet = self.high_prio_q.get_nowait()
                    self._process_lora_packet(lora_packet)
                    self.high_prio_q.task_done()
                    processed_something = True

                # --- LOW-PRIORITY QUEUE PROCESSING (only if high-prio is empty) ---
                while not self.low_prio_q.empty():
                    nrf_packet = self.low_prio_q.get_nowait()
                    self._process_nrf_packet(nrf_packet) # Calling the new function
                    self.low_prio_q.task_done()
                    processed_something = True

                if not processed_something:
                    time.sleep(0.1)
            except queue.Empty:
                time.sleep(0.1)
            except Exception as e:
                logging.error(f"An error occurred in the data processing loop: {e}", exc_info=True)

        self.db_conn.close()
        logging.info("Data Processor shutting down.")

    def _process_lora_packet(self, packet: bytes):
        """Handles deserialization, persistence, and alerting for a high-priority LoRa packet."""
        try:
            unpacked_data = struct.unpack('<H B III f f B', packet)
            node_id, _, bin1, bin2, bin3, _, _, _ = unpacked_data
        except struct.error:
            logging.error(f"Failed to unpack LoRa packet: {packet.hex()}")
            return
        try:
            cursor = self.db_conn.cursor()
            cursor.execute(
                "INSERT INTO fatigue_log (timestamp, node_id, bin_1_cycles, bin_2_cycles, bin_3_cycles, sent_to_cloud) VALUES (?, ?, ?, ?, ?, 0)",
                (datetime.utcnow().isoformat(), node_id, bin1, bin2, bin3)
            )
            self.db_conn.commit()
            logging.info(f"Logged fatigue data for Node {node_id}: Bins=({bin1}, {bin2}, {bin3})")
        except sqlite3.Error as e:
            logging.error(f"Database error for fatigue_log: {e}")
            self.db_conn.rollback()
            return
        self._check_alerting_rules(node_id, {'bin_1_cycles': bin1, 'bin_2_cycles': bin2, 'bin_3_cycles': bin3})

    # =================================================================
    # TASK GW-2: IMPLEMENT THIS FUNCTION
    # =================================================================
    def _process_nrf_packet(self, payload: bytes):
        """
        Deserializes a 5-byte nRF packet from a Scout-Node and persists
        the environmental data into the SQLite database.
        """
        # 1. Check if the payload is the correct length (5 bytes).
        if len(payload) != 5:
            logging.warning(f"Received nRF packet of incorrect length: {len(payload)} bytes. Discarding.")
            return

        try:
            # 2. Use struct.unpack to extract values. Format: Little-endian,
            #    uint8 (node_id), int16 (temp*100), uint16 (humidity*100).
            #    Note: The format string '<BhH' is correct for a packed 5-byte C struct.
            node_id, temp_scaled, hum_scaled = struct.unpack('<BhH', payload)

            # 3. Scale the integer values back to floating-point numbers.
            temperature = temp_scaled / 100.0
            humidity = hum_scaled / 100.0

            # 4. & 5. Connect to DB and execute INSERT in a transaction.
            cursor = self.db_conn.cursor()
            cursor.execute(
                """INSERT INTO environment_log (received_at, node_id, temperature_c, humidity_rh)
                   VALUES (?, ?, ?, ?)""",
                (datetime.utcnow().isoformat(), node_id, temperature, humidity)
            )
            self.db_conn.commit()

            # 7. Log a success message.
            logging.info(f"Logged environment data for Node {node_id}: Temp={temperature:.2f}C, Humidity={humidity:.2f}%")

        except struct.error:
            logging.error(f"Failed to unpack nRF packet: {payload.hex()}")
        except sqlite3.Error as e:
            logging.error(f"Database error for environment_log: {e}")
            self.db_conn.rollback()
        except Exception as e:
            logging.error(f"An unexpected error occurred in _process_nrf_packet: {e}")

    def _check_alerting_rules(self, node_id: int, data: dict):
        """Iterates through rules and generates alerts if thresholds are met."""
        for rule in self.rules:
            if rule.get('node_id') == node_id:
                field, threshold = rule.get('field_to_monitor'), rule.get('threshold')
                value = data.get(field)
                if value is not None and value > threshold:
                    alert_msg = rule['alert_message'].format(node=node_id, value=value, threshold=threshold)
                    try:
                        self.alert_q.put(alert_msg, block=False)
                        logging.warning(f"ALERT TRIGGERED: {alert_msg}")
                    except queue.Full:
                        logging.error("Alert queue is full. Cannot send new alert.")