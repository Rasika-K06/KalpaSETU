import sqlite3
import csv
import gzip
import os
import logging
from datetime import datetime, timedelta
import traceback

# --- Configuration (sourced from environment variables with defaults) ---
# This makes the script configurable without code changes.
DATABASE_FILE = os.environ.get('SETU_DB_PATH', 'setu_gateway.db')
ARCHIVE_DIR = os.environ.get('SETU_ARCHIVE_DIR', 'archive')
# ARCHIVE_DAYS_THRESHOLD can also be an environment variable if needed
ARCHIVE_DAYS_THRESHOLD = int(os.environ.get('SETU_ARCHIVE_DAYS', 365))

# --- Logging Setup ---
# Configure logging for cron job output redirection
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def archive_and_purge():
    """
    Archives old records from the fatigue_log table to a compressed CSV file
    and then transactionally purges them from the live database.
    """
    logging.info("--- Archive and Purge Process Started ---")

    # Use os.path.join for robust path construction
    db_path = os.path.abspath(DATABASE_FILE)
    archive_path = os.path.abspath(ARCHIVE_DIR)

    # 1. Pre-flight checks
    if not os.path.exists(db_path):
        logging.critical(f"Database file not found at {db_path}. Aborting.")
        return

    try:
        os.makedirs(archive_path, exist_ok=True)
    except OSError as e:
        logging.critical(f"Could not create archive directory at {archive_path}: {e}")
        return

    # Calculate the cutoff date for records to be archived
    cutoff_date = (datetime.utcnow() - timedelta(days=ARCHIVE_DAYS_THRESHOLD)).isoformat()
    conn = None
    
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        cursor = conn.cursor()

        # 2. Select old records
        logging.info(f"Selecting records from fatigue_log older than {cutoff_date}...")
        cursor.execute("SELECT * FROM fatigue_log WHERE timestamp < ?", (cutoff_date,))
        records = cursor.fetchall()

        if not records:
            logging.info("No records old enough to archive. Process finished.")
            return

        logging.info(f"Found {len(records)} records to archive.")
        header = [description[0] for description in cursor.description]

        # 3. Export to a compressed CSV file
        archive_filename = f"fatigue_log_archive_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv.gz"
        archive_filepath = os.path.join(archive_path, archive_filename)
        
        logging.info(f"Writing records to archive file: {archive_filepath}")
        with gzip.open(archive_filepath, 'wt', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(records)
        logging.info("Successfully wrote archive file.")

        # 4. CRITICAL: Purge old records in a single, safe transaction
        # This block ensures that DELETE only happens if the file write was successful.
        logging.info("Purging archived records from the live database...")
        with conn: # Using 'with conn' creates an automatic transaction
            cursor.execute("DELETE FROM fatigue_log WHERE timestamp < ?", (cutoff_date,))
            logging.info(f"Successfully purged {cursor.rowcount} records from the live database.")

    except Exception as e:
        # Catch all exceptions to provide detailed logs for cron debugging
        logging.critical("An unexpected error occurred during the archive process.")
        logging.critical(traceback.format_exc())
    finally:
        if conn:
            conn.close()
            logging.info("Database connection closed.")
        logging.info("--- Archive and Purge Process Finished ---")

if __name__ == "__main__":
    archive_and_purge()