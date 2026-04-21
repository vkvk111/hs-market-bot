"""
Hero Siege Market Bot - Screen Reader Module
Detects if auction house is open using template matching
"""

import time
import json
from pathlib import Path
from typing import Optional, Tuple

import mss
import cv2
import numpy as np


class ScreenReader:
    """Detects Hero Siege auction house using image matching"""
    
    def __init__(self, config_path: str = None):
        self.config = self._load_config(config_path)
        self.sct = mss.mss()
        self.templates = {}
        
        # Load templates
        self._load_templates()
        
    def _load_config(self, config_path: str = None) -> dict:
        """Load configuration from JSON file"""
        if config_path is None:
            config_path = Path(__file__).parent / 'config.json'
        
        try:
            with open(config_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"Config not found at {config_path}, using defaults")
            return {}
        except json.JSONDecodeError as e:
            print(f"Invalid config JSON: {e}")
            return {}
    
    def _load_templates(self):
        """Load template images for matching"""
        templates_dir = Path(__file__).parent / 'images'
        templates_dir.mkdir(exist_ok=True)
        
        # Look for template images
        for img_path in templates_dir.glob('*.png'):
            name = img_path.stem
            img = cv2.imread(str(img_path), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                self.templates[name] = img
                print(f"Loaded template: {name}")
        
        if 'market_top' not in self.templates:
            print("\n⚠️  Template 'market_top.png' not found!")
            print("   Add it to: client/images/market_top.png")
            print("   This should be an image of the market window header.\n")
    
    def capture_screen(self, monitor: int = None) -> np.ndarray:
        """Capture the screen"""
        if monitor is None:
            monitor = self.config.get('monitor', 1)
        
        mon = self.sct.monitors[monitor]
        screenshot = self.sct.grab(mon)
        
        # Convert to numpy array (BGR format for OpenCV)
        img = np.array(screenshot)
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        
        return img
    
    def find_template(self, screen: np.ndarray, template_name: str, 
                      threshold: float = None) -> Optional[Tuple[int, int, int, int]]:
        """
        Find a template image in the screen
        
        Returns: (x, y, width, height) of the match, or None if not found
        """
        if template_name not in self.templates:
            return None
        
        template = self.templates[template_name]
        if threshold is None:
            threshold = self.config.get('template_threshold', 0.8)
        
        # Convert to grayscale for matching
        if len(screen.shape) == 3:
            gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        else:
            gray = screen
        
        # Template matching
        result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
        
        if max_val >= threshold:
            h, w = template.shape[:2]
            return (max_loc[0], max_loc[1], w, h)
        
        return None
    
    def is_market_open(self) -> bool:
        """Check if the market/auction house is open"""
        if 'market_top' not in self.templates:
            print("No market_top template loaded!")
            return False
        
        screen = self.capture_screen()
        match = self.find_template(screen, 'market_top')
        return match is not None
    
    def capture_region(self, x: int, y: int, width: int, height: int) -> np.ndarray:
        """Capture a specific region of the screen"""
        monitor = self.config.get('monitor', 1)
        mon = self.sct.monitors[monitor]
        
        region = {
            'left': mon['left'] + x,
            'top': mon['top'] + y,
            'width': width,
            'height': height
        }
        
        screenshot = self.sct.grab(region)
        img = np.array(screenshot)
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        
        return img
    
    def save_screenshot(self, filename: str = None):
        """Save a screenshot for creating templates"""
        img = self.capture_screen()
        
        if filename is None:
            filename = f"screenshot_{int(time.time())}.png"
        
        screenshots_dir = Path(__file__).parent / 'screenshots'
        screenshots_dir.mkdir(exist_ok=True)
        
        filepath = screenshots_dir / filename
        cv2.imwrite(str(filepath), img)
        print(f"Saved: {filepath}")
        return str(filepath)
    
    def create_template(self, x: int, y: int, width: int, height: int, name: str):
        """Capture a region and save it as a template"""
        monitor = self.config.get('monitor', 1)
        mon = self.sct.monitors[monitor]
        
        region = {
            'left': mon['left'] + x,
            'top': mon['top'] + y,
            'width': width,
            'height': height
        }
        
        screenshot = self.sct.grab(region)
        img = np.array(screenshot)
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        
        templates_dir = Path(__file__).parent / 'images'
        templates_dir.mkdir(exist_ok=True)
        
        filepath = templates_dir / f"{name}.png"
        cv2.imwrite(str(filepath), img)
        
        # Reload templates
        self._load_templates()
        
        print(f"Created template: {filepath}")
        return str(filepath)


def main():
    """Simple market detection loop"""
    print("=" * 50)
    print("Hero Siege Market Detector")
    print("=" * 50)
    
    reader = ScreenReader()
    
    if 'market_top' not in reader.templates:
        print("\nNo market_top template found!")
        print("Run calibration first to create it.\n")
        print("Commands:")
        print("  1 - Take screenshot")
        print("  2 - Create template (enter: x y width height)")
        print("  q - Quit")
        
        while True:
            choice = input("\n> ").strip().lower()
            
            if choice == '1':
                reader.save_screenshot()
            elif choice == '2':
                print("Enter: x y width height")
                try:
                    parts = input("> ").split()
                    x, y, w, h = map(int, parts[:4])
                    reader.create_template(x, y, w, h, 'market_top')
                except (ValueError, IndexError):
                    print("Invalid input. Example: 100 50 300 40")
            elif choice == 'q':
                return
    
    print("\nMonitoring for market...")
    print("Press Ctrl+C to stop.\n")
    
    try:
        while True:
            is_open = reader.is_market_open()
            status = "✅ OPEN" if is_open else "❌ CLOSED"
            print(f"\rMarket: {status}  ", end='', flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n\nStopped.")


if __name__ == "__main__":
    main()
