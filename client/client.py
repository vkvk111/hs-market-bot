"""
Hero Siege Market Bot - Simple Client
Checks if the market is open using template matching
"""

import time
import ctypes
import re
import json
import keyboard
import threading
from screen_reader import ScreenReader
from session_tracker import session
import requests

try:
    import websocket
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False
    print("Warning: websocket-client not available. Install with: pip install websocket-client")

# Try to import pytesseract for OCR
try:
    import pytesseract
    from PIL import Image
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    print("Warning: pytesseract not available. Install with: pip install pytesseract pillow")
    print("Also install Tesseract OCR: https://github.com/UB-Mannheim/tesseract/wiki")

# Windows API constants for SendInput
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000

# Keyboard constants
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

# Get screen dimensions for absolute positioning
user32 = ctypes.windll.user32
SCREEN_WIDTH = user32.GetSystemMetrics(0)
SCREEN_HEIGHT = user32.GetSystemMetrics(1)

# Track listing creation times: {item_name: [timestamp, timestamp, ...]}
# Used to detect stale listings
listing_timestamps = {}


def focus_game_window(window_title="Hero Siege"):
    """Focus the game window by title using Windows API"""
    hwnd = user32.FindWindowW(None, window_title)
    if hwnd:
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.1)
        return True
    else:
        # Try partial match
        import ctypes.wintypes
        EnumWindows = user32.EnumWindows
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.c_void_p)
        GetWindowTextW = user32.GetWindowTextW
        GetWindowTextLengthW = user32.GetWindowTextLengthW
        
        found_hwnd = None
        
        def enum_callback(hwnd, lParam):
            nonlocal found_hwnd
            length = GetWindowTextLengthW(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                GetWindowTextW(hwnd, buff, length + 1)
                if window_title.lower() in buff.value.lower():
                    found_hwnd = hwnd
                    return False  # Stop enumeration
            return True
        
        EnumWindows(EnumWindowsProc(enum_callback), 0)
        
        if found_hwnd:
            user32.SetForegroundWindow(found_hwnd)
            time.sleep(0.1)
            return True
    
    print(f"  Warning: Could not find window '{window_title}'")
    return False


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT)
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("union", INPUT_UNION)
    ]


def move_mouse(x, y, steps=15, duration=0.15):
    """Smoothly move the mouse to target coordinates"""
    import random
    
    # Get current cursor position
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    start_x, start_y = pt.x, pt.y
    
    step_delay = duration / steps
    
    for i in range(1, steps + 1):
        t = i / steps
        # Ease-in-out interpolation
        t = t * t * (3 - 2 * t)
        cur_x = int(start_x + (x - start_x) * t)
        cur_y = int(start_y + (y - start_y) * t)
        
        abs_x = int(cur_x * 65535 / SCREEN_WIDTH)
        abs_y = int(cur_y * 65535 / SCREEN_HEIGHT)
        
        move = INPUT()
        move.type = 0  # INPUT_MOUSE
        move.union.mi.dx = abs_x
        move.union.mi.dy = abs_y
        move.union.mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
        ctypes.windll.user32.SendInput(1, ctypes.byref(move), ctypes.sizeof(INPUT))
        
        time.sleep(step_delay)


def click(x, y):
    """Move mouse smoothly to position, then click"""
    # Smooth move first
    move_mouse(x, y)
    
    # Set cursor precisely with SetCursorPos (more reliable for games)
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    
    # Convert to normalized absolute coordinates (0-65535)
    abs_x = int(x * 65535 / SCREEN_WIDTH)
    abs_y = int(y * 65535 / SCREEN_HEIGHT)
    
    # Mouse down
    down = INPUT()
    down.type = 0
    down.union.mi.dx = abs_x
    down.union.mi.dy = abs_y
    down.union.mi.dwFlags = MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE
    ctypes.windll.user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
    
    time.sleep(0.05)
    
    # Mouse up
    up = INPUT()
    up.type = 0
    up.union.mi.dx = abs_x
    up.union.mi.dy = abs_y
    up.union.mi.dwFlags = MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE
    ctypes.windll.user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))


def double_click(x, y):
    """Move mouse smoothly to position, then double-click"""
    # Smooth move first
    move_mouse(x, y)
    
    # Set cursor precisely with SetCursorPos (more reliable for games)
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    
    # Convert to normalized absolute coordinates (0-65535)
    abs_x = int(x * 65535 / SCREEN_WIDTH)
    abs_y = int(y * 65535 / SCREEN_HEIGHT)
    
    # First click
    down = INPUT()
    down.type = 0
    down.union.mi.dx = abs_x
    down.union.mi.dy = abs_y
    down.union.mi.dwFlags = MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE
    ctypes.windll.user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
    time.sleep(0.03)
    
    up = INPUT()
    up.type = 0
    up.union.mi.dx = abs_x
    up.union.mi.dy = abs_y
    up.union.mi.dwFlags = MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE
    ctypes.windll.user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))
    
    time.sleep(0.08)  # Short delay between clicks
    
    # Second click
    down2 = INPUT()
    down2.type = 0
    down2.union.mi.dx = abs_x
    down2.union.mi.dy = abs_y
    down2.union.mi.dwFlags = MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE
    ctypes.windll.user32.SendInput(1, ctypes.byref(down2), ctypes.sizeof(INPUT))
    time.sleep(0.03)
    
    up2 = INPUT()
    up2.type = 0
    up2.union.mi.dx = abs_x
    up2.union.mi.dy = abs_y
    up2.union.mi.dwFlags = MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE
    ctypes.windll.user32.SendInput(1, ctypes.byref(up2), ctypes.sizeof(INPUT))


# Mouse wheel constants
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120  # Standard scroll amount


def scroll_down(x, y, clicks=1):
    """
    Scroll down at the specified position.
    
    Args:
        x, y: Screen position to scroll at
        clicks: Number of scroll wheel clicks (positive = down)
    """
    # Move cursor to position first
    ctypes.windll.user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    
    # Send scroll event
    scroll = INPUT()
    scroll.type = 0  # INPUT_MOUSE
    scroll.union.mi.dx = 0
    scroll.union.mi.dy = 0
    scroll.union.mi.mouseData = ctypes.c_ulong(-WHEEL_DELTA * clicks).value  # Negative = scroll down
    scroll.union.mi.dwFlags = MOUSEEVENTF_WHEEL
    scroll.union.mi.time = 0
    scroll.union.mi.dwExtraInfo = ctypes.POINTER(ctypes.c_ulong)()
    
    ctypes.windll.user32.SendInput(1, ctypes.byref(scroll), ctypes.sizeof(INPUT))
    time.sleep(0.1)


def type_text(text):
    """
    Type a string of text using SendInput with Unicode characters.
    Works with any characters including special symbols.
    
    Args:
        text: The string to type
    """
    for char in text:
        # Get unicode value of character
        code = ord(char)
        
        # Key down
        down = INPUT()
        down.type = 1  # INPUT_KEYBOARD
        down.union.ki.wVk = 0
        down.union.ki.wScan = code
        down.union.ki.dwFlags = KEYEVENTF_UNICODE
        ctypes.windll.user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
        
        # Key up
        up = INPUT()
        up.type = 1  # INPUT_KEYBOARD
        up.union.ki.wVk = 0
        up.union.ki.wScan = code
        up.union.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
        ctypes.windll.user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))
        
        time.sleep(0.01)


def press_key(vk_code):
    """
    Press a virtual key using SendInput.
    Common codes: VK_RETURN=0x0D (Enter), VK_ESCAPE=0x1B, VK_SPACE=0x20
    
    Args:
        vk_code: Virtual key code
    """
    # Key down
    down = INPUT()
    down.type = 1  # INPUT_KEYBOARD
    down.union.ki.wVk = vk_code
    down.union.ki.wScan = 0
    down.union.ki.dwFlags = 0
    ctypes.windll.user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
    
    time.sleep(0.05)
    
    # Key up
    up = INPUT()
    up.type = 1  # INPUT_KEYBOARD
    up.union.ki.wVk = vk_code
    up.union.ki.wScan = 0
    up.union.ki.dwFlags = KEYEVENTF_KEYUP
    ctypes.windll.user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))


def press_key_combo(modifier_vk, key_vk):
    """
    Press a key combination (e.g., Ctrl+A, Ctrl+C).
    
    Args:
        modifier_vk: Modifier key code (e.g., 0x11 for Ctrl, 0x10 for Shift)
        key_vk: Main key code (e.g., 0x41 for 'A')
    """
    # Modifier down
    mod_down = INPUT()
    mod_down.type = 1
    mod_down.union.ki.wVk = modifier_vk
    mod_down.union.ki.wScan = 0
    mod_down.union.ki.dwFlags = 0
    ctypes.windll.user32.SendInput(1, ctypes.byref(mod_down), ctypes.sizeof(INPUT))
    
    time.sleep(0.02)
    
    # Key down
    key_down = INPUT()
    key_down.type = 1
    key_down.union.ki.wVk = key_vk
    key_down.union.ki.wScan = 0
    key_down.union.ki.dwFlags = 0
    ctypes.windll.user32.SendInput(1, ctypes.byref(key_down), ctypes.sizeof(INPUT))
    
    time.sleep(0.02)
    
    # Key up
    key_up = INPUT()
    key_up.type = 1
    key_up.union.ki.wVk = key_vk
    key_up.union.ki.wScan = 0
    key_up.union.ki.dwFlags = KEYEVENTF_KEYUP
    ctypes.windll.user32.SendInput(1, ctypes.byref(key_up), ctypes.sizeof(INPUT))
    
    time.sleep(0.02)
    
    # Modifier up
    mod_up = INPUT()
    mod_up.type = 1
    mod_up.union.ki.wVk = modifier_vk
    mod_up.union.ki.wScan = 0
    mod_up.union.ki.dwFlags = KEYEVENTF_KEYUP
    ctypes.windll.user32.SendInput(1, ctypes.byref(mod_up), ctypes.sizeof(INPUT))


# Global flag to stop the script
running = True

# Global command listener for sending messages to server
command_listener = None


def focus_game():
    """Click on center top of screen to focus the game window"""
    click(SCREEN_WIDTH // 2, 50)
    time.sleep(0.2)


def is_searchbox_empty():
    """
    Check if the search box is empty by capturing the text area and checking if it's mostly black.
    Search box text area coordinates: (1011, 243) to (1511, 258)
    
    Returns: True if empty (mostly black), False if has text
    """
    import cv2
    import numpy as np
    import mss
    
    try:
        with mss.mss() as sct:
            region = {"left": 1011, "top": 243, "width": 500, "height": 15}
            img = np.array(sct.grab(region))[:, :, :3]
        
        # Convert to grayscale and check average brightness
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        avg_brightness = np.mean(gray)
        
        # If average brightness is very low (mostly black), the box is empty
        is_empty = avg_brightness < 20
        print(f"    [Search box] avg brightness: {avg_brightness:.1f} -> {'empty' if is_empty else 'has text'}")
        return is_empty
    except Exception as e:
        print(f"    [Search box] capture failed: {e}, assuming empty")
        return True


def search_item(item_name):
    """
    Search for an item in the market.
    
    Args:
        item_name: The name of the item to search for
    """
    # Click the clear/X button until search box is empty
    max_attempts = 5
    for attempt in range(max_attempts):
        print(f"  Clicking clear button (1528, 232) - attempt {attempt + 1}")
        click(1528, 232)
        time.sleep(0.3)
        
        if is_searchbox_empty():
            break
    else:
        print(f"  ⚠️ Search box not empty after {max_attempts} attempts, continuing anyway")
    
    # Click the search box
    print(f"  Clicking search box (1280, 250)")
    click(1280, 250)
    time.sleep(0.2)
    
    # Type the search text
    print(f"  Typing: {item_name}")
    type_text(item_name)
    time.sleep(0.8)
    
    # Select from dropdown list
    # OLD: Click dropdown
    # print(f"  Clicking dropdown (1280, 290)")
    # click(1280, 290)
    # time.sleep(0.8)
    
    # NEW: Press Enter twice to select
    print(f"  Pressing Enter twice to select")
    press_key(0x0D)  # Enter
    time.sleep(0.3)
    press_key(0x0D)  # Enter
    time.sleep(0.8)
    
    # Click refresh button
    print(f"  Clicking refresh (1660, 250)")
    click(1660, 250)


def scan_prices(reader):
    """
    Scan and read prices from the market listings by finding gold icons.
    
    Args:
        reader: ScreenReader instance
    
    Returns:
        dict with 'lowest' (lowest unit price data) and 'all' (list of all price data)
    """
    import cv2
    import numpy as np
    
    if not OCR_AVAILABLE:
        print("❌ OCR not available - install pytesseract")
        return {'lowest': None, 'all': []}
    
    if 'gold_price' not in reader.templates:
        print("❌ No gold_price.png template found!")
        return {'lowest': None, 'all': []}
    
    # Capture full market region
    market_x, market_y = 125, 406
    market_w, market_h = 2400 - 125, 1400 - 406
    
    full_img = reader.capture_region(market_x, market_y, market_w, market_h)
    
    # Find all gold icons
    gray = cv2.cvtColor(full_img, cv2.COLOR_BGR2GRAY)
    template = reader.templates['gold_price']
    
    result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
    threshold = 0.8
    
    locations = []
    h, w = template.shape[:2]
    loc = np.where(result >= threshold)
    
    for pt in zip(*loc[::-1]):
        too_close = False
        for existing in locations:
            if abs(pt[0] - existing[0]) < w and abs(pt[1] - existing[1]) < h:
                too_close = True
                break
        if not too_close:
            locations.append(pt)
    
    locations.sort(key=lambda p: (p[1], p[0]))
    
    prices = []
    
    for icon_x, icon_y in locations:
        price_x = icon_x + w + 5
        price_y = max(0, icon_y - 5)
        price_w = 200
        price_h = h + 10
        
        if price_x + price_w > full_img.shape[1]:
            price_w = full_img.shape[1] - price_x
        
        price_img = full_img[price_y:price_y+price_h, price_x:price_x+price_w]
        
        if price_img.size > 0:
            gray_price = cv2.cvtColor(price_img, cv2.COLOR_BGR2GRAY)
            gray_price = cv2.resize(gray_price, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            _, thresh = cv2.threshold(gray_price, 80, 255, cv2.THRESH_BINARY)
            
            from PIL import Image
            pil_img = Image.fromarray(thresh)
            text = pytesseract.image_to_string(pil_img, config='--psm 7 -c tessedit_char_whitelist=0123456789,. xX')
            text = text.strip()
            price_data = parse_price(text)
            if price_data:
                price_data['coords'] = (icon_x + w // 2, icon_y + h // 2)  # Save center of gold icon as click target
                prices.append(price_data)
    
    lowest = min(prices, key=lambda p: p['unit_price']) if prices else None
    
    return {'lowest': lowest, 'all': prices}


def parse_price(text):
    """
    Parse a price string in format: '210,000 3 X70,000'
    - First number: total price
    - Number before X: quantity
    - Number after X: unit price
    
    Returns: dict with 'total', 'quantity', 'unit_price' or None
    """
    if not text:
        return None
    
    text = text.strip().upper()
    print(f"    [DEBUG] Parsing price text: '{text}'")
    
    # Try to parse format: "TOTAL QTY XUNIT"
    # Example: "210,000 3 X70,000"
    
    # Find X and split
    if 'X' in text:
        parts = text.split('X')
        if len(parts) == 2:
            left = parts[0].strip()  # "210,000 3"
            right = parts[1].strip() # "70,000"
            
            # Parse unit price (after X)
            unit_price = parse_number(right)
            
            # Parse left side - split into total and quantity
            left_parts = left.split()
            if len(left_parts) >= 2:
                total_str = left_parts[0]
                qty_str = left_parts[-1]
                
                total = parse_number(total_str)
                quantity = parse_number(qty_str) or 1
                
                print(f"    [DEBUG] Parsed: total={total}, qty={quantity}, unit={unit_price}")
                
                if total and unit_price:
                    # Sanity check: unit_price * quantity should roughly equal total
                    expected_total = unit_price * quantity
                    if abs(expected_total - total) <= total * 0.1:  # Allow 10% margin for rounding
                        return {
                            'total': total,
                            'quantity': quantity,
                            'unit_price': unit_price
                        }
                    else:
                        # OCR error - calculate unit price from total/quantity
                        corrected_unit = total // quantity
                        print(f"    [DEBUG] Mismatch! {unit_price}*{quantity}={expected_total} != {total}, using {corrected_unit}")
                        return {
                            'total': total,
                            'quantity': quantity,
                            'unit_price': corrected_unit
                        }
            elif len(left_parts) == 1:
                # Just total before quantity X unit
                total = parse_number(left_parts[0])
                if total and unit_price:
                    return {
                        'total': total,
                        'quantity': 1,
                        'unit_price': unit_price
                    }
    
    # Fallback: just a single number (total price, qty 1)
    total = parse_number(text)
    if total:
        return {
            'total': total,
            'quantity': 1,
            'unit_price': total
        }
    
    return None


def parse_number(text):
    """Parse a number string like '210,000' to integer"""
    if not text:
        return None
    
    text = text.strip().replace(',', '').replace(' ', '')
    
    # Strip leading non-digit characters (OCR artifacts like ', ", *, F, etc.)
    while text and not text[0].isdigit():
        # Common OCR misreads: $ -> 8
        if text[0] == '$':
            text = '8' + text[1:]
        else:
            text = text[1:]
    
    if not text:
        return None
    
    try:
        return int(float(text))
    except (ValueError, TypeError):
        return None


def fetch_buy_thresholds():
    """Fetch buy thresholds from the server API"""
    try:
        resp = requests.get('http://localhost:8080/api/buy_thresholds', timeout=2)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"[WARN] Could not fetch buy thresholds: {e}")
    return {}


def fetch_buy_options():
    """Fetch buy options (auto-buy settings) from the server API"""
    try:
        resp = requests.get('http://localhost:8080/api/buy_options', timeout=2)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"[WARN] Could not fetch buy options: {e}")
    return {'autoBuyEnabled': False, 'items': {}, 'boughtCounts': {}}


def fetch_hourly_averages():
    """Fetch 1h average prices for all enabled items"""
    try:
        resp = requests.get('http://localhost:8080/api/prices/averages', timeout=2)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"[WARN] Could not fetch 1h averages: {e}")
    return {}


def report_buy_cycle_result(item_name, bought, current_price):
    """Report cycle result to server for auto-pricing adjustment"""
    try:
        resp = requests.post('http://localhost:8080/api/buy_cycle_result', 
            json={'item': item_name, 'bought': bought, 'currentPrice': current_price},
            timeout=2)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('adjusted'):
                print(f"  [AUTO-PRICE] {item_name}: {data['oldPrice']:,} → {data['newPrice']:,} ({data['reason']})")
            return data
    except Exception as e:
        print(f"[WARN] Could not report cycle result: {e}")
    return None


def fetch_sell_config():
    """Fetch sell config from the server API"""
    try:
        resp = requests.get('http://localhost:8080/api/sell_config', timeout=2)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"[WARN] Could not fetch sell config: {e}")
    return {}


def count_listings_by_item(listings):
    """Count current listings per item name (normalized)"""
    counts = {}
    for item in listings:
        name = (item.get('name') or 'Unknown').upper().replace("'", "").replace("'", "").strip()
        counts[name] = counts.get(name, 0) + 1
    
    # Debug: print all counted items
    print(f"[DEBUG] Listing counts from {len(listings)} total listings:")
    for name, count in sorted(counts.items()):
        print(f"  - {name}: {count}")
    
    return counts


def normalize_item_name(name):
    """Normalize item name for comparison"""
    return (name or '').upper().replace("'", "").replace("'", "").strip()


# Sell UI positions
SELL_ITEM_BTN = (441, 373)        # Button to open sell dialog
AMOUNT_CONFIRM_BTN = (1152, 768)  # Confirm amount selection
SELL_SLOT_BTN = (851, 575)        # Place item in selling slot
PRICE_INPUT_BOX = (837, 746)      # Click to select price input
SELL_CONFIRM_BTN = (834, 926)     # Final confirm sell button


def shift_click(x, y):
    """Shift+click at coordinates for item selection"""
    VK_SHIFT = 0x10
    KEYEVENTF_KEYUP = 0x0002
    
    ctypes.windll.user32.keybd_event(VK_SHIFT, 0, 0, 0)
    time.sleep(0.05)
    click(x, y)
    time.sleep(0.05)
    ctypes.windll.user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.1)


def sell_item(item_name, position, quantity_per_listing, price):
    """
    Perform the sell sequence for one item.
    Returns True if successful, False otherwise.
    """
    global command_listener
    print(f"\n--- Selling {quantity_per_listing}x {item_name} at {price} ---")
    
    # Click sell tab first
    click_sell_tab()
    time.sleep(0.4)
    
    # Click the Sell Item button
    click(*SELL_ITEM_BTN)
    time.sleep(0.4)
    
    # Shift+click the item to select it
    shift_click(*position)
    time.sleep(0.5)
    
    # Clear any existing value and type quantity
    VK_BACK = 0x08  # Backspace
    press_key(VK_BACK)
    time.sleep(0.1)
    type_text(str(quantity_per_listing))
    time.sleep(0.2)
    
    # Click confirm amount
    click(*AMOUNT_CONFIRM_BTN)
    time.sleep(0.4)
    
    # Click sell slot to place item
    click(*SELL_SLOT_BTN)
    time.sleep(0.4)
    
    # Click price input box
    click(*PRICE_INPUT_BOX)
    time.sleep(0.3)
    
    # Type the price
    type_text(str(price))
    time.sleep(0.3)
    
    # Click final confirm
    click(*SELL_CONFIRM_BTN)
    time.sleep(0.5)
    
    print(f"[SELL] Listed {quantity_per_listing}x {item_name} at {price}")
    
    # Record sell to session (local tracking)
    session.record_sell(item_name, quantity_per_listing, price)
    
    # Notify server that item was listed (for stock tracking)
    if command_listener and command_listener.ws:
        command_listener._send({
            'type': 'item_listed',
            'item': item_name,
            'quantity': quantity_per_listing,
            'price': price
        })
    
    return True


def calculate_sell_price(item_name, min_price):
    """
    Calculate optimal sell price based on market conditions.
    
    Strategy:
    - Take average of 2 cheapest listings
    - Undercut by 10k
    - Never go below min_price
    
    Returns: (price, reason)
    """
    # Go to Buy tab to search market
    click_buy_tab()
    time.sleep(0.3)
    
    # Search for the item
    search_item(item_name)
    time.sleep(1.0)
    
    # Scan current market prices
    prices = full_scan(ocr_threshold=60)
    
    if not prices:
        print(f"[PRICE] No market listings found, using min_price: {min_price}")
        return (min_price, "no listings found")
    
    # Get unit prices and sort (cheapest first)
    market_prices = sorted([p['unit_price'] for p in prices if p.get('unit_price')])
    
    if not market_prices:
        print(f"[PRICE] No valid prices found, using min_price: {min_price}")
        return (min_price, "no valid prices")
    
    # Take avg of 2 cheapest (or just 1 if only one listing)
    if len(market_prices) >= 2:
        avg_price = (market_prices[0] + market_prices[1]) // 2
        print(f"[PRICE] Avg of 2 cheapest: ({market_prices[0]:,} + {market_prices[1]:,}) / 2 = {avg_price:,}")
    else:
        avg_price = market_prices[0]
        print(f"[PRICE] Only 1 listing, using: {avg_price:,}")
    
    # Record price snapshot
    session.record_price_snapshot(item_name, market_prices[0], avg_price)
    
    # Undercut by 10k
    undercut_price = avg_price - 10000
    
    # Clamp to min_price
    if undercut_price < min_price:
        print(f"[PRICE] Would undercut to {undercut_price:,}, but min is {min_price:,}")
        return (min_price, f"clamped to min (avg was {avg_price:,})")
    
    print(f"[PRICE] Undercutting avg ({avg_price:,}) by 10k -> {undercut_price:,}")
    return (undercut_price, f"undercut avg {avg_price:,}")


def refresh_sell_prices(sell_config):
    """
    Refresh market prices for all enabled sell items with auto-pricing.
    This ensures we have up-to-date prices before creating any listings.
    
    Returns dict of {item_name: (price, reason)}
    """
    if not sell_config or not sell_config.get('items'):
        return {}
    
    print("\n--- Refreshing Sell Prices ---")
    refreshed_prices = {}
    
    for item_name, config in sell_config['items'].items():
        if not config.get('enabled'):
            continue
        
        auto_pricing = config.get('autoPricing', True)
        if not auto_pricing:
            # Manual pricing - just use minPrice
            min_price = config.get('minPrice', 0)
            refreshed_prices[item_name] = (min_price, "manual price")
            print(f"[PRICE] {item_name}: {min_price:,} (manual)")
            continue
        
        # Auto pricing - check market
        min_price = config.get('minPrice', 0)
        price, reason = calculate_sell_price(item_name, min_price)
        refreshed_prices[item_name] = (price, reason)
        print(f"[PRICE] {item_name}: {price:,} ({reason})")
        
        time.sleep(0.3)
    
    print(f"[PRICE] Refreshed {len(refreshed_prices)} item prices")
    return refreshed_prices


# ============================================================================
# STALE LISTING HANDLING (placeholders)
# ============================================================================

def record_listing_created(item_name):
    """Record timestamp when a listing is created"""
    global listing_timestamps
    if item_name not in listing_timestamps:
        listing_timestamps[item_name] = []
    listing_timestamps[item_name].append(time.time())
    print(f"[STALE] Recorded listing for {item_name} at {time.strftime('%H:%M:%S')}")


def mark_all_stale(sell_config):
    """
    Mark all enabled sell items as stale so they get checked on first cycle.
    Sets timestamps to 1 hour ago.
    """
    global listing_timestamps
    stale_time = time.time() - 3600  # 1 hour ago
    
    for item_name, config in sell_config.get('items', {}).items():
        if config.get('enabled') and config.get('staleRelist', False):
            listing_timestamps[item_name] = [stale_time]
            print(f"[STALE] Marked {item_name} as stale for initial check")
    
    print(f"[STALE] Initialized {len(listing_timestamps)} items for stale checking")


def init_listing_timestamps(sell_config):
    """
    Initialize listing timestamps to NOW for all enabled stale-relist items.
    This allows normal stale checking to work after threshold time passes.
    """
    global listing_timestamps
    now = time.time()
    
    for item_name, config in sell_config.get('items', {}).items():
        if config.get('enabled') and config.get('staleRelist', False):
            listing_timestamps[item_name] = [now]
    
    print(f"[STALE] Initialized {len(listing_timestamps)} items with current timestamps")


def get_stale_listings(threshold_minutes):
    """
    Check for listings older than threshold_minutes.
    Returns list of (item_name, age_minutes) for stale items.
    """
    global listing_timestamps
    stale = []
    now = time.time()
    threshold_seconds = threshold_minutes * 60
    
    for item_name, timestamps in listing_timestamps.items():
        for ts in timestamps:
            age_seconds = now - ts
            if age_seconds > threshold_seconds:
                age_minutes = age_seconds / 60
                stale.append((item_name, age_minutes))
    
    return stale


# Cancel listing UI coordinates
CANCEL_CONFIRM_BTN = (1108, 744)  # Confirm cancel button


def press_key(key_code):
    """Press and release a key by virtual key code"""
    ctypes.windll.user32.keybd_event(key_code, 0, 0, 0)
    time.sleep(0.05)
    ctypes.windll.user32.keybd_event(key_code, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.1)


def find_listing_position(item_name):
    """
    Find the screen position of a listing for a specific item.
    Returns (x, y) of the listing row, or None if not found.
    """
    import cv2
    import numpy as np
    
    reader = ScreenReader()
    
    # Click sell tab to see my listings
    click_sell_tab()
    time.sleep(0.5)
    
    # Market region for scanning
    market_x, market_y = 125, 406
    market_w, market_h = 2500 - 125, 1400 - 406
    
    if 'gold_price' not in reader.templates:
        print("[CANCEL] No gold_price template!")
        return None
    
    template = reader.templates['gold_price']
    h, w = template.shape[:2]
    
    # Capture and find gold icons
    full_img = reader.capture_region(market_x, market_y, market_w, market_h)
    gray = cv2.cvtColor(full_img, cv2.COLOR_BGR2GRAY)
    result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
    
    locations = []
    loc = np.where(result >= 0.8)
    for pt in zip(*loc[::-1]):
        too_close = False
        for existing in locations:
            if abs(pt[0] - existing[0]) < w and abs(pt[1] - existing[1]) < h:
                too_close = True
                break
        if not too_close:
            locations.append(pt)
    
    locations.sort(key=lambda p: (p[1], p[0]))
    
    if not locations:
        print("[CANCEL] No listings found!")
        return None
    
    # OCR each listing to find the matching item
    normalized_target = normalize_item_name(item_name)
    
    for icon_x, icon_y in locations:
        # Extract name region above the gold icon
        name_x = max(0, icon_x - 30)
        name_y = max(0, icon_y - 85)
        name_w = min(350, full_img.shape[1] - name_x)
        name_h = 35
        
        name_img = full_img[name_y:name_y+name_h, name_x:name_x+name_w]
        if name_img.size == 0:
            continue
        
        # OCR the name
        gray_name = cv2.cvtColor(name_img, cv2.COLOR_BGR2GRAY)
        gray_scaled = cv2.resize(gray_name, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        _, thresh = cv2.threshold(gray_scaled, 60, 255, cv2.THRESH_BINARY)
        
        from PIL import Image
        pil_img = Image.fromarray(thresh)
        try:
            text = pytesseract.image_to_string(pil_img, config='--psm 7').strip()
            text = re.sub(r'[^\w\s\-\'\[\]]', '', text).strip()
            
            if normalize_item_name(text) == normalized_target:
                # Found it! Return absolute screen position
                abs_x = market_x + icon_x + w // 2
                abs_y = market_y + icon_y + h // 2
                print(f"[CANCEL] Found {item_name} at ({abs_x}, {abs_y})")
                return (abs_x, abs_y)
        except:
            continue
    
    print(f"[CANCEL] Could not find listing for {item_name}")
    return None


def cancel_listing(item_name, unit_price=0, quantity=1):
    """
    Cancel a listing for an item.
    Returns True if successfully cancelled, False otherwise.
    Sends a listing_canceled message to server to avoid false sold detection.
    """
    global command_listener
    print(f"[CANCEL] Cancelling listing for {item_name}...")
    
    # Find the listing position
    listing_pos = find_listing_position(item_name)
    if not listing_pos:
        print(f"[CANCEL] Could not find listing for {item_name}")
        return False
    
    # Click on the listing
    click(*listing_pos)
    time.sleep(0.3)
    
    # Press F to open cancel dialog
    VK_F = 0x46  # Virtual key code for 'F'
    press_key(VK_F)
    time.sleep(0.4)
    
    # Click confirm button
    click(*CANCEL_CONFIRM_BTN)
    time.sleep(0.5)
    
    # Notify server that this was canceled (not sold)
    if command_listener and command_listener.ws:
        command_listener._send({
            'type': 'listing_canceled',
            'item': item_name,
            'unit_price': unit_price,
            'quantity': quantity
        })
        print(f"[CANCEL] → Notified server of cancellation")
    
    print(f"[CANCEL] ✓ Cancelled listing for {item_name}")
    return True


def handle_stale_listings(sell_config):
    """
    Check for and handle stale listings.
    Strategy: Cancel and relist ONLY if not already the cheapest.
    Each item has its own staleRelist (enabled/disabled) and staleMinutes settings.
    """
    global listing_timestamps
    
    if not sell_config or not sell_config.get('items'):
        return 0
    
    relisted = 0
    items_checked = 0
    
    for item_name, config in sell_config['items'].items():
        if not config.get('enabled'):
            continue
        
        # Check if stale relist is enabled for this item
        if not config.get('staleRelist', False):
            continue
        
        items_checked += 1
        threshold_minutes = config.get('staleMinutes', 15)
        
        # Check if we have timestamps for this item
        if item_name not in listing_timestamps:
            continue
        
        # Check if any listings are stale
        now = time.time()
        threshold_seconds = threshold_minutes * 60
        stale_found = False
        
        for ts in listing_timestamps.get(item_name, []):
            age_seconds = now - ts
            if age_seconds > threshold_seconds:
                age_minutes = age_seconds / 60
                print(f"[STALE] {item_name}: {age_minutes:.1f} min old (threshold: {threshold_minutes}min)")
                stale_found = True
                break
        
        if not stale_found:
            continue
        
        # Get item config first
        position = config.get('position')
        quantity_per_listing = config.get('quantityPerListing', 1)
        min_price = config.get('minPrice', 0)
        auto_pricing = config.get('autoPricing', True)
        
        if not position:
            print(f"[STALE] No position configured for {item_name}, cannot relist")
            continue
        
        # Calculate new price BEFORE checking/cancelling
        if auto_pricing:
            new_price, reason = calculate_sell_price(item_name, min_price)
            print(f"[STALE] Price for {item_name}: {new_price:,} ({reason})")
            
            # Skip if price is at minimum (can't go lower anyway)
            if new_price == min_price:
                print(f"[STALE] {item_name} already at min price ({min_price:,}), skipping relist")
                listing_timestamps[item_name] = [time.time()]
                continue
        else:
            new_price = min_price  # Use manual price
        
        if new_price <= 0:
            print(f"[STALE] Could not calculate price for {item_name}")
            continue
        
        # Check if we're already the cheapest
        is_cheapest, old_price = check_if_cheapest(item_name)
        
        if is_cheapest:
            print(f"[STALE] {item_name} is already cheapest, skipping relist")
            # Reset timestamp since we don't need to relist
            listing_timestamps[item_name] = [time.time()]
            continue
        
        # Not the cheapest - cancel and relist!
        print(f"[STALE] Cancelling and relisting {item_name}...")
        
        # Cancel the listing (pass price and quantity for server tracking)
        cancelled = cancel_listing(item_name, old_price or 0, quantity_per_listing)
        if not cancelled:
            print(f"[STALE] Failed to cancel listing for {item_name}")
            continue
        
        print(f"[STALE] Relisting {item_name} at {new_price:,} gold")
        
        # Create new listing
        success = sell_item(item_name, position, quantity_per_listing, new_price)
        
        if success:
            # Update timestamp for the new listing
            listing_timestamps[item_name] = [time.time()]
            relisted += 1
            
            # Record the relist
            session.record_relist(item_name, old_price, new_price)
            
            print(f"[STALE] ✓ Successfully relisted {item_name}")
        else:
            print(f"[STALE] Failed to relist {item_name}")
    
    if items_checked == 0:
        return 0
    
    if relisted == 0:
        print(f"[STALE] Checked {items_checked} items, none need relisting")
    
    return relisted


def check_if_cheapest(item_name):
    """
    Check if our listing is the cheapest on the market.
    Returns (is_cheapest, our_price) tuple.
    
    Logic:
    1. Get our listing price from my_listings
    2. Get cheapest market price
    3. If our price <= cheapest, we're good
    """
    normalized_name = normalize_item_name(item_name)
    
    # First, get our current listing price
    click_sell_tab()
    time.sleep(0.3)
    my_listings = scan_my_listings()
    
    our_price = None
    for listing in my_listings:
        if normalize_item_name(listing['name']) == normalized_name:
            our_price = listing.get('price', 0)
            break
    
    if not our_price:
        print(f"[STALE] Couldn't find our listing for {item_name}")
        return (True, None)  # Can't find our listing, skip
    
    print(f"[STALE] Our listing price for {item_name}: {our_price:,}")
    
    # Now check market prices
    click_buy_tab()
    time.sleep(0.3)
    
    search_item(item_name)
    time.sleep(1.0)
    
    prices = full_scan(ocr_threshold=60)
    
    if not prices:
        print(f"[STALE] No market listings found for {item_name}")
        return (True, our_price)  # No competition
    
    market_prices = sorted([p['unit_price'] for p in prices if p.get('unit_price')])
    
    if not market_prices:
        return (True, our_price)
    
    lowest = market_prices[0]
    print(f"[STALE] Cheapest market price for {item_name}: {lowest:,}")
    
    # If our price is <= lowest, we are the cheapest
    if our_price <= lowest:
        print(f"[STALE] We ARE the cheapest ({our_price:,} <= {lowest:,})")
        return (True, our_price)
    else:
        print(f"[STALE] We are NOT cheapest ({our_price:,} > {lowest:,})")
        return (False, our_price)


def clear_sold_listings(current_listings):
    """
    Remove timestamps for items that are no longer listed (i.e., they sold).
    Call this after scanning my listings.
    """
    global listing_timestamps
    
    # Get list of items currently listed
    listed_items = set(normalize_item_name(l['name']) for l in current_listings)
    
    # Remove timestamps for items no longer listed
    for item_name in list(listing_timestamps.keys()):
        normalized = normalize_item_name(item_name)
        if normalized not in listed_items:
            del listing_timestamps[item_name]
            print(f"[STALE] Cleared timestamps for {item_name} (sold)")


def run_sell_phase(listings, sell_config):
    """
    Check current listings vs configured amounts and sell if needed.
    Re-scans listings for each item to ensure accurate counts.
    Respects stock limits - won't sell if out of stock.
    Returns number of items listed.
    """
    if not sell_config or not sell_config.get('items'):
        return 0
    
    print(f"\n--- Sell Phase ---")
    
    # Get sold counts from config (server includes this now)
    sold_counts = sell_config.get('soldCounts', {})
    
    items_listed = 0
    
    for item_name, config in sell_config['items'].items():
        if not config.get('enabled'):
            continue
        
        wanted_listings = config.get('listingsCount', 1)
        quantity_per_listing = config.get('quantityPerListing', 1)
        min_price = config.get('minPrice', 0)
        position = config.get('position', [0, 0])
        auto_pricing = config.get('autoPricing', True)  # Default to auto
        stock = config.get('stock', 10)  # Default stock is 10
        
        # Check remaining stock
        normalized_key = item_name.lower().strip()
        used_stock = sold_counts.get(normalized_key, 0)
        remaining_stock = stock - used_stock
        
        if remaining_stock <= 0:
            print(f"[SELL] Skipping {item_name} - OUT OF STOCK ({used_stock}/{stock})")
            continue
        
        if not position or position == [0, 0]:
            print(f"[SELL] Skipping {item_name} - no position configured")
            continue
        
        if min_price <= 0:
            print(f"[SELL] Skipping {item_name} - no minPrice configured")
            continue
        
        normalized_name = normalize_item_name(item_name)
        
        # Re-scan listings BEFORE each item to get accurate count
        print(f"\n[SELL] Checking listings for {item_name}... (stock: {remaining_stock}/{stock})")
        current_listings = scan_my_listings()
        listing_counts = count_listings_by_item(current_listings)
        current_count = listing_counts.get(normalized_name, 0)
        
        if current_count >= wanted_listings:
            print(f"[SELL] {item_name}: {current_count}/{wanted_listings} listings (OK)")
            continue
        
        needed = wanted_listings - current_count
        
        # Limit by remaining stock (accounting for quantity per listing)
        max_can_list = remaining_stock // quantity_per_listing
        if max_can_list <= 0:
            print(f"[SELL] {item_name}: Not enough stock for even 1 listing ({remaining_stock} < {quantity_per_listing})")
            continue
        
        if needed > max_can_list:
            print(f"[SELL] {item_name}: Need {needed} listings but only enough stock for {max_can_list}")
            needed = max_can_list
        
        print(f"[SELL] {item_name}: {current_count}/{wanted_listings} listings (will create {needed} more)")
        
        # Determine price: auto (undercut market) or fixed (use minPrice directly)
        if auto_pricing:
            price, reason = calculate_sell_price(item_name, min_price)
            print(f"[SELL] Price for {item_name}: {price:,} ({reason})")
        else:
            price = min_price
            print(f"[SELL] Price for {item_name}: {price:,} (fixed price)")
        
        # Create needed listings one at a time, re-checking count after each
        for i in range(needed):
            if not running:
                return items_listed
            
            # Re-scan to verify we still need more (in case of detection issues)
            if i > 0:  # Only re-scan after first listing
                print(f"[SELL] Re-checking listing count for {item_name}...")
                current_listings = scan_my_listings()
                listing_counts = count_listings_by_item(current_listings)
                actual_count = listing_counts.get(normalized_name, 0)
                if actual_count >= wanted_listings:
                    print(f"[SELL] {item_name} now has {actual_count}/{wanted_listings}, stopping")
                    break
            
            print(f"[SELL] Listing {item_name} ({current_count + i + 1}/{wanted_listings}) at {price:,}...")
            
            success = sell_item(item_name, tuple(position), quantity_per_listing, price)
            if success:
                items_listed += 1
                record_listing_created(item_name)  # Track for stale detection
                print(f"[SELL] ✓ Listed!")
            else:
                print(f"[SELL] ✗ Failed to list, stopping")
                break
            
            time.sleep(0.3)
    
    return items_listed


def run_bot():
    """Main bot loop - fetches thresholds from server and buys items below threshold"""
    global running, command_listener
    running = True
    
    print("=" * 50)
    print("Hero Siege Market Bot - RUNNING")
    print("=" * 50)
    print("\\nPress SPACEBAR to stop\\n")
    
    # Start session tracking
    session.start_session()
    
    # Register spacebar to stop
    keyboard.on_press_key('space', lambda _: stop_bot())
    
    # Start the command listener for web UI commands
    command_listener = CommandListener()
    command_listener.start()
    
    # Initialize listing timestamps for stale checking
    sell_config = fetch_sell_config()
    if sell_config and sell_config.get('items'):
        if sell_config.get('checkAllStaleOnStart', False):
            print("[STALE] Check all stale on start is ENABLED - will check prices immediately")
            mark_all_stale(sell_config)
        else:
            print("[STALE] Check all stale on start is DISABLED - normal stale checking after threshold")
            init_listing_timestamps(sell_config)
    
    # Focus game once at start
    print("Focusing game window...")
    focus_game_window("Hero Siege")
    time.sleep(0.3)
    
    cycle = 0
    while running:
        cycle += 1
        session.increment_cycle()
        print(f"\n{'='*50}")
        print(f"CYCLE {cycle}")
        print(f"{'='*50}")
        
        # First, check my listings (what has sold)
        print("\n--- Checking My Listings ---")
        listings = scan_my_listings()
        
        # Send listings to server
        if command_listener.ws:
            command_listener._send({
                'type': 'listings_update',
                'listings': listings
            })
            print(f"📤 Sent {len(listings)} listings to server")
        
        if not running:
            break
        
        # --- Sell Phase (run first) ---
        sell_config = fetch_sell_config()
        if sell_config and sell_config.get('items'):
            # Handle stale listings (cancel and relist if undercut)
            relisted = handle_stale_listings(sell_config)
            if relisted > 0:
                print(f"\n[STALE] Relisted {relisted} stale item(s)")
                # Re-scan and send updated listings to server after relisting
                print("[STALE] Re-scanning listings after relist...")
                updated_listings = scan_my_listings()
                listings = updated_listings  # Update local copy
                if command_listener.ws:
                    command_listener._send({
                        'type': 'listings_update',
                        'listings': updated_listings
                    })
                    print(f"📤 Sent {len(updated_listings)} updated listings to server")
            
            if not running:
                break
            
            # Check if we need to list more items
            items_listed = run_sell_phase(listings, sell_config)
            if items_listed > 0:
                print(f"\n[SELL] Listed {items_listed} item(s)")
                # Re-scan and send updated listings to server
                print("[SELL] Re-scanning listings after sell phase...")
                updated_listings = scan_my_listings()
                if command_listener.ws:
                    command_listener._send({
                        'type': 'listings_update',
                        'listings': updated_listings
                    })
                    print(f"📤 Sent {len(updated_listings)} updated listings to server")
        
        if not running:
            break
        
        # --- Buy Phase ---
        # Go to Buy tab for buying
        click_buy_tab()
        time.sleep(0.3)
        
        # Fetch current thresholds and buy options from server
        thresholds = fetch_buy_thresholds()
        if not thresholds:
            print("[WARN] No thresholds loaded, waiting 5s...")
            time.sleep(5)
            continue
        
        buy_options = fetch_buy_options()
        auto_buy_enabled = buy_options.get('autoBuyEnabled', False)
        buy_items_config = buy_options.get('items', {})
        bought_counts = buy_options.get('boughtCounts', {})
        buy_under_avg_enabled = buy_options.get('buyUnderAvgEnabled', False)
        buy_under_avg_pct = buy_options.get('buyUnderAvgPercent', 10)
        buy_under_avg_max_cycle = buy_options.get('buyUnderAvgMaxPerCycle', 1)
        buy_under_avg_max_session = buy_options.get('buyUnderAvgMaxPerSession', 0)
        buy_under_avg_session_count = buy_options.get('buyUnderAvgSessionCount', 0)
        auto_pricing_enabled = buy_options.get('autoPricingEnabled', False)
        
        # Fetch 1h averages for "buy under avg" logic
        hourly_averages = fetch_hourly_averages()
        
        print(f"Loaded thresholds: {thresholds}")
        print(f"Auto-buy: {'ON' if auto_buy_enabled else 'OFF'}, Buy under avg: {'ON' if buy_under_avg_enabled else 'OFF'} ({buy_under_avg_pct}%), Auto-pricing: {'ON' if auto_pricing_enabled else 'OFF'}")
        
        # Track buys this cycle per item
        cycle_buys = {}
        # Track "buy under avg" buys this cycle (global, not per-item)
        cycle_under_avg_buys = 0
        
        # Loop through each item with a threshold
        for item_name, threshold in thresholds.items():
            if not running:
                break
            
            print(f"\n--- {item_name} (threshold: {threshold:,}) ---")
            
            # Search for item
            search_item(item_name)
            time.sleep(1.0)
            
            if not running:
                break
            
            # Scan prices - DOUBLE READ for OCR verification
            print(f"  [OCR] First read (threshold=60)...")
            prices1 = full_scan(ocr_threshold=60)
            time.sleep(0.3)
            print(f"  [OCR] Second read (threshold=80)...")
            prices2 = full_scan(ocr_threshold=80)
            
            if not prices1 or not prices2:
                print(f"  No prices found for {item_name}")
                continue
            
            # Filter out prices with None unit_price
            valid_prices1 = [p for p in prices1 if p.get('unit_price') is not None]
            valid_prices2 = [p for p in prices2 if p.get('unit_price') is not None]
            if not valid_prices1 or not valid_prices2:
                print(f"  No valid prices found for {item_name}")
                continue
            
            # Find cheapest from both reads (if same price, prefer higher quantity)
            def best_listing(prices):
                min_price = min(p['unit_price'] for p in prices)
                at_min = [p for p in prices if p['unit_price'] == min_price]
                return max(at_min, key=lambda p: p.get('quantity', 1))
            
            cheapest1 = best_listing(valid_prices1)
            cheapest2 = best_listing(valid_prices2)
            
            # Verify both reads match
            if cheapest1['unit_price'] != cheapest2['unit_price']:
                print(f"  [SKIP] OCR mismatch! Read1: {cheapest1['unit_price']:,} vs Read2: {cheapest2['unit_price']:,}")
                continue
            
            cheapest = cheapest1
            print(f"  Cheapest: {cheapest['unit_price']:,} (verified)")
            
            # Record price snapshot for this item (always track)
            session.record_price_snapshot(item_name, cheapest['unit_price'])
            
            # Send all prices to server (for undercut tracking)
            if command_listener.ws:
                all_prices = [{'price': p['unit_price'], 'quantity': p.get('quantity', 1)} for p in valid_prices1 if p.get('unit_price')]
                command_listener._send({
                    'type': 'market_prices',
                    'item': item_name,
                    'prices': all_prices,
                    'lowest': cheapest['unit_price']
                })
            
            # Track whether we bought this cycle (for auto-pricing)
            item_bought_this_cycle = False
            
            # Check if we should auto-buy
            if threshold == 0:
                print(f"  [TRACK] Threshold=0, price tracking only (no buy)")
                # Don't report cycle result for track-only items
                continue
            
            # Get item options for auto-buy and auto-pricing checks
            item_opts = buy_items_config.get(item_name, {})
            
            # Check if price meets buy conditions
            price_under_threshold = cheapest['unit_price'] <= threshold
            
            # Check if price is under 1h average by configured percent (only if feature enabled)
            price_under_avg = False
            can_buy_under_avg = False
            avg_data = hourly_averages.get(item_name, {})
            avg_price = avg_data.get('avg', 0)
            if buy_under_avg_enabled and avg_price > 0:
                target_price = int(avg_price * (100 - buy_under_avg_pct) / 100)
                price_under_avg = cheapest['unit_price'] <= target_price
                can_buy_under_avg = True
                
                # Check "buy under avg" limits
                if buy_under_avg_max_session > 0 and buy_under_avg_session_count >= buy_under_avg_max_session:
                    can_buy_under_avg = False
                    if price_under_avg:
                        print(f"  [AVG LIMIT] Session limit reached ({buy_under_avg_session_count}/{buy_under_avg_max_session})")
                elif cycle_under_avg_buys >= buy_under_avg_max_cycle:
                    can_buy_under_avg = False
                    if price_under_avg:
                        print(f"  [AVG LIMIT] Cycle limit reached ({cycle_under_avg_buys}/{buy_under_avg_max_cycle})")
                elif price_under_avg:
                    print(f"  [AVG] Price {cheapest['unit_price']:,} <= {target_price:,} ({buy_under_avg_pct}% under avg {avg_price:,.0f})")
            
            # Buy if threshold met, OR if under avg and limits not reached
            should_consider_buy = price_under_threshold or (price_under_avg and can_buy_under_avg)
            # Track if this buy is triggered by "under avg" logic only
            buy_reason_is_avg_only = not price_under_threshold and price_under_avg and can_buy_under_avg
            
            if not should_consider_buy:
                print(f"  [SKIP] Price {cheapest['unit_price']:,} > threshold {threshold:,}" + 
                      (f" and > {buy_under_avg_pct}% under avg" if buy_under_avg_enabled and avg_price > 0 else ""))
                # Report as "not bought" for auto-pricing (if both global and per-item enabled)
                if auto_pricing_enabled and item_opts.get('autoPricing', False):
                    report_buy_cycle_result(item_name, False, threshold)
                continue
            
            # Price is good, check auto-buy settings
            if not auto_buy_enabled:
                print(f"  [INFO] Price {cheapest['unit_price']:,} <= threshold {threshold:,} (Auto-buy OFF)")
                continue
            
            # Check per-item auto-buy setting
            if not item_opts.get('autoBuy', False):
                print(f"  [INFO] Price {cheapest['unit_price']:,} <= threshold {threshold:,} (item auto-buy OFF)")
                continue
            
            # Check units needed limit (persistent goal)
            units_needed = item_opts.get('unitsNeeded', 0)
            total_bought = item_opts.get('totalBought', 0)
            if units_needed > 0 and total_bought >= units_needed:
                print(f"  [GOAL] Already have {total_bought}/{units_needed} units (goal reached)")
                continue
            
            # Check max per session
            max_per_session = item_opts.get('maxPerSession', 0)
            item_bought_count = bought_counts.get(item_name.lower(), 0)
            if max_per_session > 0 and item_bought_count >= max_per_session:
                print(f"  [LIMIT] Already bought {item_bought_count}/{max_per_session} this session")
                continue
            
            # Check max per cycle
            max_per_cycle = item_opts.get('maxPerCycle', 1)
            cycle_item_buys = cycle_buys.get(item_name, 0)
            if cycle_item_buys >= max_per_cycle:
                print(f"  [LIMIT] Already bought {cycle_item_buys}/{max_per_cycle} this cycle")
                continue
            
            # All checks passed, buy!
            if 'coords' in cheapest and cheapest['coords']:
                buy_reason = []
                if price_under_threshold:
                    buy_reason.append(f"<= threshold {threshold:,}")
                if price_under_avg and can_buy_under_avg:
                    buy_reason.append(f"<= {buy_under_avg_pct}% under avg {avg_price:,.0f}")
                print(f"  [BUY] Price {cheapest['unit_price']:,} {' / '.join(buy_reason)}")
                double_click(*cheapest['coords'])
                time.sleep(0.3)
                click(1103, 762)  # Confirm button
                time.sleep(0.3)
                print(f"  [BUY] Bought {item_name} for {cheapest['unit_price']:,}!")
                
                item_bought_this_cycle = True
                
                # Track cycle buys
                cycle_buys[item_name] = cycle_item_buys + 1
                
                # Track "buy under avg" cycle buys if this was triggered by avg logic
                if buy_reason_is_avg_only:
                    cycle_under_avg_buys += 1
                    buy_under_avg_session_count += 1
                
                # Record buy to session
                buy_qty = cheapest.get('quantity', 1)
                buy_total = cheapest.get('total', cheapest['unit_price'])
                session.record_buy(item_name, buy_qty, cheapest['unit_price'], buy_total)
                
                # Send buy event to server
                if command_listener.ws:
                    command_listener._send({
                        'type': 'item_bought',
                        'item': item_name,
                        'quantity': cheapest.get('quantity', 1),
                        'unit_price': cheapest['unit_price'],
                        'total': cheapest.get('total', cheapest['unit_price']),
                        'buyUnderAvg': buy_reason_is_avg_only
                    })
                
                # Report as "bought" for auto-pricing (if both global and per-item enabled)
                if auto_pricing_enabled and item_opts.get('autoPricing', False):
                    report_buy_cycle_result(item_name, True, threshold)
            else:
                print(f"  [SKIP] No coords for cheapest listing")
        
        if not running:
            break
        
        # Wait before next cycle
        print(f"\nCycle {cycle} complete. Waiting 3s...")
        time.sleep(3)
    
    # Stop the command listener
    command_listener.stop()
    
    # End session tracking and save stats
    session.end_session()
    
    print("\n🛑 Bot stopped.")


def stop_bot():
    """Stop the bot when spacebar is pressed"""
    global running
    running = False
    print("\n🛑 Spacebar pressed - stopping...")


def test_scan():
    """Test item search and price scanning for all items"""
    bot = BotState()
    
    print("=" * 50)
    print("Hero Siege Market Bot - SEARCH TEST")
    print("=" * 50)
    
    print("\nFocusing game window...")
    focus_game_window("Hero Siege")
    time.sleep(0.3)
    
    bot._set_state(BotState.RUNNING)
    
    for item_name in bot.items:
        print(f"\n--- Searching: {item_name} ---")
        search_item(item_name)
        
        print("Waiting for results to load...")
        time.sleep(1.0)
        
        prices = full_scan()
        print(f"[DEBUG] full_scan returned {len(prices) if prices else 0} prices")
        
        # TEST BUY: Buy cheapest item found
        if prices and len(prices) > 0:
            # Find cheapest by unit_price
            cheapest = min(prices, key=lambda p: p['unit_price'])
            if 'coords' in cheapest and cheapest['coords']:
                print(f"[TEST BUY] Double-clicking cheapest listing at {cheapest['coords']} (unit price: {cheapest['unit_price']:,})")
                double_click(*cheapest['coords'])
                time.sleep(0.3)  # Wait for confirmation popup
                print("[TEST BUY] Clicking confirm button at (1103, 762)")
                click(1103, 762)
                time.sleep(0.2)
                print("[TEST BUY] Confirmed.")
                bot._set_state(BotState.IDLE)
                if bot.ws:
                    bot.ws.close()
                return  # Stop after first buy test
            else:
                print(f"[DEBUG] Cheapest price has no coords: {cheapest}")
        else:
            print(f"[DEBUG] No prices found for {item_name}")
    
    bot._send_status()
    
    print("\n--- All scans complete ---")
    for item, data in bot.results.items():
        print(f"  {item}: lowest = {data['unit_price']:,}")
    
    bot._set_state(BotState.IDLE)
    if bot.ws:
        bot.ws.close()


def full_scan(ocr_threshold=60):
    """Full price scanning - find gold icons and read prices next to them
    
    Args:
        ocr_threshold: Binary threshold value for OCR preprocessing (default: 60)
    """
    print("=" * 50)
    print(f"Hero Siege Market Bot - FULL SCAN (threshold={ocr_threshold})")
    print("=" * 50)
    
    import cv2
    import numpy as np
    
    reader = ScreenReader()
    
    # Capture full market region
    market_x, market_y = 125, 406
    market_w, market_h = 2400 - 125, 1400 - 406
    
    print(f"\nScanning market region...")
    
    full_img = reader.capture_region(market_x, market_y, market_w, market_h)
    
    if 'gold_price' not in reader.templates:
        print("\n⚠️  No gold_price.png template found!")
        return
    
    gray = cv2.cvtColor(full_img, cv2.COLOR_BGR2GRAY)
    template = reader.templates['gold_price']
    
    result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
    threshold = 0.8
    
    # Find all matches above threshold
    locations = []
    h, w = template.shape[:2]
    
    loc = np.where(result >= threshold)
    
    # Group nearby matches (non-maximum suppression)
    for pt in zip(*loc[::-1]):
        too_close = False
        for existing in locations:
            if abs(pt[0] - existing[0]) < w and abs(pt[1] - existing[1]) < h:
                too_close = True
                break
        if not too_close:
            locations.append(pt)
    
    # Sort by y then x (top to bottom, left to right)
    locations.sort(key=lambda p: (p[1], p[0]))
    
    print(f"Found {len(locations)} prices:\n")
    
    prices = []
    
    if not OCR_AVAILABLE or len(locations) == 0:
        return prices
    
    from PIL import Image
    
    # Extract all price regions first for batch OCR (fast)
    # Track which location index each region corresponds to
    price_regions = []
    region_location_indices = []  # Maps region index -> location index
    region_height = 0
    max_width = 0
    
    for loc_idx, (icon_x, icon_y) in enumerate(locations):
        # Start capturing further left to avoid cutting off first digit
        price_x = icon_x + w - 5  # Changed from +5 to -5
        price_y = max(0, icon_y - 5)
        price_w = 210  # Slightly wider
        price_h = h + 10
        
        if price_x + price_w > full_img.shape[1]:
            price_w = full_img.shape[1] - price_x
        
        price_img = full_img[price_y:price_y+price_h, price_x:price_x+price_w]
        if price_img.size > 0:
            gray_price = cv2.cvtColor(price_img, cv2.COLOR_BGR2GRAY)
            
            # Scale up 3x for better OCR accuracy
            gray_scaled = cv2.resize(gray_price, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            
            # Add left padding to ensure first digit isn't cut off
            left_padding = 30
            padded = cv2.copyMakeBorder(gray_scaled, 0, 0, left_padding, 0, cv2.BORDER_CONSTANT, value=0)
            
            # Binary threshold - invert so we get black text on white background (better for OCR)
            _, thresh = cv2.threshold(padded, 60, 255, cv2.THRESH_BINARY_INV)
            
            price_regions.append(thresh)
            region_location_indices.append(loc_idx)
            region_height = thresh.shape[0]
            max_width = max(max_width, thresh.shape[1])
    
    if not price_regions:
        return prices
    
    # PHASE 1: Fast stitched batch OCR
    padding = 40
    total_height = len(price_regions) * (region_height + padding)
    
    stitched = np.zeros((total_height, max_width), dtype=np.uint8)
    for idx, region in enumerate(price_regions):
        y_offset = idx * (region_height + padding)
        stitched[y_offset:y_offset+region.shape[0], 0:region.shape[1]] = region
    
    pil_img = Image.fromarray(stitched)
    data = pytesseract.image_to_data(pil_img, config='--psm 6', output_type=pytesseract.Output.DICT)
    
    # Group words by line
    line_words = {}
    for j in range(len(data['text'])):
        text = data['text'][j].strip()
        if text:
            word_y = data['top'][j]
            region_idx = word_y // (region_height + padding)
            if region_idx not in line_words:
                line_words[region_idx] = []
            line_words[region_idx].append(text)
    
    # Parse results and track which need re-scan
    parsed_results = {}  # region_idx -> (total, quantity, unit_price, needs_rescan)
    
    for region_idx in range(len(price_regions)):
        words = line_words.get(region_idx, [])
        
        total = None
        quantity = 1
        unit_price = None
        needs_rescan = False
        
        # Find first numeric word
        price_word_idx = -1
        for idx, word in enumerate(words):
            parsed = parse_number(word)
            if parsed is not None:
                total = parsed
                unit_price = total
                price_word_idx = idx
                break
        
        # Look for quantity/unit price
        if price_word_idx >= 0 and price_word_idx < len(words) - 1:
            for j, word in enumerate(words[price_word_idx + 1:], price_word_idx + 1):
                word_lower = word.lower()
                if word_lower.startswith('x'):
                    unit_price = parse_number(word_lower[1:])
                    if j > price_word_idx + 1:
                        quantity = parse_number(words[j-1]) or 1
                elif j == price_word_idx + 1 and len(words) >= price_word_idx + 3:
                    quantity = parse_number(word) or 1
        
        if total:
            if unit_price is None:
                unit_price = total
            # Check for mismatch
            expected_total = unit_price * quantity
            if abs(expected_total - total) > total * 0.1:
                needs_rescan = True
        else:
            needs_rescan = True  # No parse = needs rescan
        
        parsed_results[region_idx] = (total, quantity, unit_price, needs_rescan)
    
    # PHASE 2: Re-scan mismatched regions individually
    for region_idx, (total, quantity, unit_price, needs_rescan) in parsed_results.items():
        loc_idx = region_location_indices[region_idx]
        
        if needs_rescan and total is not None:
            # Re-OCR this single region
            region = price_regions[region_idx]
            pil_single = Image.fromarray(region)
            try:
                text = pytesseract.image_to_string(pil_single, config='--psm 7').strip()
                words = text.split()
                
                # Re-parse
                new_total = None
                new_quantity = 1
                new_unit_price = None
                
                price_word_idx = -1
                for idx, word in enumerate(words):
                    parsed = parse_number(word)
                    if parsed is not None:
                        new_total = parsed
                        new_unit_price = new_total
                        price_word_idx = idx
                        break
                
                if price_word_idx >= 0 and price_word_idx < len(words) - 1:
                    for j, word in enumerate(words[price_word_idx + 1:], price_word_idx + 1):
                        word_lower = word.lower()
                        if word_lower.startswith('x'):
                            new_unit_price = parse_number(word_lower[1:])
                            if j > price_word_idx + 1:
                                new_quantity = parse_number(words[j-1]) or 1
                        elif j == price_word_idx + 1 and len(words) >= price_word_idx + 3:
                            new_quantity = parse_number(word) or 1
                
                if new_total:
                    if new_unit_price is None:
                        new_unit_price = new_total
                    # Check if rescan fixed it
                    expected = new_unit_price * new_quantity
                    if abs(expected - new_total) <= new_total * 0.1:
                        total, quantity, unit_price = new_total, new_quantity, new_unit_price
                        print(f"  [{loc_idx+1:02d}] ✓ Rescan fixed: {total:,}")
                    else:
                        # Still bad, use corrected calculation
                        unit_price = new_total // new_quantity
                        total, quantity = new_total, new_quantity
                        print(f"  [{loc_idx+1:02d}] ⚠️ Rescan still mismatched, using {unit_price:,}")
            except:
                pass
        
        # Final sanity check and correction
        if total:
            if unit_price is None:
                unit_price = total
            expected_total = unit_price * quantity
            if abs(expected_total - total) > total * 0.1:
                corrected_unit = total // quantity
                print(f"  [{loc_idx+1:02d}] ⚠️ Final correction: {unit_price}*{quantity}!={total}, using {corrected_unit}")
                unit_price = corrected_unit
            
            icon_x, icon_y = locations[loc_idx]
            screen_x = market_x + icon_x + w // 2
            screen_y = market_y + icon_y + h // 2 - 50
            
            price_data = {
                'total': total,
                'quantity': quantity,
                'unit_price': unit_price,
                'coords': (screen_x, screen_y)
            }
            prices.append(price_data)
            if quantity > 1:
                print(f"  [{loc_idx+1:02d}] Total: {total:,} | {quantity}x {unit_price:,} @ ({screen_x},{screen_y})")
            else:
                print(f"  [{loc_idx+1:02d}] {total:,} @ ({screen_x},{screen_y})")
        else:
            print(f"  [{loc_idx+1:02d}] (parse failed)")
    
    if prices:
        # Find lowest by unit price (filter out any with None unit_price)
        valid_prices = [p for p in prices if p['unit_price'] is not None]
        if valid_prices:
            lowest = min(valid_prices, key=lambda p: p['unit_price'])
            print(f"\n💰 Lowest unit price: {lowest['unit_price']:,} ({lowest['quantity']}x = {lowest['total']:,} total)")
            print(f"📊 Total listings: {len(prices)}")
    
    return prices


# Tab coordinates for market navigation
BUY_TAB = (234, 247)   # Buy section (normal start)
SELL_TAB = (440, 247)  # Sell section (my listings)


def click_buy_tab():
    """Click the Buy tab to go to buying section"""
    print(f"Clicking Buy tab at {BUY_TAB}...")
    click(*BUY_TAB)
    time.sleep(0.3)


def click_sell_tab():
    """Click the Sell tab to go to my listings"""
    print(f"Clicking Sell tab at {SELL_TAB}...")
    click(*SELL_TAB)
    time.sleep(0.3)


def scan_my_listings():
    """Scan the 'My Listings' tab to get items currently being sold, with scroll support"""
    print("=" * 50)
    print("SCANNING MY LISTINGS")
    print("=" * 50)
    
    import cv2
    import numpy as np
    from PIL import Image
    import os
    
    reader = ScreenReader()
    
    # Click on Sell tab to see my listings
    click_sell_tab()
    time.sleep(0.5)  # Wait for tab to load
    
    # Market region for scanning listings - extend right to capture scroll bar
    market_x, market_y = 125, 406
    market_w, market_h = 2500 - 125, 1400 - 406  # Extended right edge to 2500
    
    # Scroll position (center of market area)
    scroll_x = market_x + market_w // 2
    scroll_y = market_y + market_h // 2
    
    debug_dir = os.path.join(os.path.dirname(__file__), 'debug')
    os.makedirs(debug_dir, exist_ok=True)
    
    if 'gold_price' not in reader.templates:
        print("\n⚠️  No gold_price.png template found!")
        return []
    
    template = reader.templates['gold_price']
    h, w = template.shape[:2]
    
    all_listings = []
    scroll_count = 0
    max_scrolls = 20  # Safety limit
    
    def has_scroll_bar(img):
        """Check if yellow scroll bar is visible on the right side"""
        # Check scroll bar area (shifted right) - wider strip
        right_strip = img[:, -100:-50]
        
        # Convert to HSV to detect yellow/gold color
        hsv = cv2.cvtColor(right_strip, cv2.COLOR_BGR2HSV)
        
        # Yellow/gold color range in HSV - extra widened range for better detection
        lower_yellow = np.array([10, 50, 50])
        upper_yellow = np.array([50, 255, 255])
        
        mask = cv2.inRange(hsv, lower_yellow, upper_yellow)
        yellow_pixels = np.sum(mask > 0)
        
        # If more than 30 yellow pixels, scroll bar exists (lowered threshold)
        has_bar = yellow_pixels > 30
        print(f"  Yellow pixels in scroll area: {yellow_pixels} {'(scroll bar present)' if has_bar else '(no scroll bar)'}")
        return has_bar
    
    def is_at_bottom(img, prev_img):
        """Check if we've reached bottom by comparing scroll bar area only"""
        if prev_img is None:
            return False
        
        # Only compare the scroll bar area (85-60px from right edge)
        curr_strip = img[:, -85:-60]
        prev_strip = prev_img[:, -85:-60]
        
        gray_curr = cv2.cvtColor(curr_strip, cv2.COLOR_BGR2GRAY)
        gray_prev = cv2.cvtColor(prev_strip, cv2.COLOR_BGR2GRAY)
        
        diff = cv2.absdiff(gray_curr, gray_prev)
        diff_sum = np.sum(diff)
        similarity = 1.0 - (diff_sum / (gray_curr.size * 255))
        
        print(f"  Scroll bar similarity: {similarity:.2%}")
        return similarity > 0.98
    
    prev_img = None
    
    while scroll_count <= max_scrolls:
        print(f"\n--- Scan pass {scroll_count + 1} ---")
        
        # Capture market region
        full_img = reader.capture_region(market_x, market_y, market_w, market_h)
        
        # Check if we've reached the bottom (no change after scroll)
        if prev_img is not None and is_at_bottom(full_img, prev_img):
            print("✅ Reached bottom, done scanning")
            break
        
        # Find gold icons
        gray = cv2.cvtColor(full_img, cv2.COLOR_BGR2GRAY)
        result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
        
        locations = []
        loc = np.where(result >= 0.8)
        
        for pt in zip(*loc[::-1]):
            too_close = False
            for existing in locations:
                if abs(pt[0] - existing[0]) < w and abs(pt[1] - existing[1]) < h:
                    too_close = True
                    break
            if not too_close:
                locations.append(pt)
        
        locations.sort(key=lambda p: (p[1], p[0]))
        
        print(f"Found {len(locations)} gold icons")
        
        if not locations:
            break
        
        # On first pass, scan ALL visible items
        # On subsequent passes (after scroll), only scan bottom row (last 3 items)
        if scroll_count == 0:
            items_to_scan = locations
            print(f"First pass: scanning all {len(items_to_scan)} items")
        else:
            # Get items in the bottom row (highest y values, typically last 3)
            if len(locations) >= 3:
                # Sort by y descending to get bottom row
                sorted_by_y = sorted(locations, key=lambda p: p[1], reverse=True)
                bottom_y = sorted_by_y[0][1]
                # Get all items within ~20px of bottom row
                items_to_scan = [loc for loc in locations if abs(loc[1] - bottom_y) < 20]
                print(f"Scroll pass: scanning {len(items_to_scan)} new items (bottom row)")
            else:
                items_to_scan = locations
        
        # OCR the items
        new_listings = _ocr_listings(full_img, items_to_scan, w, h, len(all_listings))
        all_listings.extend(new_listings)
        
        # Check if scroll bar exists - if not, no more items
        if not has_scroll_bar(full_img):
            print("✅ No scroll bar, done scanning")
            break
        
        # Save current image and scroll
        prev_img = full_img.copy()
        
        print("Scrolling down...")
        scroll_down(scroll_x, scroll_y, clicks=3)  # 3 clicks = 3 rows
        time.sleep(0.4)  # Wait for scroll animation
        
        scroll_count += 1
    
    # Save final debug image
    full_img = reader.capture_region(market_x, market_y, market_w, market_h)
    debug_path = os.path.join(debug_dir, 'sell_tab_full.png')
    cv2.imwrite(debug_path, full_img)
    
    print(f"\n{'='*50}")
    print(f"📦 TOTAL: {len(all_listings)} listings found")
    print("=" * 50)
    for idx, listing in enumerate(all_listings):
        print(f"  [{idx+1:02d}] {listing['name']}: {listing['price']:,}")
    
    return all_listings


def _ocr_listings(full_img, locations, w, h, start_index=0):
    """Helper function to OCR a list of listing locations"""
    import cv2
    import numpy as np
    from PIL import Image
    
    listings = []
    
    if not OCR_AVAILABLE or len(locations) == 0:
        return listings
    
    # BATCH OCR: Extract all regions first
    name_regions = []
    price_regions = []
    
    name_height = 0
    name_max_width = 0
    price_height = 0
    price_max_width = 0
    
    for icon_x, icon_y in locations:
        # Item name region - ABOVE the gold icon
        name_x = max(0, icon_x - 30)
        name_y = max(0, icon_y - 85)
        name_w = min(350, full_img.shape[1] - name_x)
        name_h = 35
        
        # Price region - to the right of gold icon
        price_x = icon_x + w + 5
        price_y = max(0, icon_y - 5)
        price_w = min(200, full_img.shape[1] - price_x)
        price_h = h + 10
        
        # Extract name region
        name_img = full_img[name_y:name_y+name_h, name_x:name_x+name_w]
        if name_img.size > 0:
            gray_name = cv2.cvtColor(name_img, cv2.COLOR_BGR2GRAY)
            gray_scaled = cv2.resize(gray_name, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            _, thresh = cv2.threshold(gray_scaled, 60, 255, cv2.THRESH_BINARY)
            name_regions.append(thresh)
            name_height = thresh.shape[0]
            name_max_width = max(name_max_width, thresh.shape[1])
        else:
            name_regions.append(None)
        
        # Extract price region
        price_img = full_img[price_y:price_y+price_h, price_x:price_x+price_w]
        if price_img.size > 0:
            gray_price = cv2.cvtColor(price_img, cv2.COLOR_BGR2GRAY)
            gray_scaled = cv2.resize(gray_price, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            _, thresh = cv2.threshold(gray_scaled, 60, 255, cv2.THRESH_BINARY)
            price_regions.append(thresh)
            price_height = thresh.shape[0]
            price_max_width = max(price_max_width, thresh.shape[1])
        else:
            price_regions.append(None)
    
    # Batch OCR for names
    padding = 20
    names = [""] * len(locations)
    
    if name_regions and name_height > 0:
        total_height = len(name_regions) * (name_height + padding)
        stitched_names = np.zeros((total_height, name_max_width), dtype=np.uint8)
        
        for idx, region in enumerate(name_regions):
            if region is not None:
                y_offset = idx * (name_height + padding)
                stitched_names[y_offset:y_offset+region.shape[0], 0:region.shape[1]] = region
        
        pil_names = Image.fromarray(stitched_names)
        try:
            data = pytesseract.image_to_data(pil_names, config='--psm 6', output_type=pytesseract.Output.DICT)
            
            line_words = {}
            for j in range(len(data['text'])):
                text = data['text'][j].strip()
                if text:
                    word_y = data['top'][j]
                    region_idx = word_y // (name_height + padding)
                    if region_idx not in line_words:
                        line_words[region_idx] = []
                    line_words[region_idx].append(text)
            
            for idx in range(len(locations)):
                words = line_words.get(idx, [])
                if words:
                    name = ' '.join(words)
                    names[idx] = re.sub(r'[^\w\s\-\'\[\]]', '', name).strip()
        except Exception as e:
            print(f"❌ Name OCR error: {e}")
    
    # Batch OCR for prices - now captures total, quantity, and unit_price like full_scan
    prices_list = [{'total': None, 'quantity': 1, 'unit_price': None} for _ in range(len(locations))]
    
    if price_regions and price_height > 0:
        total_height = len(price_regions) * (price_height + padding)
        stitched_prices = np.zeros((total_height, price_max_width), dtype=np.uint8)
        
        for idx, region in enumerate(price_regions):
            if region is not None:
                y_offset = idx * (price_height + padding)
                stitched_prices[y_offset:y_offset+region.shape[0], 0:region.shape[1]] = region
        
        pil_prices = Image.fromarray(stitched_prices)
        try:
            data = pytesseract.image_to_data(pil_prices, config='--psm 6', output_type=pytesseract.Output.DICT)
            
            line_words = {}
            for j in range(len(data['text'])):
                text = data['text'][j].strip()
                if text:
                    word_y = data['top'][j]
                    region_idx = word_y // (price_height + padding)
                    if region_idx not in line_words:
                        line_words[region_idx] = []
                    line_words[region_idx].append(text)
            
            for idx in range(len(locations)):
                words = line_words.get(idx, [])
                if not words:
                    continue
                
                # Parse like full_scan: find first numeric word, then look for quantity/unit
                total = None
                quantity = 1
                unit_price = None
                price_word_idx = -1
                
                for widx, word in enumerate(words):
                    parsed = parse_number(word)
                    if parsed is not None:
                        total = parsed
                        unit_price = total
                        price_word_idx = widx
                        break
                
                # Look for quantity pattern (e.g., "2x 50,000")
                if price_word_idx >= 0:
                    for j, word in enumerate(words[price_word_idx + 1:], price_word_idx + 1):
                        word_lower = word.lower()
                        if word_lower.startswith('x') and len(word_lower) > 1:
                            unit_price = parse_number(word_lower[1:])
                            if j > price_word_idx + 1:
                                quantity = parse_number(words[j-1]) or 1
                
                prices_list[idx] = {'total': total, 'quantity': quantity, 'unit_price': unit_price or total}
        except Exception as e:
            print(f"❌ Price OCR error: {e}")
    
    # Build listings
    for idx in range(len(locations)):
        name = names[idx] or "Unknown"
        price_data = prices_list[idx]
        
        listing = {
            'name': name,
            'price': price_data['total'] or 0,  # Total price
            'quantity': price_data['quantity'],
            'unit_price': price_data['unit_price'] or price_data['total'] or 0,  # Unit price for comparison
            'index': start_index + idx
        }
        listings.append(listing)
    
    return listings


def try_buy_item(prices, threshold):
    """Click the first listing with unit_price <= threshold. Returns True if bought."""
    for p in prices:
        if p['unit_price'] is not None and p['coords'] and p['unit_price'] <= threshold:
            print(f"[BUY] Double-clicking listing at {p['coords']} for {p['unit_price']:,} (<= {threshold:,})")
            double_click(*p['coords'])
            time.sleep(0.3)  # Wait for confirmation popup
            print("[BUY] Clicking confirm button at (1103, 762)")
            click(1103, 762)
            time.sleep(0.2)
            return True
    return False


def test():
    """Test: Go to sell tab and scan all listed items"""
    print("=" * 50)
    print("Hero Siege Market Bot - TEST MODE")
    print("=" * 50)
    print("Testing: Scan My Listings")
    print("=" * 50)
    
    # Focus the game first
    print("\nFocusing game...")
    focus_game_window("Hero Siege")
    time.sleep(0.3)
    
    # Go to sell tab and scan listings
    listings = scan_my_listings()
    
    print(f"\n{'='*50}")
    print(f"RESULTS: Found {len(listings)} listings")
    print("=" * 50)
    for item in listings:
        print(f"  - {item['name']}: {item['price']:,}")
    
    # Send to server
    print("\nConnecting to server...")
    listener = CommandListener()
    if listener._connect():
        listener._send({
            'type': 'listings_update',
            'listings': listings
        })
        print(f"📤 Sent {len(listings)} listings to server")
    else:
        print("⚠️  Could not connect to server")
    
    print("\nDone!")


def on_spacebar():
    """Stop the script when spacebar is pressed"""
    global running
    running = False
    print("\n🛑 Spacebar pressed - stopping...")


class CommandListener:
    """WebSocket listener for commands from the web UI"""
    
    def __init__(self, server_url="ws://localhost:8080/ws"):
        self.server_url = server_url
        self.ws = None
        self.running = False
        self.thread = None
    
    def start(self):
        """Start listening for commands in a background thread"""
        if not WS_AVAILABLE:
            print("⚠️  WebSocket not available, command listener disabled")
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._listen_loop, daemon=True)
        self.thread.start()
        print("📡 Command listener started")
    
    def stop(self):
        """Stop the command listener"""
        self.running = False
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
    
    def _connect(self):
        """Connect to the WebSocket server"""
        try:
            self.ws = websocket.create_connection(
                self.server_url,
                header={"X-Client-Type": "python"},
                timeout=5
            )
            print("✅ Command listener connected to server")
            return True
        except Exception as e:
            print(f"⚠️  Command listener could not connect: {e}")
            self.ws = None
            return False
    
    def _send(self, data):
        """Send a JSON message to the server"""
        if self.ws:
            try:
                self.ws.send(json.dumps(data))
                return True
            except Exception as e:
                print(f"⚠️  Failed to send: {e}")
                self.ws = None
                return False
        return False
    
    def _listen_loop(self):
        """Main loop that listens for commands"""
        while self.running:
            # Connect if not connected
            if not self.ws:
                if not self._connect():
                    time.sleep(5)  # Wait before retry
                    continue
            
            try:
                # Set a timeout so we can check self.running periodically
                self.ws.settimeout(1.0)
                data = self.ws.recv()
                
                if data:
                    message = json.loads(data)
                    self._handle_message(message)
                    
            except websocket.WebSocketTimeoutException:
                # Timeout is normal, just continue loop
                continue
            except websocket.WebSocketConnectionClosedException:
                print("⚠️  Connection closed, reconnecting...")
                self.ws = None
                time.sleep(2)
            except Exception as e:
                print(f"⚠️  Listener error: {e}")
                self.ws = None
                time.sleep(2)
    
    def _handle_message(self, message):
        """Handle an incoming message from the server"""
        msg_type = message.get('type')
        
        if msg_type == 'command':
            command = message.get('command')
            print(f"\n📥 Received command: {command}")
            
            if command == 'scan_listings':
                self._handle_scan_listings()
            else:
                print(f"❓ Unknown command: {command}")
    
    def _handle_scan_listings(self):
        """Handle the scan_listings command"""
        print("\n🔍 Scanning my listings...")
        
        # Focus game window
        focus_game_window("Hero Siege")
        time.sleep(0.3)
        
        # Scan listings
        listings = scan_my_listings()
        
        # Send results to server
        self._send({
            'type': 'listings_update',
            'listings': listings
        })
        
        print(f"📤 Sent {len(listings)} listings to server")


class BotState:
    """State machine for the market bot"""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    
    def __init__(self, server_url="ws://localhost:8080/ws"):
        self.state = self.IDLE
        self.reader = ScreenReader()
        self.items = ["jadenium powder", "dark matter"]  # Items to scan
        self.results = {}  # item_name -> price data
        self.ws = None
        self.server_url = server_url
        self._connect()
        self._setup_hotkeys()
    
    def _connect(self):
        """Connect to the server via WebSocket"""
        if not WS_AVAILABLE:
            print("⚠️  WebSocket not available, running offline")
            return
        try:
            self.ws = websocket.create_connection(
                self.server_url,
                header={"X-Client-Type": "python"}
            )
            print("✅ Connected to server")
            self._send_status()
        except Exception as e:
            print(f"⚠️  Could not connect to server: {e}")
            self.ws = None
    
    def _send(self, data):
        """Send a JSON message to the server"""
        if self.ws:
            try:
                self.ws.send(json.dumps(data))
            except Exception:
                self.ws = None
    
    def _send_status(self):
        """Send current state to server"""
        self._send({
            "type": "status",
            "state": self.state,
            "stats": {
                "items": self.items,
                "results": {k: v for k, v in self.results.items() if v}
            }
        })
    
    def _set_state(self, new_state):
        """Change state and notify server"""
        self.state = new_state
        self._send_status()
    
    def _setup_hotkeys(self):
        keyboard.on_press_key('F5', lambda _: self.start())
        keyboard.on_press_key('F6', lambda _: self.pause())
        keyboard.on_press_key('F7', lambda _: self.stop())
        
    def start(self):
        if self.state == self.PAUSED:
            print("\n▶️  Resuming - restarting from beginning...")
            self._set_state(self.RUNNING)
        elif self.state in (self.IDLE, self.STOPPED):
            print("\n▶️  Starting bot...")
            self._set_state(self.RUNNING)
        
    def pause(self):
        if self.state == self.RUNNING:
            print("\n⏸️  Paused - press F5 to resume")
            self._set_state(self.PAUSED)
    
    def stop(self):
        print("\n⏹️  Stopping bot...")
        self._set_state(self.STOPPED)
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
    
    def is_running(self):
        return self.state == self.RUNNING
    
    def is_paused(self):
        return self.state == self.PAUSED
    
    def is_stopped(self):
        return self.state == self.STOPPED
    
    def _send_price(self, item_name, prices):
        """Send price data for an item to the server"""
        if not prices:
            return
        lowest = min(prices, key=lambda p: p['unit_price'])
        self._send({
            "type": "price_update",
            "data": {
                "item_name": item_name,
                "price": lowest['unit_price'],
                "lowest": lowest,
                "all_prices": prices,
                "timestamp": time.time()
            }
        })
    
    def run_cycle(self):
        """Run one full scan cycle for all items"""
        self.results = {}
        
        for item in self.items:
            if not self.is_running():
                return
            
            print(f"\n--- Searching: {item} ---")
            search_item(item)
            
            if not self.is_running():
                return
            
            time.sleep(1.0)
            prices = full_scan()
            
            if prices:
                lowest = min(prices, key=lambda p: p['unit_price'])
                self.results[item] = lowest
                self._send_price(item, prices)
        
        self._send_status()
        
        print("\n--- Cycle complete ---")
        for item, data in self.results.items():
            if data:
                print(f"  {item}: lowest = {data['unit_price']:,}")
    
    def run(self):
        """Main bot loop"""
        print("=" * 50)
        print("Hero Siege Market Bot")
        print("=" * 50)
        print("\nHotkeys:")
        print("  F5 = Start / Resume")
        print("  F6 = Pause")
        print("  F7 = Stop & Exit")
        print("\nWaiting to start (press F5)...\n")
        
        while not self.is_stopped():
            if self.is_running():
                focus_game_window("Hero Siege")
                time.sleep(0.3)
                
                if not self.reader.is_market_open():
                    print("❌ Market not open, waiting...")
                    time.sleep(2)
                    continue
                
                self.run_cycle()
                
                if self.is_running():
                    print("\nWaiting 5s before next cycle...")
                    for _ in range(50):
                        if not self.is_running():
                            break
                        time.sleep(0.1)
            else:
                time.sleep(0.1)
        
        print("\nBot stopped.")


def main():
    """Simple market detection"""
    global running
    
    print("=" * 50)
    print("Hero Siege Market Detector")
    print("=" * 50)
    
    # Focus the game first
    print("\nFocusing game...")
    focus_game()
    
    reader = ScreenReader()
    
    # Check if template exists
    if 'market_top' not in reader.templates:
        print("\n⚠️  No market_top.png template found!")
        print("\nTo create the template:")
        print("  1. Open the game to the market/auction house")
        print("  2. Run: python screen_reader.py")
        print("  3. Take a screenshot (option 1)")
        print("  4. Find the market header region coordinates")
        print("  5. Create template with those coords (option 2)")
        return
    
    # Register spacebar to stop
    keyboard.on_press_key('space', lambda _: on_spacebar())
    
    print("\nTemplate loaded. Checking if market is open...")
    print("Press SPACEBAR to stop.\n")
    
    while running:
        is_open = reader.is_market_open()
        
        if is_open:
            print("✅ Market is OPEN")
            search_item("jadenium powder")
        else:
            print("❌ Market is CLOSED")
        
        time.sleep(1)
    
    print("Stopped.")


def collect_money_mode():
    """
    Collect money mode - iterates through listings and collects gold.
    Sequence: Click list item -> Click collect -> Close popup -> Scroll down -> Repeat
    """
    global running
    running = True
    
    # Coordinates
    LIST_ITEM_BTN = (1246, 486)
    COLLECT_BTN = (1167, 991)
    CLOSE_BTN = (1638, 398)
    SCROLL_CENTER = (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)
    
    print("=" * 50)
    print("Hero Siege Market Bot - COLLECT MONEY MODE")
    print("=" * 50)
    print("\nPress SPACEBAR to stop\n")
    print(f"List Item: {LIST_ITEM_BTN}")
    print(f"Collect: {COLLECT_BTN}")
    print(f"Close: {CLOSE_BTN}")
    print(f"Scroll at: {SCROLL_CENTER}")
    print("=" * 50)
    
    # Register spacebar to stop
    keyboard.on_press_key('space', lambda _: stop_bot())
    
    # Focus game
    print("\nFocusing game window...")
    focus_game_window("Hero Siege")
    time.sleep(0.5)
    
    cycle = 0
    while running:
        cycle += 1
        print(f"\n--- Collect Cycle {cycle} ---")
        
        # 1. Click list item button
        print(f"[1] Clicking list item at {LIST_ITEM_BTN}")
        click(*LIST_ITEM_BTN)
        time.sleep(0.3)
        
        if not running:
            break
        
        # 2. Click collect button
        print(f"[2] Clicking collect at {COLLECT_BTN}")
        click(*COLLECT_BTN)
        time.sleep(0.3)
        
        if not running:
            break
        
        # 3. Close popup
        print(f"[3] Closing popup at {CLOSE_BTN}")
        click(*CLOSE_BTN)
        time.sleep(0.2)
        
        if not running:
            break
        
        # 4. Scroll down once
        print(f"[4] Scrolling down at {SCROLL_CENTER}")
        scroll_down(*SCROLL_CENTER, clicks=1)
        time.sleep(0.2)
    
    print("\n" + "=" * 50)
    print("Collect money mode stopped.")
    print("=" * 50)


def scan_stock_mode():
    """
    Scan stock mode - reads item quantities from the screen and updates the server.
    Inventory is a 2x6 grid. Each cell is OCR'd individually.
    Left col (X≈1545), Right col (X≈1585), 6 rows from Y=800 to Y=1184.
    """
    import cv2
    import numpy as np
    from PIL import Image
    import os

    if not OCR_AVAILABLE:
        print("❌ OCR not available - install pytesseract")
        return

    print("=" * 50)
    print("Hero Siege Market Bot - SCAN STOCK MODE")
    print("=" * 50)

    sell_config = fetch_sell_config()
    if not sell_config or not sell_config.get('items'):
        print("❌ No sell config found or no items configured")
        return

    items = sell_config['items']
    print(f"\nFound {len(items)} items in sell config")

    print("\nFocusing game window...")
    focus_game_window("Hero Siege")
    time.sleep(0.3)

    reader = ScreenReader()
    debug_dir = os.path.join(os.path.dirname(__file__), 'debug')
    os.makedirs(debug_dir, exist_ok=True)

    # Scan region — shifted right vs before to fully capture right column
    SCAN_X1, SCAN_Y1 = 1520, 800
    SCAN_X2, SCAN_Y2 = 1660, 1184
    SCAN_WIDTH  = SCAN_X2 - SCAN_X1   # 140
    SCAN_HEIGHT = SCAN_Y2 - SCAN_Y1   # 384

    # Grid layout
    GRID_COLS = 2
    GRID_ROWS = 6
    CELL_W = SCAN_WIDTH  // GRID_COLS   # 70
    CELL_H = SCAN_HEIGHT // GRID_ROWS   # 64

    # Column split: items with X <= this go to col 0, others col 1
    COL_SPLIT_X = 1565

    print(f"\nCapturing scan region ({SCAN_X1},{SCAN_Y1}) to ({SCAN_X2},{SCAN_Y2})...")
    full_img = reader.capture_region(SCAN_X1, SCAN_Y1, SCAN_WIDTH, SCAN_HEIGHT)

    if full_img is None or full_img.size == 0:
        print("❌ Could not capture scan region")
        return

    # Save full debug image
    cv2.imwrite(os.path.join(debug_dir, "stock_scan_full.png"), full_img)
    print(f"Saved full debug image to client/debug/stock_scan_full.png")

    # Draw grid lines on debug copy
    debug_grid = full_img.copy()
    for c in range(1, GRID_COLS):
        cv2.line(debug_grid, (c * CELL_W, 0), (c * CELL_W, SCAN_HEIGHT), (0, 255, 0), 1)
    for r in range(1, GRID_ROWS):
        cv2.line(debug_grid, (0, r * CELL_H), (SCAN_WIDTH, r * CELL_H), (0, 255, 0), 1)
    cv2.imwrite(os.path.join(debug_dir, "stock_scan_grid.png"), debug_grid)

    def ocr_cell(cell_img, label="", col_idx=0):
        """OCR only the bottom number strip of a grid cell, filtering out icon blobs."""
        h, w = cell_img.shape[:2]
        ny = int(h * 0.70)
        # Col 1 items (Chaos Gem): number sits near left edge of cell, no x-crop needed.
        # Col 0 items: crop left 40% to avoid light icon bleed (Moonstone crystal, etc.)
        nx = 0 if col_idx == 1 else int(w * 0.40)
        num_region = cell_img[ny:h, nx:w]

        safe = label.replace(' ', '_').replace("'", "")
        cv2.imwrite(os.path.join(debug_dir, f"stock_num_{safe}.png"), num_region)

        # Isolate bright white pixels — pure game text has S≈0; tinted icons (blue crystal
        # etc.) have S>20 and are excluded here, before any other processing.
        hsv = cv2.cvtColor(num_region, cv2.COLOR_BGR2HSV)
        white_mask = cv2.inRange(hsv, np.array([0, 0, 190]), np.array([180, 20, 255]))

        # ---- Row-gap detection: physically remove icon bleed above the number ----
        # Scan rows from bottom up. Numbers sit at the very bottom; icon bleeds in
        # from the top. The UI always has at least 2 dark rows between icon and text.
        # Find that gap and zero out everything above it.
        row_sums = np.sum(white_mask, axis=1) / 255.0
        strip_h_px = white_mask.shape[0]
        num_top_row = 0      # topmost row of the number zone (rows below this are kept)
        in_number = False
        consecutive_empty = 0
        for r in range(strip_h_px - 1, -1, -1):
            if row_sums[r] >= 1.0:          # row has at least one white pixel → digit row
                in_number = True
                num_top_row = r
                consecutive_empty = 0
            elif in_number:                 # blank row after being in number zone
                consecutive_empty += 1
                if consecutive_empty >= 2:  # 2+ consecutive blank rows = real gap above number
                    break
        if num_top_row > 0:
            white_mask[:num_top_row, :] = 0  # zero out icon-bleed rows above the number

        # Scale up
        white_mask = cv2.resize(white_mask, None, fx=6, fy=6, interpolation=cv2.INTER_NEAREST)
        scaled_h = white_mask.shape[0]
        min_digit_span = int(scaled_h * 0.40)  # blob must span ≥40% of strip height

        # Connected component filtering:
        # - Remove noise (< 40px²) and large icon blobs (> 10000px²)
        # - "Top to bottom" check: blobs that don't span ≥40% of strip height are
        #   not digits — they are corner fragments, horizontal dashes, etc.
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(white_mask, connectivity=8)
        filtered = np.zeros_like(white_mask)
        for i in range(1, num_labels):
            area = stats[i, cv2.CC_STAT_AREA]
            height_span = stats[i, cv2.CC_STAT_HEIGHT]
            if 40 <= area <= 10000 and height_span >= min_digit_span:
                filtered[labels == i] = 255

        # Light dilation to reconnect stroke gaps
        kernel = np.ones((2, 2), np.uint8)
        filtered = cv2.dilate(filtered, kernel, iterations=1)
        filtered = cv2.copyMakeBorder(filtered, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=0)

        # Invert to black text on white background (better Tesseract accuracy on game fonts)
        inverted = cv2.bitwise_not(filtered)
        cv2.imwrite(os.path.join(debug_dir, f"stock_proc_{safe}.png"), inverted)

        pil_img = Image.fromarray(inverted)
        # No whitelist — game's stylised font (1→"i", 5→"p", etc.) fails digit-only mode.
        base_cfg = '--oem 3 -c load_system_dawg=0 -c load_freq_dawg=0'
        # Map common game-font OCR confusions to their actual digit
        char_map = {
            'i': '1', 'l': '1', 'I': '1', '|': '1',
            'p': '5', 's': '5', 'S': '5',
            'o': '0', 'O': '0', 'Q': '0',
            'b': '6', 'q': '9', 'g': '9',
            'z': '2', 'Z': '2',
        }
        digits = ''
        text = ''
        psm = 7
        for psm in (7, 6, 13, 11):
            text = pytesseract.image_to_string(pil_img, config=f'{base_cfg} --psm {psm}').strip()
            # Apply substitution for known game-font misreadings, then keep only digits
            mapped = ''.join(char_map.get(c, c) for c in text)
            digits = ''.join(c for c in mapped if c.isdigit())
            if digits:
                break
        result = int(digits) if digits else 0
        print(f"    OCR [{label}]: raw='{text}' mapped->'{digits}' (psm={psm}) -> {result}")
        return result

    scanned_stocks = {}

    print("\n" + "-" * 50)
    print("Scanning grid cells...")
    print("-" * 50)

    for item_name, config in items.items():
        position = config.get('position')
        if not position or position == [0, 0]:
            print(f"  ⚠ {item_name}: No position configured, skipping")
            continue

        item_x, item_y = position

        # Determine grid cell
        col_idx = 0 if item_x <= COL_SPLIT_X else 1
        # Row: clamp to valid range
        row_idx = int((item_y - SCAN_Y1) / CELL_H)
        row_idx = max(0, min(GRID_ROWS - 1, row_idx))

        # Crop cell from full image
        cx = col_idx * CELL_W
        cy = row_idx * CELL_H
        cell = full_img[cy:cy + CELL_H, cx:cx + CELL_W]

        # Save cell debug image
        safe = item_name.replace(' ', '_').replace("'", "")
        cv2.imwrite(os.path.join(debug_dir, f"stock_cell_{safe}.png"), cell)

        quantity = ocr_cell(cell, item_name, col_idx=col_idx)
        old_stock = config.get('stock', 0)
        scanned_stocks[item_name] = quantity

        status = "✓" if quantity > 0 else "⚠"
        change = f" (was {old_stock})" if quantity != old_stock else ""
        print(f"  {status} {item_name}: {quantity}{change}  [grid col={col_idx} row={row_idx}]")

    print("\n" + "-" * 50)
    print(f"Scanned {len(scanned_stocks)} items")
    print("-" * 50)

    print("\nUpdate stock on server? (y/n): ", end="")
    confirm = input().strip().lower()

    if confirm == 'y':
        # Apply display offsets before sending: -10 for Tinkerer's Toolkit, -1 for all others
        adjusted_stocks = {}
        for name, qty in scanned_stocks.items():
            offset = 10 if "Tinkerer" in name else 1
            adjusted_stocks[name] = max(0, qty - offset)
        try:
            response = requests.post(
                'http://localhost:8080/api/sell_config/stock/bulk',
                json={'stocks': adjusted_stocks},
                timeout=10
            )
            if response.ok:
                result = response.json()
                print("\n✅ Stock updated on server!")
                for r in result.get('results', []):
                    if 'error' in r:
                        print(f"  ⚠ {r['item']}: {r['error']}")
                    else:
                        print(f"  ✓ {r['item']}: {r['oldStock']} -> {r['newStock']}")
            else:
                print(f"❌ Failed to update: {response.text}")
        except Exception as e:
            print(f"❌ Error updating server: {e}")
    else:
        print("\n⚠ Stock update cancelled")

    print("\n" + "=" * 50)
    print("Scan stock mode completed.")
    print("=" * 50)



if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("Hero Siege Market Bot")
    print("=" * 50)
    print("\nSelect mode:")
    print("  1. Market Bot (buy/sell automation)")
    print("  2. Collect Money (collect gold from sold items)")
    print("  3. Scan Stock (read inventory and update server)")
    print()
    
    choice = input("Enter choice (1, 2, or 3): ").strip()
    
    try:
        if choice == "2":
            print("\n[INFO] Starting Collect Money Mode...")
            collect_money_mode()
        elif choice == "3":
            print("\n[INFO] Starting Scan Stock Mode...")
            scan_stock_mode()
        else:
            print("\n[INFO] Starting Market Bot...")
            run_bot()
    except KeyboardInterrupt:
        print("\n\n[INFO] Bot stopped by user.")
    except Exception as e:
        print(f"\n[ERROR] Bot crashed: {e}")
        raise
