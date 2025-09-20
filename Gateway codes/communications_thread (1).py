import threading
import logging
import queue
import time
import serial
import sqlite3
import json
from datetime import datetime
from typing import Tuple
# --- Configuration ---
# These should be moved to a central, secure config file in a production deployment.
ALERT_RECIPIENT_NUMBER = "+1234567890"  # Placeholder phone number
CLOUD_ENDPOINT_URL = "http://your-cloud-endpoint.com/api/setu-data" # Placeholder URL
GPRS_APN = "your_apn"  # e.g., "internet" or "airtelgprs.com"
GATEWAY_ID = "GW001"
DATABASE_FILE = "setu_gateway.db"

class SIM800LManager:
    """
    A helper class to manage AT command interactions with the SIM800L module.
    This class abstracts the complexity of serial communication, command timing,
    and response parsing.
    """
    def __init__(self, port='/dev/serial0', baudrate=9600, timeout=1):
        try:
            self.ser = serial.Serial(port, baudrate, timeout=timeout)
            self.ser.flush()
            logging.info(f"Serial port {port} opened for SIM800L.")
        except serial.SerialException as e:
            logging.critical(f"Could not open serial port {port}: {e}")
            self.ser = None

    def send_at_command(self, command: str, expected_response="OK", timeout=5) -> Tuple[bool, str]:
        """
        Sends an AT command and waits for an expected response. Handles timeouts.

        Returns: A tuple (bool, str) indicating success and the full response.
        """
        if not self.ser: return False, "Serial port not available"
        
        logging.debug(f"Sending AT command: {command}")
        self.ser.write((command + '\r\n').encode())
        
        start_time = time.time()
        response = ""
        while time.time() - start_time < timeout:
            line = self.ser.readline().decode('utf-8', 'ignore').strip()
            if line:
                logging.debug(f"SIM800L response: {line}")
                response += line + "\n"
                if expected_response in line:
                    return True, response
        logging.warning(f"Timeout waiting for '{expected_response}' for command '{command}'")
        return False, response

    def close(self):
        """Closes the serial port."""
        if self.ser and self.ser.is_open:
            self.ser.close()
            logging.info("Serial port for SIM800L closed.")

class CommunicationsThread(threading.Thread):
    """
    Thread for handling all outbound communications (SMS, Cloud).
    Isolates slow, blocking network I/O to prevent it from affecting
    the real-time data ingestion threads.
    """
    def __init__(self, alert_q: queue.Queue, shutdown_event: threading.Event):
        super().__init__(name="Communications")
        self.alert_q = alert_q
        self.shutdown_event = shutdown_event
        self.sim_manager = None
        logging.info("Communications Thread initialized.")

    def setup_sms(self) -> bool:
        """Initializes the SIM800L module for sending SMS."""
        try:
            if not self.sim_manager or not self.sim_manager.ser:
                self.sim_manager = SIM800LManager()
                if not self.sim_manager.ser: return False

            # Check basic communication and SIM status
            if not self.sim_manager.send_at_command("AT")[0]: return False
            if "+CPIN: READY" not in self.sim_manager.send_at_command("AT+CPIN?")[1]:
                logging.warning("SIM card not ready.")
                return False
            # Set to text mode
            if not self.sim_manager.send_at_command("AT+CMGF=1")[0]:
                logging.error("Failed to set SIM800L to text mode.")
                return False
            logging.info("SIM800L module is ready for SMS.")
            return True
        except Exception as e:
            logging.critical(f"Failed to initialize SIM800L: {e}")
            if self.sim_manager: self.sim_manager.close()
            self.sim_manager = None
            return False

    def send_sms(self, number: str, message: str) -> bool:
        """Sends an SMS message using the initialized SIM manager."""
        if not self.sim_manager: return False
        logging.info(f"Attempting to send SMS to {number}")
        success, _ = self.sim_manager.send_at_command(f'AT+CMGS="{number}"', expected_response=">")
        if success:
            self.sim_manager.ser.write(message.encode())
            time.sleep(0.1)
            self.sim_manager.ser.write(b'\x1a') # Ctrl+Z to send
            final_success, response = self.sim_manager.send_at_command("", expected_response="OK", timeout=60)
            if final_success:
                logging.info("SMS sent successfully.")
                return True
            logging.error(f"Failed to send SMS. Final response: {response}")
        return False

    def forward_data_to_cloud(self):
        """
        Reads unsent data from SQLite and transmits it to the cloud via HTTP POST.
        """
        logging.info("Checking for data to forward to the cloud...")
        # NOTE: Assumes 'fatigue_log' table has a 'sent_to_cloud' column.
        # It should be added with: ALTER TABLE fatigue_log ADD COLUMN sent_to_cloud INTEGER DEFAULT 0;
        
        # 1. Fetch unsent records from the database
        db_conn = sqlite3.connect(DATABASE_FILE)
        cursor = db_conn.cursor()
        cursor.execute("SELECT log_id, node_id, timestamp, bin_1_cycles, bin_2_cycles, bin_3_cycles FROM fatigue_log WHERE sent_to_cloud = 0 LIMIT 50")
        records = cursor.fetchall()
        
        if not records:
            logging.info("No new data to send.")
            db_conn.close()
            return
            
        log_ids_to_update = [row[0] for row in records]
        
        # 2. Format data into the required JSON payload
        payload_data = [
            {
                "gateway_id": GATEWAY_ID,
                "packet_id": row[0],
                "node_id": row[1],
                "timestamp": row[2],
                "fatigue_cycles": {
                    "bin_1": row[3],
                    "bin_2": row[4],
                    "bin_3": row[5]
                }
            } for row in records
        ]
        json_payload = json.dumps(payload_data)
        
        # 3. Execute HTTP POST using AT commands
        success = self._http_post_payload(json_payload)

        # 4. CRITICAL: Update database only on successful transmission
        if success:
            logging.info(f"Successfully sent {len(records)} records. Updating database.")
            placeholders = ','.join('?' for _ in log_ids_to_update)
            cursor.execute(f"UPDATE fatigue_log SET sent_to_cloud = 1 WHERE log_id IN ({placeholders})", log_ids_to_update)
            db_conn.commit()
        else:
            logging.error("Failed to send data to cloud. Database not updated.")
            
        db_conn.close()

    def _http_post_payload(self, payload: str) -> bool:
        """Handles the full AT command sequence for an HTTP POST request."""
        if not self.sim_manager: return False
        
        try:
            # Check GPRS and Network Registration
            if "+CREG: 0,1" not in self.sim_manager.send_at_command("AT+CREG?")[1] and \
               "+CREG: 0,5" not in self.sim_manager.send_at_command("AT+CREG?")[1]:
                logging.warning("Not registered on network.")
                return False
                
            # Enable GPRS bearer
            self.sim_manager.send_at_command(f'AT+SAPBR=3,1,"APN","{GPRS_APN}"')
            self.sim_manager.send_at_command("AT+SAPBR=1,1")

            # Initialize HTTP service
            self.sim_manager.send_at_command("AT+HTTPINIT")
            self.sim_manager.send_at_command('AT+HTTPPARA="CID",1')
            self.sim_manager.send_at_command(f'AT+HTTPPARA="URL","{CLOUD_ENDPOINT_URL}"')
            self.sim_manager.send_at_command('AT+HTTPPARA="CONTENT","application/json"')
            
            # Send data
            payload_size = len(payload)
            self.sim_manager.send_at_command(f"AT+HTTPDATA={payload_size},10000", expected_response="DOWNLOAD")
            logging.debug(f"Sending JSON payload: {payload}")
            self.sim_manager.ser.write(payload.encode())
            
            # Wait for OK after data is sent
            if not self.sim_manager.send_at_command("")[0]: return False
            
            # Execute POST action
            success, response = self.sim_manager.send_at_command("AT+HTTPACTION=1", expected_response="+HTTPACTION", timeout=30)
            
            # Check for HTTP 200 OK
            if success and "1,200" in response:
                logging.info("HTTP POST successful (200 OK).")
                return True
            else:
                logging.error(f"HTTP POST failed. Response: {response}")
                return False

        finally:
            # Cleanup HTTP and GPRS session
            self.sim_manager.send_at_command("AT+HTTPTERM")
            self.sim_manager.send_at_command("AT+SAPBR=0,1")

    def run(self):
        logging.info("Communications Thread started.")
        is_sms_ready = False
        last_cloud_upload_time = time.time() - 880 # Upload shortly after start

        while not self.shutdown_event.is_set():
            if not is_sms_ready:
                is_sms_ready = self.setup_sms()
                if not is_sms_ready:
                    self.shutdown_event.wait(10)
                    continue
            try:
                alert_message = self.alert_q.get(block=True, timeout=1.0)
                if not self.send_sms(ALERT_RECIPIENT_NUMBER, alert_message):
                    logging.warning("SMS failed. Re-queuing alert.")
                    self.alert_q.put(alert_message)
                    is_sms_ready = False
                self.alert_q.task_done()
            except queue.Empty:
                pass # Normal timeout, no alerts to send
            except Exception as e:
                logging.error(f"Error in communications loop: {e}", exc_info=True)
                is_sms_ready = False

            # Periodic cloud data forwarding
            if time.time() - last_cloud_upload_time > 900: # Every 15 minutes
                self.forward_data_to_cloud()
                last_cloud_upload_time = time.time()

        if self.sim_manager:
            self.sim_manager.close()
        logging.info("Communications Thread shutting down.")
