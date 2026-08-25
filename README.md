# Over-Head

A wall-mounted live aircraft display for a standard monitor. Over-Head retrieves
nearby aircraft from the Airplanes.live v2 point API, selects the closest
aircraft with a recent position, and renders a 128×64 RGB display which is
scaled cleanly to fill the screen.

Every render writes the same pixels in two formats:

- `output/frame.rgb`: raw, tightly packed RGB888 bytes, row-major from the top left
- `output/frame.png`: a directly viewable preview

The raw frame is exactly 24,576 bytes: `128 × 64 × 3`.

## Install

Python 3.10 or later is recommended.

### Linux

```bash
git clone https://github.com/egyptianteyes/Over-Head.git
cd Over-Head
python3 -m pip install -r requirements.txt
cp config.example.json config.json
python3 monitor.py --demo --open
```

### Windows PowerShell

```powershell
git clone https://github.com/egyptianteyes/Over-Head.git
Set-Location Over-Head
py -m pip install -r requirements.txt
Copy-Item config.example.json config.json
py monitor.py --demo --open
```

The browser opens at `http://127.0.0.1:8765/`. Double-click the display to enter
full-screen mode. Press `Ctrl+C` in the terminal to stop it.

## Configure live aircraft

Edit `config.json` and replace the example latitude and longitude with the
coordinates of the display location:

```json
{
  "latitude": 51.5074,
  "longitude": -0.1278,
  "radius_nm": 25,
  "refresh_seconds": 10,
  "output_rgb": "output/frame.rgb",
  "output_png": "output/frame.png",
  "demo_on_failure": true
}
```

Then start Over-Head with live data:

```bash
python3 monitor.py --open
```

On Windows, use `py monitor.py --open`.

For a dedicated monitor, start the server at boot and launch Chromium in kiosk
mode with:

```bash
chromium --kiosk --noerrdialogs --disable-infobars http://127.0.0.1:8765/
```

## Use live aircraft data

Edit the latitude and longitude in `config.json`, then run:

```bash
python3 overhead.py
```

For continuous output:

```bash
python3 overhead.py --watch
```

The program refreshes `frame.rgb` and `frame.png` every ten seconds by default.
If the API cannot be reached, it produces a labelled demo frame rather than
leaving the output undefined. Set `demo_on_failure` to `false` if failure should
instead stop the program.

## Test

```bash
python3 -m unittest -v
```

## Connecting real RGB hardware later

Keep `overhead.py` as the selection and rendering layer. The panel driver
only needs to consume the RGB888 byte stream and map its 128×64 pixels to the
chosen hardware. Suitable adapters can target HUB75 panels, addressable RGB
tiles, SDL, Linux framebuffer, or another panel controller without changing the
Airplanes.live or layout logic.

No Airplanes.live API key is embedded or required by this version.

## Author

Over-Head is an [Egyptan Eyes](https://egyptianeyes.com) project by
[20tele](https://20tele.com).

## Acknowledgements

Over-Head was inspired by
[TheFlightWall](https://github.com/AxisNimble/TheFlightWall_OSS), an open-source
LED aircraft information display created by AxisNimble.

Live aircraft position data is provided by the
[Airplanes.live](https://airplanes.live/) community ADS-B network.

Over-Head is an independent project and is not affiliated with or endorsed by
TheFlightWall or Airplanes.live.

## License

Over-Head is released under the [MIT License](LICENSE).
