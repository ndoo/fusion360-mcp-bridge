# Fusion 360 MCP — Scripting Knowledge

This file is read automatically by Claude at the start of every session.
It encodes hard-won knowledge about the Fusion 360 Python API so that
`fusion_execute` scripts work first time rather than through trial and error.

---

## Architecture

```
Claude  ←→  server.py (MCP, stdio)  ←→  HTTP :7654  ←→  FusionMCPBridge add-in  ←→  Fusion API
```

Two tools:
- **`fusion_execute(script)`** — run arbitrary Python inside Fusion's process
- **`fusion_screenshot(...)`** — capture the active viewport as a base64 PNG

All geometry work goes through `fusion_execute`. Write scripts that `print()` their results back.

---

## Units

Fusion stores all geometry in **centimetres (cm)**. When calling API methods that accept a `ValueInput`, pass strings like `"3cm"`, `"10mm"`, `"45 deg"` — Fusion converts them. Raw floats are always in cm.

---

## Script structure

```python
import adsk.core, adsk.fusion, math

def run(_context):
    comp = design.activeComponent
    # ... work here ...
    print("done")
```

The context provides `adsk`, `app`, `ui`, and `design` (may be `None` if no design is open). Defining `run(_context)` is optional but conventional. Always `print()` results — return values are ignored.

---

## Design mode

Check before scripting anything parametric:

```python
import adsk.fusion
def run(_context):
    mode = design.designType
    if mode == adsk.fusion.DesignTypes.ParametricDesignType:
        print("parametric")
    else:
        print("direct")
```

- **Parametric mode**: features are recorded in the timeline. `TemporaryBRepManager` bodies need a `BaseFeature` wrapper (see below).
- **Direct mode**: operations happen immediately, no timeline. Some parametric APIs are unavailable.

---

## Revolve / profile rules

**The single most common error: profile crossing the revolve axis.**

Fusion's modelling kernel rejects any revolve where the sketch profile has geometry on both sides of the axis — even if the crossing point is just a line endpoint at the origin.

### Rules
1. The profile must lie **entirely on one side** of the revolve axis (x ≥ 0 or x ≤ 0).
2. Profile edges **touching** the axis are fine (the poles of a sphere land on the axis).
3. A closing line that runs **along** the axis is fine.
4. A line that **passes through** the axis (e.g. from `(r,0,0)` to `(-r,0,0)`) is not fine.

### Correct sphere pattern (use this every time)

Revolve on the **XY plane around the Y axis**. Use `addByThreePoints` — it's unambiguous about arc direction.

```python
import adsk.core, adsk.fusion, math

def run(_context):
    comp = design.activeComponent
    r = 3.0  # 3 cm radius → 6 cm diameter

    sk = comp.sketches.add(comp.xYConstructionPlane)

    # Arc: top pole → equator → bottom pole (all x >= 0)
    sk.sketchCurves.sketchArcs.addByThreePoints(
        adsk.core.Point3D.create(0,  r, 0),
        adsk.core.Point3D.create(r,  0, 0),
        adsk.core.Point3D.create(0, -r, 0))

    # Closing line along Y axis (ON the axis — allowed)
    sk.sketchCurves.sketchLines.addByTwoPoints(
        adsk.core.Point3D.create(0, -r, 0),
        adsk.core.Point3D.create(0,  r, 0))

    # Sanity check
    if sk.profiles.count == 0:
        print("Error: no profile formed")
        return

    rev = comp.features.revolveFeatures.createInput(
        sk.profiles.item(0),
        comp.yConstructionAxis,
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    rev.setAngleExtent(False, adsk.core.ValueInput.createByString('360 deg'))
    feat = comp.features.revolveFeatures.add(rev)
    print(f"Sphere created: {feat.name}")
```

### Why not `addByCenterStartSweep`?

`addByCenterStartSweep(center, start, sweep_angle)` sweeps in the sketch plane's "positive" rotation direction. The direction is easy to get wrong, and if the start point is on the axis the profile may fail silently. `addByThreePoints` is always unambiguous.

---

## Profile formation checklist

If `sk.profiles.count == 0` after adding sketch curves:

1. **Open contour** — every curve endpoint must connect to exactly one other curve endpoint. Check for gaps.
2. **Duplicate edge** — adding the same line twice creates a zero-area region. Remove duplicates.
3. **Endpoints on revolve axis** — can sometimes prevent profile closure. Shift the arc to avoid placing both endpoints on the axis, or switch to `addByThreePoints`.
4. **Wrong sketch plane** — verify `comp.xYConstructionPlane` vs `xZConstructionPlane` vs `yZConstructionPlane`.

Always add an explicit check before using `profiles.item(0)`:

```python
if sk.profiles.count == 0:
    print(f"No profile — curves added: {sk.sketchCurves.count}")
    return
```

---

## TemporaryBRepManager pattern (complex shapes)

Use this for polyhedra, convex hulls, and anything that can't be built from a single sketch profile. It constructs BRep topology directly without needing Fusion's feature history.

```python
import adsk.core, adsk.fusion

def run(_context):
    tbm = adsk.fusion.TemporaryBRepManager.get()
    # ... build bodies with tbm ...

    if design.designType == adsk.fusion.DesignTypes.ParametricDesignType:
        # Parametric mode: must wrap in a BaseFeature
        base_feat = design.activeComponent.features.baseFeatures.add()
        base_feat.startEdit()
        try:
            design.activeComponent.bRepBodies.add(body, base_feat)
        finally:
            base_feat.finishEdit()
    else:
        # Direct mode: add directly
        design.activeComponent.bRepBodies.add(body)
```

### Half-space intersection (convex polyhedra)

Efficient way to build any convex solid — intersect half-spaces defined by face planes:

```python
def make_convex_solid(tbm, planes_with_normals):
    """
    planes_with_normals: list of (point_on_plane, outward_normal_vector)
    Returns a BRepBody or None.
    """
    # Start with a large box that definitely contains the solid
    box_input = adsk.core.OrientedBoundingBox3D.create(
        adsk.core.Point3D.create(0, 0, 0),
        adsk.core.Vector3D.create(1, 0, 0),
        adsk.core.Vector3D.create(0, 1, 0),
        200, 200, 200)
    solid = tbm.createBox(box_input)

    for pt, normal in planes_with_normals:
        # Build a very large cutting box on the outside of each face
        # (intersect away the outside half-space)
        plane = adsk.core.Plane.create(pt, normal)
        cutter = tbm.createHalfSpace(plane, pt)  # if available
        # Alternative: use boolean difference with a half-space solid
        tbm.booleanOperation(solid, cutter,
                             adsk.fusion.BooleanTypes.IntersectionBooleanType)
    return solid
```

---

## Boolean operations

`booleanOperation` mutates the target body in-place and takes a **single** `BRepBody` (not a collection):

```python
# Correct
tbm.booleanOperation(
    target_body,
    tool_body,
    adsk.fusion.BooleanTypes.UnionBooleanType)

# Wrong — do not pass ObjectCollection
```

Boolean types: `UnionBooleanType`, `IntersectionBooleanType`, `DifferenceBooleanType`.

---

## Checking document state

Before any operation, it's defensive to confirm a design is open:

```python
def run(_context):
    if design is None:
        print("No active design — open or create a Fusion document first")
        return
    comp = design.activeComponent
    print(f"Design: {design.rootComponent.name}, mode: {design.designType}")
    print(f"Bodies: {comp.bRepBodies.count}, Sketches: {comp.sketches.count}")
```

---

## Common construction planes

```python
comp.xYConstructionPlane   # horizontal (Z up)
comp.xZConstructionPlane   # front face (Y up)
comp.yZConstructionPlane   # side face (X up)
comp.xConstructionAxis     # X axis
comp.yConstructionAxis     # Y axis
comp.zConstructionAxis     # Z axis
```

---

## Viewport screenshots

Use `fusion_screenshot` (the separate tool) rather than scripting it:

```
fusion_screenshot(direction="iso-top-right")
```

Available directions: `current`, `front`, `back`, `left`, `right`, `top`, `bottom`,
`iso-top-right`, `iso-top-left`, `iso-bottom-right`, `iso-bottom-left`.

---

## Keeping this file up to date

### Routine — run after any Fusion update

```
Read CLAUDE.md in this repo.

Then use fusion_execute to probe the running Fusion instance:
  print(adsk.core.Application.get().version)

Check whether any patterns documented in CLAUDE.md produce deprecation warnings
or errors against the current Fusion version. Report findings and update
CLAUDE.md only — no code changes are needed.
```

### Migration — when Autodesk ships native MCP support

```
Autodesk has shipped native MCP support for Fusion 360.

1. Read .mcp.json, mcp-server/server.py, CLAUDE.md, and README.md in this repo.
2. List which of our two tools (fusion_execute, fusion_screenshot) are now
   available natively in Autodesk's MCP, and what their tool names/schemas are.
3. For tools now provided natively:
   - Update .mcp.json to point at Autodesk's MCP server
   - Remove mcp-server/ and fusion-addin/ directories
   - Remove scripts/quickstart-mac.sh
4. Update CLAUDE.md:
   - Remove workarounds for things now handled natively
   - Add any new native tool names or patterns worth knowing
   - Keep all Fusion API knowledge (units, revolve rules, TBrepM, etc.) —
     it is still valid regardless of the transport layer
5. Update README.md to reflect the simplified setup.

Do not delete CLAUDE.md. The scripting knowledge is transport-independent.
```
