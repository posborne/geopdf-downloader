#!/usr/bin/env python3
"""
Download geo-referenced PDFs (GeoPDFs) for Minnesota DNR recreation areas.

This downloads actual GeoPDFs with embedded coordinate systems from the
Minnesota DNR's GeoPDF download service. These PDFs can be used with GPS
apps like Avenza Maps for offline navigation.

Usage:
    uv run geopdf_downloader.py [--dry-run] [--output-dir DIR] [--category CATEGORY]

Categories available:
    - state_parks (default)
    - state_forests
    - recreation
    - water_trails
    - ohv (off-highway vehicle)
    - water_access
    - state_trails
    - snowmobile
    - trout_streams
    - all
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
import httpx
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TransferSpeedColumn,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

# ArcGIS MapServer for GeoPDF indexes
GEOPDF_INDEX_URL = (
    "https://arcgis.dnr.state.mn.us/arcgis/rest/services/"
    "gen/mndnr_geopdf_downloader_indexes/MapServer"
)
PROXY_URL = "https://arcgis.dnr.state.mn.us/gis/pdf/proxy.ashx"
REFERER = "https://arcgis.dnr.state.mn.us/gis/pdf/"

# Layer IDs in the MapServer
LAYER_IDS = {
    "water_trails": 0,
    "ohv": 1,
    "recreation": 2,
    "state_parks": 3,
    "state_forests": 4,
    "water_access": 5,
    "state_trails": 6,
    "snowmobile": 7,
    "trout_streams": 8,
}

# Concurrency settings
MAX_CONCURRENT_DOWNLOADS = 5
HTTP_TIMEOUT = 120.0

console = Console()


@dataclass
class GeoPDFMap:
    """A GeoPDF map available for download."""

    name: str  # e.g., "Afton (Summer)"
    category: str  # e.g., "state_parks" - used for subdirectory
    collection: str  # e.g., "MNDNR State Parks"
    doc_url: str  # Direct download URL
    size_str: str  # e.g., "4.5 MB"
    epsg_code: str  # e.g., "3857"

    @property
    def filename(self) -> str:
        """Generate a safe filename for this map."""
        # Clean up name for filesystem
        name = self.name
        # Replace problematic characters
        for char in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
            name = name.replace(char, '_')
        # Ensure it ends with .pdf
        if not name.lower().endswith('.pdf'):
            name = f"{name}.pdf"
        return name

    @property
    def size_bytes(self) -> int:
        """Parse size string to bytes."""
        try:
            size_str = self.size_str.upper().strip()
            if 'MB' in size_str:
                return int(float(size_str.replace('MB', '').strip()) * 1024 * 1024)
            elif 'KB' in size_str:
                return int(float(size_str.replace('KB', '').strip()) * 1024)
            elif 'GB' in size_str:
                return int(float(size_str.replace('GB', '').strip()) * 1024 * 1024 * 1024)
        except (ValueError, AttributeError):
            pass
        return 0


async def fetch_maps_for_layer(
    client: httpx.AsyncClient,
    layer_id: int,
    layer_name: str,
) -> list[GeoPDFMap]:
    """Fetch all GeoPDF maps for a given layer."""
    # Build the query URL through the proxy
    query_url = (
        f"{GEOPDF_INDEX_URL}/{layer_id}/query"
        f"?where=1%3D1&outFields=*&f=json"
    )
    proxied_url = f"{PROXY_URL}?{query_url}"

    try:
        response = await client.get(
            proxied_url,
            headers={"Referer": REFERER},
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as e:
        console.print(f"[red]Failed to fetch {layer_name}: {e}[/]")
        return []

    maps: list[GeoPDFMap] = []
    features = data.get("features", [])

    for feature in features:
        attrs = feature.get("attributes", {})
        doc_url = attrs.get("doc_url", "")
        if not doc_url:
            continue

        maps.append(
            GeoPDFMap(
                name=attrs.get("map_name", "Unknown"),
                category=layer_name,
                collection=attrs.get("collection_name", layer_name),
                doc_url=doc_url,
                size_str=attrs.get("size", "0 MB"),
                epsg_code=attrs.get("epsg_code", ""),
            )
        )

    return maps


async def fetch_all_maps(
    client: httpx.AsyncClient,
    categories: list[str],
) -> list[GeoPDFMap]:
    """Fetch all GeoPDF maps for the specified categories."""
    console.print("[bold blue]Fetching map list from DNR GeoPDF service...[/]")

    all_maps: list[GeoPDFMap] = []

    for category in categories:
        layer_id = LAYER_IDS.get(category)
        if layer_id is None:
            console.print(f"[yellow]Unknown category: {category}[/]")
            continue

        console.print(f"  Fetching {category}...")
        maps = await fetch_maps_for_layer(client, layer_id, category)
        console.print(f"    Found {len(maps)} maps")
        all_maps.extend(maps)

    console.print(f"[green]Found {len(all_maps)} total GeoPDF maps[/]")
    return sorted(all_maps, key=lambda m: m.name)


def get_local_path(geopdf: GeoPDFMap, output_dir: Path) -> Path:
    """Get the local file path for a GeoPDF, organized by category."""
    return output_dir / geopdf.category / geopdf.filename


def should_download(geopdf: GeoPDFMap, output_dir: Path) -> bool:
    """Check if a map needs to be downloaded."""
    local_path = get_local_path(geopdf, output_dir)
    if not local_path.exists():
        return True
    # Check if size roughly matches (within 10% to account for size string parsing)
    local_size = local_path.stat().st_size
    expected_size = geopdf.size_bytes
    if expected_size == 0:
        return False  # Can't verify, assume OK
    return abs(local_size - expected_size) > expected_size * 0.1


async def download_map(
    client: httpx.AsyncClient,
    geopdf: GeoPDFMap,
    output_dir: Path,
    progress: Progress,
    task_id: TaskID,
) -> bool:
    """Download a single GeoPDF file."""
    local_path = get_local_path(geopdf, output_dir)
    local_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        async with client.stream("GET", geopdf.doc_url, follow_redirects=True) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            progress.update(task_id, total=total)

            with open(local_path, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    f.write(chunk)
                    progress.update(task_id, advance=len(chunk))

        return True
    except httpx.HTTPError as e:
        console.print(f"[red]Failed to download {geopdf.filename}: {e}[/]")
        # Clean up partial download
        if local_path.exists():
            local_path.unlink()
        return False


async def download_all_maps(
    client: httpx.AsyncClient,
    maps: Sequence[GeoPDFMap],
    output_dir: Path,
) -> tuple[int, int]:
    """Download all maps, returning (success_count, failure_count)."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Filter to only maps that need downloading
    maps_to_download = [m for m in maps if should_download(m, output_dir)]
    skipped = len(maps) - len(maps_to_download)

    if skipped > 0:
        console.print(f"[yellow]Skipping {skipped} already-downloaded maps[/]")

    if not maps_to_download:
        console.print("[green]All maps already downloaded![/]")
        return (0, 0)

    console.print(f"[bold blue]Downloading {len(maps_to_download)} GeoPDF maps...[/]")

    success = 0
    failure = 0
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

    async def download_with_semaphore(
        geopdf: GeoPDFMap,
        progress: Progress,
    ) -> bool:
        async with semaphore:
            task_id = progress.add_task(
                f"[cyan]{geopdf.filename[:40]}",
                total=geopdf.size_bytes,
            )
            result = await download_map(
                client, geopdf, output_dir, progress, task_id
            )
            progress.remove_task(task_id)
            return result

    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        console=console,
    ) as progress:
        tasks = [download_with_semaphore(m, progress) for m in maps_to_download]
        results = await asyncio.gather(*tasks)

        success = sum(1 for r in results if r)
        failure = sum(1 for r in results if not r)

    return (success, failure)


def print_dry_run_summary(maps: Sequence[GeoPDFMap], output_dir: Path) -> None:
    """Print what would be downloaded in dry-run mode."""
    to_download = [m for m in maps if should_download(m, output_dir)]
    skipped = len(maps) - len(to_download)

    console.print("\n[bold]Dry Run Summary[/]")
    console.print(f"  Total GeoPDF maps found: {len(maps)}")
    console.print(f"  Already downloaded: {skipped}")
    console.print(f"  Would download: {len(to_download)}")

    if to_download:
        console.print("\n[bold]Maps to download:[/]")
        total_size = 0
        # Group by category for cleaner output
        by_category: dict[str, list[GeoPDFMap]] = {}
        for m in to_download:
            by_category.setdefault(m.category, []).append(m)
        
        for category in sorted(by_category.keys()):
            console.print(f"\n  [bold cyan]{category}/[/]")
            for m in sorted(by_category[category], key=lambda x: x.filename):
                total_size += m.size_bytes
                console.print(f"    {m.filename} ({m.size_str})")
        console.print(f"\n[bold]Total download size:[/] {total_size / (1024 * 1024):.1f} MB")


async def async_main(args: argparse.Namespace) -> int:
    """Main async entry point."""
    output_dir = Path(args.output_dir).resolve()

    # Determine which categories to fetch
    if args.category == "all":
        categories = list(LAYER_IDS.keys())
    else:
        categories = [args.category]

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        # Fetch map list from ArcGIS service
        maps = await fetch_all_maps(client, categories)

        if not maps:
            console.print("[red]No maps found![/]")
            return 1

        if args.dry_run:
            print_dry_run_summary(maps, output_dir)
            return 0

        # Download maps
        success, failure = await download_all_maps(client, maps, output_dir)

        console.print(f"\n[bold green]Downloaded {success} GeoPDF maps[/]")
        if failure > 0:
            console.print(f"[bold red]Failed to download {failure} maps[/]")
            return 1

    return 0


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Download GeoPDFs for Minnesota DNR recreation areas",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Categories:
  state_parks    State park maps (default)
  state_forests  State forest maps
  recreation     Recreation area maps
  water_trails   Water trail maps
  ohv            Off-highway vehicle maps
  water_access   Water access maps
  state_trails   State trail maps
  snowmobile     Snowmobile trail maps
  trout_streams  Trout stream maps
  all            All categories
""",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Show what would be downloaded without downloading",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default="./downloads",
        help="Output directory for downloaded maps (default: ./downloads)",
    )
    parser.add_argument(
        "-c",
        "--category",
        default="state_parks",
        choices=list(LAYER_IDS.keys()) + ["all"],
        help="Category of maps to download (default: state_parks)",
    )

    args = parser.parse_args()

    try:
        exit_code = asyncio.run(async_main(args))
        sys.exit(exit_code)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/]")
        sys.exit(130)


if __name__ == "__main__":
    main()
