import sqlite3
import logging

# Configure basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

DATABASE_FILE = 'setu_gateway.db'

def setup_database():
    """
    Creates and initializes all required SQLite database tables if they do not exist.
    This script is idempotent and can be run safely multiple times.
    """
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        # Create the 'nodes' table for sensor metadata
        logging.info("Creating 'nodes' table if not exists...")
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS nodes (
            node_id INTEGER PRIMARY KEY,
            location_description TEXT NOT NULL,
            install_date TEXT NOT NULL
        )
        ''')

        # Create the 'fatigue_log' table for high-priority structural data
        logging.info("Creating 'fatigue_log' table if not exists...")
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS fatigue_log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            node_id INTEGER NOT NULL,
            bin_1_cycles INTEGER NOT NULL,
            bin_2_cycles INTEGER NOT NULL,
            bin_3_cycles INTEGER NOT NULL,
            sent_to_cloud INTEGER DEFAULT 0,
            FOREIGN KEY(node_id) REFERENCES nodes(node_id)
        )
        ''')

        # =================================================================
        # TASK GW-2: ADD THE NEW TABLE CREATION LOGIC HERE
        # =================================================================
        # Create the 'environment_log' table for low-priority contextual data
        logging.info("Creating 'environment_log' table if not exists...")
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS environment_log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at TEXT NOT NULL,
            node_id INTEGER NOT NULL,
            temperature_c REAL NOT NULL,
            humidity_rh REAL NOT NULL
        )
        ''')
        # =================================================================

        # Add a sample node for testing purposes
        try:
            cursor.execute("INSERT INTO nodes (node_id, location_description, install_date) VALUES (?,?,?)",
                           (1, "Girder A, North Abutment", "2025-08-26"))
            logging.info("Inserted sample node with ID 1.")
        except sqlite3.IntegrityError:
            logging.info("Sample node with ID 1 already exists.")

        conn.commit()
        logging.info(f"Database '{DATABASE_FILE}' and all tables are set up successfully.")

    except sqlite3.Error as e:
        logging.error(f"Database setup failed: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    setup_database()