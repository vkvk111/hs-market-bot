"""
Mouse Position Helper
Shows current mouse coordinates - useful for finding screen regions
"""

import time

try:
    import pyautogui
except ImportError:
    print("Installing pyautogui...")
    import subprocess
    subprocess.check_call(['pip', 'install', 'pyautogui'])
    import pyautogui


def main():
    print("=" * 50)
    print("Mouse Position Helper")
    print("=" * 50)
    print("\nMove your mouse to get coordinates.")
    print("Press Ctrl+C to exit.\n")
    
    try:
        while True:
            x, y = pyautogui.position()
            print(f"\rX: {x:4d}  Y: {y:4d}", end='', flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n\nDone!")


if __name__ == "__main__":
    main()
