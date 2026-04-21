"""
Session Tracker - Saves bot session statistics to SQLite database
"""

import sqlite3
import os
import time
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'sessions.db')


def init_db():
    """Initialize the database and create tables if they don't exist"""
    # Ensure data directory exists
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Sessions table
    c.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TEXT NOT NULL,
            end_time TEXT,
            duration_minutes REAL,
            total_bought INTEGER DEFAULT 0,
            total_sold INTEGER DEFAULT 0,
            total_spent INTEGER DEFAULT 0,
            total_earned INTEGER DEFAULT 0,
            cycles INTEGER DEFAULT 0
        )
    ''')
    
    # Buys table - individual buy transactions
    c.execute('''
        CREATE TABLE IF NOT EXISTS buys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            item_name TEXT NOT NULL,
            quantity INTEGER DEFAULT 1,
            unit_price INTEGER NOT NULL,
            total_price INTEGER NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    ''')
    
    # Sells table - individual sell listings created
    c.execute('''
        CREATE TABLE IF NOT EXISTS sells (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            item_name TEXT NOT NULL,
            quantity INTEGER DEFAULT 1,
            price INTEGER NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    ''')
    
    # Price snapshots - market prices captured during scans
    c.execute('''
        CREATE TABLE IF NOT EXISTS price_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            item_name TEXT NOT NULL,
            lowest_price INTEGER,
            avg_price INTEGER,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    ''')
    
    # Relists - stale listings that were canceled and relisted
    c.execute('''
        CREATE TABLE IF NOT EXISTS relists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            item_name TEXT NOT NULL,
            old_price INTEGER,
            new_price INTEGER,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    ''')
    
    conn.commit()
    conn.close()
    print(f"[SESSION] Database initialized at {DB_PATH}")


class SessionTracker:
    def __init__(self):
        self.session_id = None
        self.start_time = None
        self.cycles = 0
        self.stats = {
            'total_bought': 0,
            'total_sold': 0,
            'total_spent': 0,
            'total_earned': 0
        }
        init_db()
    
    def start_session(self):
        """Start a new session"""
        self.start_time = datetime.now()
        self.cycles = 0
        self.stats = {
            'total_bought': 0,
            'total_sold': 0,
            'total_spent': 0,
            'total_earned': 0
        }
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            INSERT INTO sessions (start_time)
            VALUES (?)
        ''', (self.start_time.isoformat(),))
        self.session_id = c.lastrowid
        conn.commit()
        conn.close()
        
        print(f"[SESSION] Started session #{self.session_id} at {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        return self.session_id
    
    def end_session(self):
        """End the current session and save final stats"""
        if not self.session_id:
            return
        
        end_time = datetime.now()
        duration = (end_time - self.start_time).total_seconds() / 60  # minutes
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            UPDATE sessions
            SET end_time = ?,
                duration_minutes = ?,
                total_bought = ?,
                total_sold = ?,
                total_spent = ?,
                total_earned = ?,
                cycles = ?
            WHERE id = ?
        ''', (
            end_time.isoformat(),
            round(duration, 2),
            self.stats['total_bought'],
            self.stats['total_sold'],
            self.stats['total_spent'],
            self.stats['total_earned'],
            self.cycles,
            self.session_id
        ))
        conn.commit()
        conn.close()
        
        print(f"\n{'='*50}")
        print(f"[SESSION] Session #{self.session_id} ended")
        print(f"  Duration: {duration:.1f} minutes")
        print(f"  Cycles: {self.cycles}")
        print(f"  Items bought: {self.stats['total_bought']}")
        print(f"  Items sold: {self.stats['total_sold']}")
        print(f"  Total spent: {self.stats['total_spent']:,}")
        print(f"  Total earned: {self.stats['total_earned']:,}")
        print(f"{'='*50}\n")
    
    def increment_cycle(self):
        """Increment cycle counter"""
        self.cycles += 1
    
    def record_buy(self, item_name, quantity, unit_price, total_price):
        """Record an item purchase"""
        if not self.session_id:
            return
        
        self.stats['total_bought'] += quantity
        self.stats['total_spent'] += total_price
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            INSERT INTO buys (session_id, timestamp, item_name, quantity, unit_price, total_price)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            self.session_id,
            datetime.now().isoformat(),
            item_name,
            quantity,
            unit_price,
            total_price
        ))
        conn.commit()
        conn.close()
    
    def record_sell(self, item_name, quantity, price):
        """Record an item listing"""
        if not self.session_id:
            return
        
        self.stats['total_sold'] += quantity
        self.stats['total_earned'] += price  # Note: This is listing price, not actual sale
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            INSERT INTO sells (session_id, timestamp, item_name, quantity, price)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            self.session_id,
            datetime.now().isoformat(),
            item_name,
            quantity,
            price
        ))
        conn.commit()
        conn.close()
    
    def record_price_snapshot(self, item_name, lowest_price, avg_price=None):
        """Record market price for an item"""
        if not self.session_id:
            return
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            INSERT INTO price_snapshots (session_id, timestamp, item_name, lowest_price, avg_price)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            self.session_id,
            datetime.now().isoformat(),
            item_name,
            lowest_price,
            avg_price
        ))
        conn.commit()
        conn.close()
    
    def record_relist(self, item_name, old_price, new_price):
        """Record a stale relist"""
        if not self.session_id:
            return
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            INSERT INTO relists (session_id, timestamp, item_name, old_price, new_price)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            self.session_id,
            datetime.now().isoformat(),
            item_name,
            old_price,
            new_price
        ))
        conn.commit()
        conn.close()


# Global session tracker instance
session = SessionTracker()
