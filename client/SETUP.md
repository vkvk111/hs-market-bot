# Market Bot Setup

## 1. Install Python Dependencies

```bash
cd client
pip install -r requirements.txt
```

## 2. Create the Market Template

1. Open Hero Siege and go to the market/auction house
2. Run the calibration tool:
   ```bash
   python screen_reader.py
   ```
3. Take a screenshot (option `1`)
4. Open `screenshots/` folder and find the coordinates of the market header
5. Create the template (option `2`): enter `x y width height`

Example: `100 50 300 40` captures a 300x40 region starting at position (100, 50)

## 3. Run the Client

Once you have `images/market_top.png`:

```bash
python client.py
```

The script will print whether the market is **OPEN** or **CLOSED** every second.

## Configuration

Edit `config.json`:

```json
{
    "monitor": 1,            // 1 = primary, 2 = secondary
    "template_threshold": 0.8  // Lower if template not found (try 0.7)
}
```
