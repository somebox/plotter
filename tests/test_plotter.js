/**
 * Tests for static/plotter.js pure functions.
 * Run with: node tests/test_plotter.js
 */
const assert = require("assert");
const {
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
} = require("../static/plotter.js");

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    console.error(`FAIL: ${name}`);
    console.error(`  ${e.message}`);
  }
}

// ── simplifyPath ──

test("simplifyPath: straight line reduces to endpoints", () => {
  const pts = [{ x: 0, y: 0 }, { x: 5, y: 5 }, { x: 10, y: 10 }];
  const result = simplifyPath(pts, 0.1);
  assert.deepStrictEqual(result, [{ x: 0, y: 0 }, { x: 10, y: 10 }]);
});

test("simplifyPath: preserves corners", () => {
  const pts = [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }];
  const result = simplifyPath(pts, 0.1);
  assert.strictEqual(result.length, 3);
});

test("simplifyPath: two points unchanged", () => {
  const pts = [{ x: 0, y: 0 }, { x: 10, y: 10 }];
  const result = simplifyPath(pts, 1.0);
  assert.deepStrictEqual(result, pts);
});

test("simplifyPath: single point unchanged", () => {
  const pts = [{ x: 5, y: 5 }];
  const result = simplifyPath(pts, 1.0);
  assert.deepStrictEqual(result, pts);
});

test("simplifyPath: zero tolerance keeps all points", () => {
  const pts = [{ x: 0, y: 0 }, { x: 5, y: 5 }, { x: 10, y: 10 }];
  const result = simplifyPath(pts, 0);
  assert.deepStrictEqual(result, pts);
});

test("simplifyPath: high tolerance reduces to endpoints", () => {
  const pts = [
    { x: 0, y: 0 }, { x: 1, y: 0.1 }, { x: 2, y: -0.1 },
    { x: 3, y: 0.05 }, { x: 4, y: 0 },
  ];
  const result = simplifyPath(pts, 1.0);
  assert.deepStrictEqual(result, [{ x: 0, y: 0 }, { x: 4, y: 0 }]);
});

test("simplifyPath: preserves significant deviation", () => {
  const pts = [{ x: 0, y: 0 }, { x: 5, y: 10 }, { x: 10, y: 0 }];
  const result = simplifyPath(pts, 1.0);
  assert.strictEqual(result.length, 3);
});

// ── sortPathsNearest ──

test("sortPathsNearest: empty returns empty", () => {
  assert.deepStrictEqual(sortPathsNearest([]), []);
});

test("sortPathsNearest: single path unchanged", () => {
  const paths = [[{ x: 5, y: 5 }, { x: 10, y: 10 }]];
  const result = sortPathsNearest(paths);
  assert.strictEqual(result.length, 1);
});

test("sortPathsNearest: picks closest path first", () => {
  const paths = [
    [{ x: 100, y: 100 }, { x: 110, y: 110 }],
    [{ x: 1, y: 1 }, { x: 5, y: 5 }],
  ];
  const result = sortPathsNearest(paths);
  // Path starting near origin (1,1) should come first
  assert.strictEqual(result[0][0].x, 1);
  assert.strictEqual(result[0][0].y, 1);
});

test("sortPathsNearest: may reverse paths to minimize travel", () => {
  const paths = [
    [{ x: 0, y: 0 }, { x: 10, y: 0 }],
    [{ x: 20, y: 0 }, { x: 11, y: 0 }],
  ];
  const result = sortPathsNearest(paths);
  // Second path should be reversed so it starts at 11 (closer to 10)
  assert.strictEqual(result[1][0].x, 11);
});

test("sortPathsNearest: preserves all paths at scale", () => {
  // Simulate a complex SVG with many small segments (like a Clifford attractor)
  const paths = [];
  for (let i = 0; i < 1000; i++) {
    const x = (i * 7.3) % 200, y = (i * 11.1) % 200;
    paths.push([{ x, y }, { x: x + 1, y: y + 1 }]);
  }
  const result = sortPathsNearest(paths);
  assert.strictEqual(result.length, 1000);
});

test("sortPathsNearest: all points at same location", () => {
  const paths = [
    [{ x: 5, y: 5 }, { x: 5, y: 5 }],
    [{ x: 5, y: 5 }, { x: 5, y: 5 }],
    [{ x: 5, y: 5 }, { x: 5, y: 5 }],
  ];
  const result = sortPathsNearest(paths);
  assert.strictEqual(result.length, 3);
});

// ── mergePaths ──

test("mergePaths: merges close paths", () => {
  const paths = [
    [{ x: 0, y: 0 }, { x: 10, y: 0 }],
    [{ x: 10.5, y: 0 }, { x: 20, y: 0 }],
  ];
  const result = mergePaths(paths, 1.0);
  assert.strictEqual(result.length, 1);
  assert.strictEqual(result[0].length, 4);
});

test("mergePaths: keeps distant paths separate", () => {
  const paths = [
    [{ x: 0, y: 0 }, { x: 10, y: 0 }],
    [{ x: 50, y: 0 }, { x: 60, y: 0 }],
  ];
  const result = mergePaths(paths, 1.0);
  assert.strictEqual(result.length, 2);
});

test("mergePaths: zero gap returns original", () => {
  const paths = [
    [{ x: 0, y: 0 }, { x: 10, y: 0 }],
    [{ x: 10, y: 0 }, { x: 20, y: 0 }],
  ];
  const result = mergePaths(paths, 0);
  assert.strictEqual(result.length, 2);
});

test("mergePaths: single path unchanged", () => {
  const paths = [[{ x: 0, y: 0 }, { x: 10, y: 0 }]];
  const result = mergePaths(paths, 5);
  assert.strictEqual(result.length, 1);
});

// ── optimizePaths ──

test("optimizePaths: runs full pipeline", () => {
  const paths = [
    [{ x: 100, y: 0 }, { x: 105, y: 0 }, { x: 110, y: 0 }],
    [{ x: 0, y: 0 }, { x: 5, y: 0 }, { x: 10, y: 0 }],
    [{ x: 10.5, y: 0 }, { x: 15, y: 0 }],
  ];
  const result = optimizePaths(paths, 0.1, 1.0);
  // Simplification reduces collinear points; sorting reorders; merging joins close paths
  assert.ok(result.length <= paths.length);
});

// ── transformPaths ──

test("transformPaths: applies scale and offset with Y flip", () => {
  const paths = [[{ x: 10, y: 20 }, { x: 30, y: 40 }]];
  const bbox = { minX: 0, minY: 0, maxX: 100, maxY: 100 };
  const result = transformPaths(paths, bbox, 2, 5, 5);
  // x = (10 - 0) * 2 + 5 = 25
  assert.strictEqual(result[0][0].x, 25);
  // y = (100 - (20 - 0)) * 2 + 5 = 80 * 2 + 5 = 165
  assert.strictEqual(result[0][0].y, 165);
});

test("transformPaths: identity transform with zero offset", () => {
  const paths = [[{ x: 50, y: 50 }]];
  const bbox = { minX: 0, minY: 0, maxX: 100, maxY: 100 };
  const result = transformPaths(paths, bbox, 1, 0, 0);
  assert.strictEqual(result[0][0].x, 50);
  // Y flipped: (100 - 50) * 1 + 0 = 50
  assert.strictEqual(result[0][0].y, 50);
});

test("transformPaths: respects bbox offset", () => {
  const paths = [[{ x: 110, y: 210 }]];
  const bbox = { minX: 100, minY: 200, maxX: 200, maxY: 300 };
  const result = transformPaths(paths, bbox, 1, 0, 0);
  // x = (110 - 100) * 1 + 0 = 10
  assert.strictEqual(result[0][0].x, 10);
  // y = (100 - (210 - 200)) * 1 + 0 = 90
  assert.strictEqual(result[0][0].y, 90);
});

// ── computeFitToBed ──

test("computeFitToBed: square SVG on square bed", () => {
  const bbox = { minX: 0, minY: 0, maxX: 100, maxY: 100 };
  const bounds = { minX: 10, minY: 10, maxX: 290, maxY: 290 };
  const fit = computeFitToBed(bbox, bounds);
  assert.ok(fit);
  assert.strictEqual(fit.scale, 2.8); // 280 / 100
  assert.strictEqual(fit.ox, 10);
  assert.strictEqual(fit.oy, 10);
});

test("computeFitToBed: wide SVG constrained by X", () => {
  const bbox = { minX: 0, minY: 0, maxX: 200, maxY: 50 };
  const bounds = { minX: 0, minY: 0, maxX: 100, maxY: 100 };
  const fit = computeFitToBed(bbox, bounds);
  assert.ok(fit);
  assert.strictEqual(fit.scale, 0.5); // 100 / 200
  assert.strictEqual(fit.ox, 0);
  assert.strictEqual(fit.oy, 37.5); // (100 - 50*0.5) / 2
});

test("computeFitToBed: returns null for zero-size bbox", () => {
  const bbox = { minX: 5, minY: 5, maxX: 5, maxY: 10 };
  const bounds = { minX: 0, minY: 0, maxX: 300, maxY: 300 };
  assert.strictEqual(computeFitToBed(bbox, bounds), null);
});

// ── checkBounds ──

test("checkBounds: all within bounds returns null", () => {
  const paths = [[{ x: 10, y: 10 }, { x: 100, y: 100 }]];
  const bounds = { minX: 0, minY: 0, maxX: 300, maxY: 300 };
  assert.strictEqual(checkBounds(paths, bounds), null);
});

test("checkBounds: point outside returns error", () => {
  const paths = [[{ x: -1, y: 10 }]];
  const bounds = { minX: 0, minY: 0, maxX: 300, maxY: 300 };
  const err = checkBounds(paths, bounds);
  assert.ok(err);
  assert.ok(err.includes("Exceeds safe area"));
});

test("checkBounds: point on boundary is within bounds", () => {
  const paths = [[{ x: 0, y: 0 }, { x: 300, y: 300 }]];
  const bounds = { minX: 0, minY: 0, maxX: 300, maxY: 300 };
  assert.strictEqual(checkBounds(paths, bounds), null);
});

// ── generateMoveCommands ──

test("generateMoveCommands: correct G-code sequence", () => {
  const cmds = generateMoveCommands(100, 200, 1500, 2, 300);
  assert.deepStrictEqual(cmds, [
    "G90",
    "G1 Z2 F300",
    "G1 X100 Y200 F1500",
  ]);
});

// ── generateCircleCommands ──

test("generateCircleCommands: correct G-code sequence", () => {
  const cmds = generateCircleCommands(150, 150, 20, 1000, 2, 0, 300);
  assert.deepStrictEqual(cmds, [
    "G90",
    "G1 Z2 F300",
    "G1 X170 Y150 F1000",
    "G1 Z0 F300",
    "G2 X170 Y150 I-20 J0 F1000",
    "G1 Z2 F300",
  ]);
});

// ── generatePlotCommands ──

test("generatePlotCommands: generates correct sequence", () => {
  const paths = [
    [{ x: 10, y: 20 }, { x: 30, y: 40 }, { x: 50, y: 60 }],
  ];
  const cmds = generatePlotCommands(paths, 1500, 2, 0);
  assert.deepStrictEqual(cmds, [
    "G28",
    "G90",
    "G0 Z2",
    "G0 X10.00 Y20.00",
    "G0 Z0",
    "G1 X30.00 Y40.00 F1500",
    "G1 X50.00 Y60.00 F1500",
    "G0 Z2",
  ]);
});

test("generatePlotCommands: skips single-point paths", () => {
  const paths = [
    [{ x: 5, y: 5 }],
    [{ x: 10, y: 10 }, { x: 20, y: 20 }],
  ];
  const cmds = generatePlotCommands(paths, 1000, 3, 0);
  // Header (3) + one valid path (travel + pen down + 1 draw + pen up = 4) = 7
  assert.strictEqual(cmds.length, 7);
});

test("generatePlotCommands: multiple paths", () => {
  const paths = [
    [{ x: 0, y: 0 }, { x: 10, y: 0 }],
    [{ x: 20, y: 0 }, { x: 30, y: 0 }],
  ];
  const cmds = generatePlotCommands(paths, 1000, 2, 0);
  // Header (3) + path1 (4) + path2 (4) = 11
  assert.strictEqual(cmds.length, 11);
  // Each path should have a pen-up at the end
  assert.strictEqual(cmds[6], "G0 Z2");
  assert.strictEqual(cmds[10], "G0 Z2");
});

// ── generatePlotCommandsWithMap ──

test("generatePlotCommandsWithMap: cmds match generatePlotCommands", () => {
  const paths = [
    [{ x: 10, y: 20 }, { x: 30, y: 40 }, { x: 50, y: 60 }],
  ];
  const cmds = generatePlotCommands(paths, 1500, 2, 0);
  const result = generatePlotCommandsWithMap(paths, 1500, 2, 0);
  assert.deepStrictEqual(result.cmds, cmds);
});

test("generatePlotCommandsWithMap: map length matches cmds length", () => {
  const paths = [
    [{ x: 0, y: 0 }, { x: 10, y: 0 }],
    [{ x: 20, y: 0 }, { x: 30, y: 0 }],
  ];
  const { cmds, map } = generatePlotCommandsWithMap(paths, 1000, 2, 0);
  assert.strictEqual(map.length, cmds.length);
});

test("generatePlotCommandsWithMap: header commands map to null", () => {
  const paths = [[{ x: 0, y: 0 }, { x: 10, y: 0 }]];
  const { map } = generatePlotCommandsWithMap(paths, 1000, 2, 0);
  assert.strictEqual(map[0], null); // G28
  assert.strictEqual(map[1], null); // G90
  assert.strictEqual(map[2], null); // G0 Z2
});

test("generatePlotCommandsWithMap: draw commands have correct types", () => {
  const paths = [[{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 20, y: 0 }]];
  const { map } = generatePlotCommandsWithMap(paths, 1000, 2, 0);
  // After 3 header cmds: travel, pendown, draw, draw, penup
  assert.strictEqual(map[3].type, "travel");
  assert.strictEqual(map[3].pathIndex, 0);
  assert.strictEqual(map[4].type, "pendown");
  assert.strictEqual(map[5].type, "draw");
  assert.strictEqual(map[5].pointIndex, 1);
  assert.strictEqual(map[6].type, "draw");
  assert.strictEqual(map[6].pointIndex, 2);
  assert.strictEqual(map[7].type, "penup");
});

test("generatePlotCommandsWithMap: multiple paths have correct pathIndex", () => {
  const paths = [
    [{ x: 0, y: 0 }, { x: 10, y: 0 }],
    [{ x: 20, y: 0 }, { x: 30, y: 0 }],
  ];
  const { map } = generatePlotCommandsWithMap(paths, 1000, 2, 0);
  const path0Entries = map.filter(m => m && m.pathIndex === 0);
  const path1Entries = map.filter(m => m && m.pathIndex === 1);
  assert.ok(path0Entries.length > 0);
  assert.ok(path1Entries.length > 0);
});

test("generatePlotCommandsWithMap: skips single-point paths", () => {
  const paths = [
    [{ x: 5, y: 5 }],
    [{ x: 10, y: 10 }, { x: 20, y: 20 }],
  ];
  const { map } = generatePlotCommandsWithMap(paths, 1000, 2, 0);
  // Only path index 1 should appear (single-point path skipped)
  const pathIndices = map.filter(m => m).map(m => m.pathIndex);
  assert.ok(!pathIndices.includes(0));
  assert.ok(pathIndices.includes(1));
});

// ── elementToPathD ──

test("elementToPathD: line element", () => {
  const attrs = (n) => ({ x1: "10", y1: "20", x2: "30", y2: "40" }[n]);
  assert.strictEqual(elementToPathD("line", attrs), "M10,20 L30,40");
});

test("elementToPathD: rect element", () => {
  const attrs = (n) => ({ x: "10", y: "20", width: "80", height: "60" }[n]);
  const d = elementToPathD("rect", attrs);
  assert.ok(d.startsWith("M10,20"));
  assert.ok(d.includes("L90,20"));
  assert.ok(d.includes("L90,80"));
  assert.ok(d.includes("L10,80"));
  assert.ok(d.endsWith("Z"));
});

test("elementToPathD: circle element", () => {
  const attrs = (n) => ({ cx: "50", cy: "60", r: "25" }[n]);
  const d = elementToPathD("circle", attrs);
  assert.ok(d.startsWith("M75,60"));
  assert.ok(d.includes("A25,25"));
});

test("elementToPathD: ellipse element", () => {
  const attrs = (n) => ({ cx: "50", cy: "60", rx: "30", ry: "20" }[n]);
  const d = elementToPathD("ellipse", attrs);
  assert.ok(d.startsWith("M80,60"));
  assert.ok(d.includes("A30,20"));
});

test("elementToPathD: polyline element", () => {
  const attrs = (n) => ({ points: "10,20 30,40 50,60" }[n]);
  const d = elementToPathD("polyline", attrs);
  assert.ok(d.startsWith("M10,20"));
  assert.ok(d.includes("L30,40"));
  assert.ok(!d.includes("Z"));
});

test("elementToPathD: polygon closes path", () => {
  const attrs = (n) => ({ points: "0,0 100,0 50,100" }[n]);
  const d = elementToPathD("polygon", attrs);
  assert.ok(d.endsWith("Z"));
});

test("elementToPathD: unknown element returns null", () => {
  assert.strictEqual(elementToPathD("text", () => null), null);
});

// ── formatDuration ──

test("formatDuration: zero milliseconds", () => {
  assert.strictEqual(formatDuration(0), "0s");
});

test("formatDuration: seconds only", () => {
  assert.strictEqual(formatDuration(5000), "5s");
});

test("formatDuration: sub-second rounds down", () => {
  assert.strictEqual(formatDuration(999), "0s");
});

test("formatDuration: minutes and seconds", () => {
  assert.strictEqual(formatDuration(65000), "1m 5s");
});

test("formatDuration: exact minutes", () => {
  assert.strictEqual(formatDuration(120000), "2m 0s");
});

test("formatDuration: hours, minutes, seconds", () => {
  assert.strictEqual(formatDuration(3661000), "1h 1m 1s");
});

test("formatDuration: hours with zero minutes", () => {
  assert.strictEqual(formatDuration(3605000), "1h 0m 5s");
});

// ── Report ──

console.log(`\n${passed + failed} tests: ${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
