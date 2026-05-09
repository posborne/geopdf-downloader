# Minnesota DNR GeoPDF Downloader

A CLI tool to autonomously download geo-referenced PDFs (GeoPDFs) for Minnesota DNR recreation areas. These maps include embedded coordinate systems and are ideal for offline navigation in apps like Avenza Maps.

## Features

- **Automated Downloads:** Fetches the latest map list directly from the MN DNR ArcGIS service.
- **Concurrent Downloads:** Downloads multiple maps in parallel for speed.
- **Smart Sync:** Skips already-downloaded maps and verifies file sizes.
- **Organized:** Automatically sorts maps into subdirectories by category.
- **Dry Run Mode:** Preview what will be downloaded without saving any files.

## Requirements

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended for running)

## Usage

The easiest way to run the downloader is using `uv`:

```bash
# Download State Park maps (default)
uv run geopdf_downloader.py

# Preview what would be downloaded for all categories
uv run geopdf_downloader.py --dry-run --category all

# Download OHV maps to a specific directory
uv run geopdf_downloader.py --category ohv --output-dir ./my_maps
```

### Options

- `-n, --dry-run`: Show what would be downloaded without actually downloading.
- `-o, --output-dir DIR`: Directory to save maps (default: `./downloads`).
- `-c, --category CATEGORY`: Category of maps to download.

### Available Categories

- `state_parks` (Default)
- `state_forests`
- `recreation`
- `water_trails`
- `ohv` (Off-highway vehicle)
- `water_access`
- `state_trails`
- `snowmobile`
- `trout_streams`
- `all`

## Data Source

Maps are sourced from the [Minnesota Department of Natural Resources](https://www.dnr.state.mn.us/). This tool is not affiliated with the MN DNR.

## License

This project is licensed under the [MIT License](LICENSE).
