"""
Test script for the selling sequence
"""
import time
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from client import focus_game_window, click, click_sell_tab, SELL_TAB, type_text, move_mouse

# Sell item button position
SELL_ITEM_BTN = (441, 373)

# Item positions in inventory when selling
SELL_ITEMS = {
    "Angel's Wisdom": (1545, 821),
    "Angelic Gem": (1545, 881),
    "Elemental Gem": (1545, 940),
    "Tinkerers Toolkit": (1545, 1000),
}

# Default sell amounts per item
SELL_AMOUNTS = {
    "Tinkerers Toolkit": 10,
}

# UI positions
AMOUNT_CONFIRM_BTN = (1152, 768)  # Confirm amount selection
SELL_SLOT_BTN = (851, 575)        # Place item in selling slot
PRICE_INPUT_BOX = (837, 746)      # Click to select price input
SELL_CONFIRM_BTN = (834, 926)     # Final confirm sell button


def shift_click(x, y):
    """Shift+click at coordinates"""
    import ctypes
    
    # Key codes
    VK_SHIFT = 0x10
    KEYEVENTF_KEYUP = 0x0002
    
    # Hold shift
    ctypes.windll.user32.keybd_event(VK_SHIFT, 0, 0, 0)
    time.sleep(0.05)
    
    # Click
    click(x, y)
    time.sleep(0.05)
    
    # Release shift
    ctypes.windll.user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.1)


def test_sell_sequence():
    """Test: Full sell sequence for one item"""
    print("=" * 50)
    print("Hero Siege Market Bot - SELL SEQUENCE TEST")
    print("=" * 50)
    print(f"Sell Tab: {SELL_TAB}")
    print(f"Sell Item Button: {SELL_ITEM_BTN}")
    print("\nAvailable items:")
    for name, pos in SELL_ITEMS.items():
        print(f"  - {name}: {pos}")
    print("=" * 50)
    
    # Get item choice
    print("\nWhich item to sell?")
    print("1. Angel's Wisdom")
    print("2. Angelic Gem")
    print("3. Elemental Gem")
    print("4. Tinkerers Toolkit")
    choice = input("Enter choice (1-4): ").strip()
    
    if choice == "1":
        item_name = "Angel's Wisdom"
    elif choice == "2":
        item_name = "Angelic Gem"
    elif choice == "3":
        item_name = "Elemental Gem"
    elif choice == "4":
        item_name = "Tinkerers Toolkit"
    else:
        print("Invalid choice")
        return
    
    item_pos = SELL_ITEMS[item_name]
    
    # Get amount - auto-set based on item
    default_amount = SELL_AMOUNTS.get(item_name, 1)
    amount = str(default_amount)
    price = input("Price per item: ").strip()
    
    if not price:
        print("Price is required")
        return
    
    print(f"\n--- Selling {amount}x {item_name} at {price} each ---")
    input("Press Enter when game is open and market is visible...")
    
    # Focus the game first
    print("\n1. Focusing game...")
    focus_game_window("Hero Siege")
    time.sleep(0.5)
    
    # Click sell tab to go to My Listings
    print("\n2. Clicking Sell tab (My Listings)...")
    click_sell_tab()
    time.sleep(0.5)
    
    # Click the Sell Item button
    print(f"\n3. Clicking Sell Item button at {SELL_ITEM_BTN}...")
    click(*SELL_ITEM_BTN)
    time.sleep(0.5)
    
    # Shift+click the item to select it
    print(f"\n4. Shift+clicking {item_name} at {item_pos}...")
    shift_click(*item_pos)
    time.sleep(0.3)
    
    # Type amount (always required)
    print(f"\n5. Typing amount: {amount}...")
    type_text(amount)
    time.sleep(0.2)
    
    # Click confirm amount
    print(f"\n6. Clicking amount confirm at {AMOUNT_CONFIRM_BTN}...")
    click(*AMOUNT_CONFIRM_BTN)
    time.sleep(0.5)
    
    # Click sell slot to place item
    print(f"\n7. Clicking sell slot at {SELL_SLOT_BTN}...")
    click(*SELL_SLOT_BTN)
    time.sleep(0.5)
    
    # Click price input box
    print(f"\n8. Clicking price input at {PRICE_INPUT_BOX}...")
    click(*PRICE_INPUT_BOX)
    time.sleep(0.3)
    
    # Type the price
    print(f"\n9. Typing price: {price}...")
    type_text(price)
    time.sleep(0.3)
    
    # Move mouse to final confirm (don't click yet)
    print(f"\n10. Moving mouse to sell confirm at {SELL_CONFIRM_BTN}...")
    move_mouse(*SELL_CONFIRM_BTN)
    print("    (Mouse is at confirm button - click manually to complete)")
    time.sleep(0.3)
    
    print("\n" + "=" * 50)
    print("DONE - Item should be listed!")
    print("=" * 50)


if __name__ == "__main__":
    test_sell_sequence()
