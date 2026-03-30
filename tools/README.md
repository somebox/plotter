# Tools

Standalone generators and utilities for creating plotter-ready SVG artwork.

## Artwork Generators

### `beatles_typography.py`

Generates a wandering-ribbon typographic poster from Beatles lyrics using the Relief SingleLine plotter font.

On first run, lyrics are fetched from [tylerlewiscook/beatles-lyrics](https://github.com/tylerlewiscook/beatles-lyrics) on GitHub and cached locally to `.beatles_lyrics_cache.json` (gitignored). Subsequent runs use the cache.

```bash
python tools/beatles_typography.py                     # defaults
python tools/beatles_typography.py --font-size 8 --png # with PNG preview (requires cairosvg)
```

### `perlin_landscape.mjs`

Perlin noise landscape generator adapted from [turtletoy.net](https://turtletoy.net/turtle/65cb465053). Requires the `turtletoy` npm package.

```bash
cd tools && npm install
node tools/perlin_landscape.mjs -o landscape.svg
node tools/perlin_landscape.mjs --lines 400 --panels 6 --seed 42
```

### `calibration_grid.py`

Generates SVG calibration grids for testing shading/fill patterns. Produces a grid where rows are pattern types (hatch, crosshatch, dots, circles) and columns are density levels.

```bash
python tools/calibration_grid.py -o calibration.svg
```

## Utilities

### `gpkg_to_svg.py`

Converts swisstopo GeoPackage layers to SVG for pen plotting.

```bash
python tools/gpkg_to_svg.py input.gpkg --layers buildings,contours
python tools/gpkg_to_svg.py input.gpkg --bbox 2600000,1199000,2602000,1201000 --scale 5000
```

### `speed_test.py`

Measures actual printer feed rates by timing repeated moves. Homes the machine, then runs batches of random XY and Z moves at each target speed.

```bash
python tools/speed_test.py
```

## Fonts

`fonts/ReliefSingleLineSVG-Regular.svg` is from the open-source [Relief SingleLine](https://github.com/isdat-type/Relief-SingleLine) project, a single-stroke font designed for plotters and engravers.
