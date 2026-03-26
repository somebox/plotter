// Pure functions for path optimization, coordinate transforms, G-code generation,
// and SVG element conversion. No DOM dependencies — fully testable under Node.js.

// ── Path Optimization ──

// Douglas-Peucker simplification
function simplifyPath(points, tolerance) {
  if (points.length <= 2 || tolerance <= 0) return points;

  let maxDist = 0, maxIdx = 0;
  const first = points[0], last = points[points.length - 1];
  const dx = last.x - first.x, dy = last.y - first.y;
  const lenSq = dx * dx + dy * dy;

  for (let i = 1; i < points.length - 1; i++) {
    let dist;
    if (lenSq === 0) {
      const ex = points[i].x - first.x, ey = points[i].y - first.y;
      dist = Math.sqrt(ex * ex + ey * ey);
    } else {
      const t = Math.max(0, Math.min(1, ((points[i].x - first.x) * dx + (points[i].y - first.y) * dy) / lenSq));
      const px = first.x + t * dx, py = first.y + t * dy;
      const ex = points[i].x - px, ey = points[i].y - py;
      dist = Math.sqrt(ex * ex + ey * ey);
    }
    if (dist > maxDist) { maxDist = dist; maxIdx = i; }
  }

  if (maxDist > tolerance) {
    const left = simplifyPath(points.slice(0, maxIdx + 1), tolerance);
    const right = simplifyPath(points.slice(maxIdx), tolerance);
    return left.slice(0, -1).concat(right);
  }
  return [first, last];
}

// Sort paths by nearest neighbor to minimize pen-up travel.
// Uses a spatial grid for O(n) average-case lookups instead of O(n²) brute force.
function sortPathsNearest(paths) {
  if (paths.length <= 1) return paths;

  // Find bounding box of all endpoints
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (let i = 0; i < paths.length; i++) {
    const p = paths[i];
    const sx = p[0].x, sy = p[0].y, ex = p[p.length-1].x, ey = p[p.length-1].y;
    if (sx < minX) minX = sx; if (sy < minY) minY = sy;
    if (sx > maxX) maxX = sx; if (sy > maxY) maxY = sy;
    if (ex < minX) minX = ex; if (ey < minY) minY = ey;
    if (ex > maxX) maxX = ex; if (ey > maxY) maxY = ey;
  }

  // Build grid — target ~4 paths per cell on average
  const cellCount = Math.max(1, Math.ceil(Math.sqrt(paths.length / 4)));
  const w = (maxX - minX) || 1, h = (maxY - minY) || 1;
  const cellW = w / cellCount, cellH = h / cellCount;
  const grid = new Map(); // cellKey -> Set of path indices

  function cellKey(x, y) {
    const cx = Math.min(cellCount - 1, Math.max(0, Math.floor((x - minX) / cellW)));
    const cy = Math.min(cellCount - 1, Math.max(0, Math.floor((y - minY) / cellH)));
    return cx + cy * cellCount;
  }

  // Insert each path into grid cells for both its start and end points
  for (let i = 0; i < paths.length; i++) {
    const p = paths[i];
    const k1 = cellKey(p[0].x, p[0].y);
    const k2 = cellKey(p[p.length-1].x, p[p.length-1].y);
    if (!grid.has(k1)) grid.set(k1, new Set());
    grid.get(k1).add(i);
    if (k2 !== k1) {
      if (!grid.has(k2)) grid.set(k2, new Set());
      grid.get(k2).add(i);
    }
  }

  function removeFromGrid(idx) {
    const p = paths[idx];
    const k1 = cellKey(p[0].x, p[0].y);
    const k2 = cellKey(p[p.length-1].x, p[p.length-1].y);
    const s1 = grid.get(k1); if (s1) { s1.delete(idx); if (s1.size === 0) grid.delete(k1); }
    if (k2 !== k1) {
      const s2 = grid.get(k2); if (s2) { s2.delete(idx); if (s2.size === 0) grid.delete(k2); }
    }
  }

  // Search outward from a cell position in expanding rings
  function findNearest(x, y) {
    const ccx = Math.min(cellCount - 1, Math.max(0, Math.floor((x - minX) / cellW)));
    const ccy = Math.min(cellCount - 1, Math.max(0, Math.floor((y - minY) / cellH)));
    let bestDist = Infinity, bestIdx = -1, bestReverse = false;

    for (let radius = 0; radius <= cellCount; radius++) {
      // If we found something and the next ring can't be closer, stop
      if (bestDist < Infinity) {
        const ringMinDist = Math.max(0, radius - 1) * Math.min(cellW, cellH);
        if (ringMinDist * ringMinDist > bestDist) break;
      }

      const rMinX = Math.max(0, ccx - radius), rMaxX = Math.min(cellCount - 1, ccx + radius);
      const rMinY = Math.max(0, ccy - radius), rMaxY = Math.min(cellCount - 1, ccy + radius);

      for (let gy = rMinY; gy <= rMaxY; gy++) {
        for (let gx = rMinX; gx <= rMaxX; gx++) {
          // Only check cells on the ring perimeter (skip interior — already checked)
          if (radius > 0 && gx > rMinX && gx < rMaxX && gy > rMinY && gy < rMaxY) continue;
          const cell = grid.get(gx + gy * cellCount);
          if (!cell) continue;
          for (const i of cell) {
            const p = paths[i];
            const sdx = p[0].x - x, sdy = p[0].y - y;
            const dStart = sdx * sdx + sdy * sdy;
            if (dStart < bestDist) { bestDist = dStart; bestIdx = i; bestReverse = false; }
            const edx = p[p.length-1].x - x, edy = p[p.length-1].y - y;
            const dEnd = edx * edx + edy * edy;
            if (dEnd < bestDist) { bestDist = dEnd; bestIdx = i; bestReverse = true; }
          }
        }
      }
    }
    return { bestIdx, bestReverse };
  }

  const sorted = [];
  let curX = 0, curY = 0;
  for (let n = 0; n < paths.length; n++) {
    const { bestIdx, bestReverse } = findNearest(curX, curY);
    removeFromGrid(bestIdx);
    let path = paths[bestIdx];
    if (bestReverse) path = path.slice().reverse();
    sorted.push(path);
    curX = path[path.length-1].x;
    curY = path[path.length-1].y;
  }
  return sorted;
}

// Merge paths where one ends close to where the next starts
function mergePaths(paths, gap) {
  if (gap <= 0 || paths.length <= 1) return paths;
  const gapSq = gap * gap;
  const merged = [paths[0].slice()];
  for (let i = 1; i < paths.length; i++) {
    const prev = merged[merged.length - 1];
    const last = prev[prev.length - 1];
    const first = paths[i][0];
    const dx = first.x - last.x, dy = first.y - last.y;
    if (dx * dx + dy * dy <= gapSq) {
      for (let j = 0; j < paths[i].length; j++) {
        prev.push(paths[i][j]);
      }
    } else {
      merged.push(paths[i].slice());
    }
  }
  return merged;
}

// Full optimization pipeline: simplify, sort, merge
function optimizePaths(paths, simplifyTol, mergeGap) {
  let result = paths;
  if (simplifyTol > 0) {
    result = result.map(p => simplifyPath(p, simplifyTol)).filter(p => p.length >= 2);
  }
  result = sortPathsNearest(result);
  if (mergeGap > 0) {
    result = mergePaths(result, mergeGap);
  }
  return result;
}

// ── Coordinate Transforms ──

// Transform SVG paths to printer coordinates (with Y flip)
function transformPaths(paths, bbox, scale, ox, oy) {
  const svgH = bbox.maxY - bbox.minY;
  return paths.map(path => path.map(pt => ({
    x: (pt.x - bbox.minX) * scale + ox,
    y: (svgH - (pt.y - bbox.minY)) * scale + oy,
  })));
}

// Compute scale and offset to fit SVG bbox within print bounds
function computeFitToBed(bbox, bounds) {
  const svgW = bbox.maxX - bbox.minX;
  const svgH = bbox.maxY - bbox.minY;
  if (svgW === 0 || svgH === 0) return null;

  const availX = bounds.maxX - bounds.minX;
  const availY = bounds.maxY - bounds.minY;
  const scale = Math.min(availX / svgW, availY / svgH);
  const ox = bounds.minX + (availX - svgW * scale) / 2;
  const oy = bounds.minY + (availY - svgH * scale) / 2;
  return { scale, ox, oy };
}

// ── Bounds Checking ──

// Returns null if all points within bounds, or an error string
function checkBounds(paths, bounds) {
  for (const path of paths) {
    for (const pt of path) {
      if (pt.x < bounds.minX || pt.x > bounds.maxX || pt.y < bounds.minY || pt.y > bounds.maxY) {
        return `Exceeds safe area (${bounds.minX}-${bounds.maxX}, ${bounds.minY}-${bounds.maxY})! Adjust offset or scale.`;
      }
    }
  }
  return null;
}

// ── G-code Generation ──

function generateMoveCommands(x, y, f, penUpZ, zSpeed) {
  return [
    "G90",
    `G1 Z${penUpZ} F${zSpeed}`,
    `G1 X${x} Y${y} F${f}`,
  ];
}

function generateCircleCommands(x, y, r, f, penUpZ, penDownZ, zSpeed) {
  return [
    "G90",
    `G1 Z${penUpZ} F${zSpeed}`,
    `G1 X${x + r} Y${y} F${f}`,
    `G1 Z${penDownZ} F${zSpeed}`,
    `G2 X${x + r} Y${y} I${-r} J0 F${f}`,
    `G1 Z${penUpZ} F${zSpeed}`,
  ];
}

function generatePlotCommands(paths, speed, penUpZ, penDownZ) {
  return generatePlotCommandsWithMap(paths, speed, penUpZ, penDownZ).cmds;
}

// Like generatePlotCommands but also returns a parallel map array
// where map[i] describes what cmds[i] does (pathIndex, pointIndex, type).
function generatePlotCommandsWithMap(paths, speed, penUpZ, penDownZ) {
  const cmds = [];
  const map = [];

  cmds.push("G28");              map.push(null);
  cmds.push("G90");              map.push(null);
  cmds.push(`G0 Z${penUpZ}`);   map.push(null);

  for (let p = 0; p < paths.length; p++) {
    const path = paths[p];
    if (path.length < 2) continue;
    cmds.push(`G0 X${path[0].x.toFixed(2)} Y${path[0].y.toFixed(2)}`);
    map.push({ pathIndex: p, pointIndex: 0, type: "travel" });
    cmds.push(`G0 Z${penDownZ}`);
    map.push({ pathIndex: p, pointIndex: 0, type: "pendown" });
    for (let i = 1; i < path.length; i++) {
      cmds.push(`G1 X${path[i].x.toFixed(2)} Y${path[i].y.toFixed(2)} F${speed}`);
      map.push({ pathIndex: p, pointIndex: i, type: "draw" });
    }
    cmds.push(`G0 Z${penUpZ}`);
    map.push({ pathIndex: p, pointIndex: path.length - 1, type: "penup" });
  }

  return { cmds, map };
}

// ── SVG Element Conversion ──

// Convert SVG shape attributes to a path d string.
// attrs: function(name) → string|null, mimicking el.getAttribute
function elementToPathD(tag, attrs) {
  if (tag === "line") {
    const x1 = attrs("x1") || 0, y1 = attrs("y1") || 0;
    const x2 = attrs("x2") || 0, y2 = attrs("y2") || 0;
    return `M${x1},${y1} L${x2},${y2}`;
  }
  if (tag === "rect") {
    const x = +attrs("x") || 0, y = +attrs("y") || 0;
    const w = +attrs("width"), h = +attrs("height");
    return `M${x},${y} L${x+w},${y} L${x+w},${y+h} L${x},${y+h} Z`;
  }
  if (tag === "circle") {
    const cx = +attrs("cx") || 0, cy = +attrs("cy") || 0;
    const r = +attrs("r");
    return `M${cx+r},${cy} A${r},${r} 0 1,0 ${cx-r},${cy} A${r},${r} 0 1,0 ${cx+r},${cy}`;
  }
  if (tag === "ellipse") {
    const cx = +attrs("cx") || 0, cy = +attrs("cy") || 0;
    const rx = +attrs("rx"), ry = +attrs("ry");
    return `M${cx+rx},${cy} A${rx},${ry} 0 1,0 ${cx-rx},${cy} A${rx},${ry} 0 1,0 ${cx+rx},${cy}`;
  }
  if (tag === "polyline" || tag === "polygon") {
    const pts = attrs("points").trim().split(/[\s,]+/);
    let d = "";
    for (let i = 0; i < pts.length; i += 2) {
      d += (i === 0 ? "M" : "L") + pts[i] + "," + pts[i+1] + " ";
    }
    if (tag === "polygon") d += "Z";
    return d;
  }
  return null;
}

// ── Time Formatting ──

function formatDuration(ms) {
  const totalSec = Math.floor(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

// ── Exports (Node.js) ──

if (typeof module !== "undefined") {
  module.exports = {
    simplifyPath,
    sortPathsNearest,
    mergePaths,
    optimizePaths,
    transformPaths,
    computeFitToBed,
    checkBounds,
    generateMoveCommands,
    generateCircleCommands,
    generatePlotCommands,
    generatePlotCommandsWithMap,
    elementToPathD,
    formatDuration,
  };
}
