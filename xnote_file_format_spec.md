# XNote File Format Specification

The `.xnote` file format is a ZIP archive containing a JSON manifest (`manifest.json`), an optional OpenDocument XML text flow layer (`flow.xml`), an optional embedded PDF document (`document.pdf`), and embedded templates or bitmap assets.

## Archive Layout

An `.xnote` or `.xcanvas` file is packaged as a standard deflate-compressed ZIP archive with the following directory structure:

```text
notebook.xnote/
├── manifest.json            # Document metadata, page hierarchy, and vector items
├── flow.xml                 # (Optional) OpenDocument body text transcription layer
├── document.pdf             # (Optional) Embedded source PDF document
├── templates/               # (Optional) Embedded custom background templates
│   └── <template-id>.xtemplate
└── assets/                  # (Optional) Embedded raster images and PDF page renders
    ├── image-<hash>.png
    └── template-<hash>.xtemplate
```

## Root Structure (`manifest.json`)

```json
{
  "format": "xnote",
  "version": 1,
  "writer": 48,
  "created": "2026-10-20T11:26:46.054Z",
  "dpi": 150,
  "has_pdf": false,
  "bookmarks": [],
  "pages": [
    {
      "width": 1240.157,
      "height": 1753.937,
      "pdf_page": null,
      "style": { "template": "lines" },
      "items": []
    }
  ],
  "style": {
    "page_color": [255, 255, 255, 255],
    "template": "lines",
    "pattern_color": [0, 56, 183, 95]
  }
}
```

## `XNotePackage` (struct)

Top-level ZIP archive representation of an .xnote notebook or .xcanvas workspace.

| Field | Type | Description |
|---|---|---|
| `manifest.json` | `ManifestDocument` | JSON metadata and vector stroke content tree. |
| `flow.xml` | `Option<FlowXmlDocument>` | OpenDocument flat XML body text stream for accessibility and layout flow. |
| `document.pdf` | `Option<Vec<u8>>` | Embedded source PDF when annotating or importing a PDF document. |
| `templates/` | `Option<Vec<XTemplateDocument>>` | Embedded vector page template definitions. |
| `assets/` | `Option<Vec<u8>>` | Embedded raster images (assets/image-*.png) and PDF renders. |

## `ManifestDocument` (struct)

Root JSON object stored in manifest.json inside the .xnote package.

| Field | Type | Description |
|---|---|---|
| `format` | `FormatType` | Document format identifier: "xnote" for multi-page notebooks, "xcanvas" for infinite canvases. |
| `version` | `u32` | Manifest schema version number (currently 1). |
| `writer` | `u32` | Application build / writer version that created or saved this file. |
| `created` | `String` | ISO-8601 creation timestamp string (UTC). |
| `dpi` | `f64` | Base reference DPI for content coordinate dimensions (default 150.0). |
| `has_pdf` | `bool` | True if an embedded document.pdf is present in the archive. |
| `bookmarks` | `Vec<Bookmark>` | Array of page bookmarks and table of contents markers. |
| `pages` | `Vec<Page>` | Ordered list of discrete document pages (for .xnote format). |
| `style` | `DocumentStyle` | Default document-wide paper background color, template identifier, and pattern colors. |

## `FormatType` (enum)

Document format category.

| Variant | Type | Description |
|---|---|---|
| `xnote` | `` | Multi-page notebook with discrete page boundaries and per-page styling. |
| `xcanvas` | `` | Infinite 2D canvas with unbounded panning, zooming, and dynamic pattern backgrounds. |

## `DocumentStyle` (struct)

Default visual styling inherited by all pages unless overridden at the page level.

| Field | Type | Description |
|---|---|---|
| `page_color` | `RgbaColor` | Default paper background color as [R, G, B, A] (components 0-255). Default [255, 255, 255, 255]. |
| `template` | `String` | Identifier or filename of the default background template (e.g. "lines", "grid", "dots", "isometric", or xtemplate ID). |
| `pattern_color` | `RgbaColor` | Color used for primary template pattern lines, dots, or grid rules. Default [0, 56, 183, 95]. |
| `accent_color` | `Option<RgbaColor>` | Color used for secondary template accent rules (such as vertical left margin lines). Default [215, 45, 75, 95]. |

## `Bookmark` (struct)

User navigation bookmark pointing to a specific page index.

| Field | Type | Description |
|---|---|---|
| `page` | `usize` | 0-based index of the target page in the document pages array. |
| `title` | `Option<String>` | Optional user-defined bookmark name or chapter label. |

## `Page` (struct)

A single page in an .xnote document with dimensions, background template binding, and content items.

| Field | Type | Description |
|---|---|---|
| `width` | `f64` | Page width in pixels at document DPI (e.g. ~1240.16 px for A4 at 150 DPI). |
| `height` | `f64` | Page height in pixels at document DPI (e.g. ~1753.94 px for A4 at 150 DPI). |
| `pdf_page` | `Option<usize>` | 0-based page index in document.pdf to render as this page's background layer, or null. |
| `cover` | `Option<bool>` | True if this page is rendered and treated as the notebook cover. |
| `style` | `PageStyle` | Per-page template and color overrides that take precedence over DocumentStyle. |
| `items` | `Vec<PageItem>` | Ordered list of vector, ink, shape, text, and image items rendered on this page (bottom to top). |

## `PageStyle` (struct)

Page-specific visual style overrides for background colors and templates.

| Field | Type | Description |
|---|---|---|
| `template` | `Option<String>` | Template ID override for this page, or "none" for a blank background. |
| `page_color` | `Option<RgbaColor>` | Override paper background color for this specific page. |
| `pattern_color` | `Option<RgbaColor>` | Override template rule/pattern color for this specific page. |
| `accent_color` | `Option<RgbaColor>` | Override template accent color for this specific page. |

## `PageItem` (enum)

Discriminant union of graphical items that can be placed on a page or canvas.

| Variant | Type | Description |
|---|---|---|
| `stroke` | `(StrokeItem)` | Freehand vector ink stroke with variable width, pressure dynamics, or dashed styling. |
| `text` | `(TextItem)` | Positioned typography text box with font family, size, weight, and styling. |
| `image` | `(ImageItem)` | Embedded raster image (PNG, JPEG, WebP) with rotation and transformation options. |
| `shape` | `(ShapeItem)` | Vector geometric primitive (rectangle, ellipse, line, arrow, triangle, star). |
| `connector` | `(ConnectorItem)` | Dynamic line connecting shapes with automatic routing and endpoint arrow heads. |
| `tape` | `(TapeItem)` | Study tape overlay item for active recall masking and reveal. |
| `audio` | `(AudioItem)` | Audio clip recorded during notetaking with timestamp sync markers. |

## `StrokeItem` (struct)

Freehand vector ink stroke recorded from a pen, stylus, or mouse.

| Field | Type | Description |
|---|---|---|
| `kind` | `String` | Fixed item discriminator: "stroke". |
| `tool` | `ToolType` | Stylus tool type determining rendering model and smoothing dynamics. |
| `config` | `StrokeConfig` | Configuration parameters for stroke width, color, and dynamic sensitivities. |
| `samples` | `Vec<SamplePoint>` | Sequential array of recorded stylus coordinate and pressure samples [x, y, pressure]. |
| `smooth_scale` | `Option<f64>` | Coordinate scale factor used during spline smoothing (defaults to 1.0). |
| `points` | `Option<Vec<SamplePoint>>` | Alternative legacy alias key for samples. |

## `ToolType` (enum)

Drawing tool identifier defining brush dynamics and rendering behavior.

| Variant | Type | Description |
|---|---|---|
| `pen` | `` | Standard pressure-sensitive ballpoint/gel pen with smooth Bezier contour generation. |
| `highlighter` | `` | Translucent flat highlighter stroke rendered with multiply alpha blending. |
| `calligraphy` | `` | Chisel-tip pen modulating thickness based on stroke movement angle. |
| `dashed` | `` | Uniform-width dashed line stroke with configurable dash length and gap. |
| `taper` | `` | Tapered pen stroke with smooth start and end tip fade. |
| `speed` | `` | Velocity-sensitive pen thinning down at higher drawing speeds. |
| `pencil` | `` | Textured graphite pencil stroke with grain distribution. |

## `StrokeConfig` (struct)

Detailed rendering and dynamics parameters for a freehand stroke.

| Field | Type | Description |
|---|---|---|
| `rgba` | `RgbaColor` | Stroke color as [R, G, B, A] with components in [0, 255]. |
| `base_width` | `f64` | Nominal base stroke width in document pixels. |
| `pressure_enabled` | `bool` | Whether stylus pressure modulates stroke width. |
| `pressure_min_factor` | `Option<f64>` | Minimum stroke width scale factor at zero pressure (e.g. 0.35). |
| `direction_strength` | `Option<f64>` | Calligraphic angle influence strength factor (typically 0.78 for calligraphy). |
| `speed_strength` | `Option<f64>` | Velocity sensitivity factor reducing width at fast speeds (e.g. 0.869). |
| `highlighter_alpha` | `Option<f64>` | Explicit alpha transparency override for highlighter strokes (e.g. 0.50). |
| `dash_length` | `Option<f64>` | Length of each drawn dash segment in pixels for dashed strokes (e.g. 10.0). |
| `dash_gap` | `Option<f64>` | Spacing gap between dashes in pixels for dashed strokes (e.g. 8.0). |
| `taper_enabled` | `Option<bool>` | Whether the stroke start and end tips are tapered. |

## `SamplePoint` (struct)

Individual coordinate sample recorded along a stroke trajectory. Serialized as a 3-element array [x, y, pressure].

| Field | Type | Description |
|---|---|---|
| `[0] x` | `f64` | Horizontal coordinate in page pixels. |
| `[1] y` | `f64` | Vertical coordinate in page pixels. |
| `[2] pressure` | `f64` | Normalized stylus tip pressure in range [0.0, 1.0]. |

## `TextItem` (struct)

Positioned typographical text element on a page or canvas.

| Field | Type | Description |
|---|---|---|
| `kind` | `String` | Fixed item discriminator: "text". |
| `text` | `String` | Plain text UTF-8 string content. |
| `rect` | `Option<Rect4>` | Explicit bounding box [x, y, width, height] in page pixels. |
| `pos` | `Option<Point2>` | Top-left position [x, y] when rect is omitted. |
| `width` | `Option<f64>` | Bounding box width in pixels. |
| `height` | `Option<f64>` | Bounding box height in pixels. |
| `font_family` | `Option<String>` | Font family name (e.g. "Sans", "Serif", "Monospace"). Default "Sans". |
| `font_size` | `Option<f64>` | Font point size in pixels/points. Default 18.0. |
| `rgba` | `Option<RgbaColor>` | Text color as [R, G, B, A] (0-255). Default [30, 30, 30, 255]. |
| `bold` | `Option<bool>` | Whether the text is rendered with bold font weight. |
| `italic` | `Option<bool>` | Whether the text is rendered with italic slant. |
| `angle` | `Option<f64>` | Clockwise rotation angle in radians around the text center. |

## `ImageItem` (struct)

Positioned raster image element referencing an asset stored within the ZIP archive.

| Field | Type | Description |
|---|---|---|
| `kind` | `String` | Fixed item discriminator: "image" or "picture". |
| `asset` | `String` | Relative path to image inside archive (e.g. "assets/image-9b3f.png"). Also aliased as "src". |
| `rect` | `Option<Rect4>` | Destination rectangle [x, y, width, height] in page coordinates. |
| `angle` | `Option<f64>` | Clockwise rotation angle in radians around the image center. |
| `flip_h` | `Option<bool>` | Whether the image is horizontally mirrored across its vertical axis. |
| `flip_v` | `Option<bool>` | Whether the image is vertically mirrored across its horizontal axis. |

## `ShapeItem` (struct)

Vector geometric primitive (rectangle, ellipse, line, arrow, triangle, star).

| Field | Type | Description |
|---|---|---|
| `kind` | `String` | Fixed item discriminator: "shape". |
| `shape_type` | `ShapeType` | Geometric shape category (e.g. "rectangle", "ellipse", "line", "arrow"). |
| `rect` | `Rect4` | Bounding box [x, y, width, height] in page coordinates. |
| `stroke_width` | `f64` | Outline stroke thickness in pixels (default 3.5). |
| `rgba` | `RgbaColor` | Stroke outline color as [R, G, B, A] (0-255). |
| `fill` | `bool` | Whether the interior of the shape is filled. |
| `fill_solid` | `Option<bool>` | True for 100% opaque fill; false for tinted 22% fill. |
| `angle` | `Option<f64>` | Clockwise rotation angle in radians around the shape center. |
| `start_pt` | `Option<Point2>` | Starting endpoint coordinate [x, y] for line and arrow shapes. |
| `end_pt` | `Option<Point2>` | Terminal endpoint coordinate [x, y] for line and arrow shapes. |

## `ShapeType` (enum)

Geometric shape type identifier.

| Variant | Type | Description |
|---|---|---|
| `rectangle` | `` | Orthogonal rectangle. |
| `rounded_rect` | `` | Rectangle with rounded corner arcs. |
| `ellipse` | `` | Ellipse or circle bounded by rect. |
| `circle` | `` | Constrained symmetrical circle. |
| `line` | `` | Straight single line segment from start_pt to end_pt. |
| `arrow` | `` | Directional arrow with filled arrowhead tip at end_pt. |
| `triangle` | `` | Isosceles or equilateral triangle. |
| `star` | `` | 5-point vector star. |

## `ConnectorItem` (struct)

Dynamic connector line linking items or points across a document or canvas.

| Field | Type | Description |
|---|---|---|
| `kind` | `String` | Fixed item discriminator: "connector". |
| `start_pt` | `Point2` | Starting connection coordinate [x, y]. |
| `end_pt` | `Point2` | Ending connection coordinate [x, y]. |
| `routing` | `String` | Connector routing path algorithm: "straight", "curved", or "elbow". |
| `head` | `String` | Terminal arrow head style: "none", "arrow", or "dot". |
| `rgba` | `RgbaColor` | Connector stroke color [R, G, B, A]. |
| `stroke_width` | `f64` | Connector line width in pixels. |

## `TapeItem` (struct)

Active-recall revision tape overlaying text or diagrams.

| Field | Type | Description |
|---|---|---|
| `kind` | `String` | Fixed item discriminator: "tape". |
| `rect` | `Rect4` | Tape coverage rectangle [x, y, width, height]. |
| `tape_mode` | `String` | Tape mask mode: "mask" or "reveal". |
| `tape_pattern` | `String` | Visual tape texture pattern: "solid", "stripes", or "dots". |
| `angle` | `Option<f64>` | Clockwise rotation angle in radians. |

## `AudioItem` (struct)

Audio voice note recording synchronized with ink timestamps.

| Field | Type | Description |
|---|---|---|
| `kind` | `String` | Fixed item discriminator: "audio". |
| `asset` | `String` | Relative path to audio file inside archive (e.g. "audio/rec-01.m4a"). |
| `rect` | `Rect4` | Player widget bounding box [x, y, width, height]. |
| `duration` | `f64` | Recording playback duration in seconds. |

## `RgbaColor` (struct)

Color representation as a 4-element array [R, G, B, A] with components in [0, 255].

| Field | Type | Description |
|---|---|---|
| `[0] red` | `u8` | Red channel [0, 255]. |
| `[1] green` | `u8` | Green channel [0, 255]. |
| `[2] blue` | `u8` | Blue channel [0, 255]. |
| `[3] alpha` | `u8` | Alpha opacity channel [0, 255], where 255 is fully opaque. |

## `Rect4` (struct)

Bounding box coordinates serialized as a 4-element array [x, y, width, height].

| Field | Type | Description |
|---|---|---|
| `[0] x` | `f64` | Left horizontal coordinate. |
| `[1] y` | `f64` | Top vertical coordinate. |
| `[2] width` | `f64` | Box width. |
| `[3] height` | `f64` | Box height. |

## `Point2` (struct)

2D coordinate serialized as a 2-element array [x, y].

| Field | Type | Description |
|---|---|---|
| `[0] x` | `f64` | X coordinate. |
| `[1] y` | `f64` | Y coordinate. |

## `XCanvasDocument` (struct)

Root schema structure for infinite canvas documents (.xcanvas).

| Field | Type | Description |
|---|---|---|
| `format` | `FormatType` | Fixed format string: "xcanvas". |
| `version` | `u32` | Schema version (1). |
| `created` | `String` | ISO-8601 creation timestamp string. |
| `dpi` | `f64` | Base reference DPI (typically 150.0). |
| `background` | `CanvasBackground` | Infinite background pattern, color, and grid spacing configuration. |
| `view` | `CanvasView` | Initial/last camera viewport offset and zoom level. |
| `items` | `Vec<PageItem>` | Unbounded 2D items placed across the infinite canvas. |

## `CanvasBackground` (struct)

Background paper color and repeating grid pattern configuration for infinite canvases.

| Field | Type | Description |
|---|---|---|
| `pattern` | `PatternStyle` | Repeating pattern style (e.g. "none", "dots", "grid", "lines", "isometric", "isometric_dots"). |
| `pattern_color` | `RgbaColor` | Color of grid lines or pattern dots [R, G, B, A]. Default [150, 150, 150, 64]. |
| `spacing` | `f64` | Grid period spacing in canvas pixels (default 64.0). |
| `paper_color` | `RgbaColor` | Canvas background paper color [R, G, B, A]. Default [255, 255, 255, 255]. |

## `PatternStyle` (enum)

Procedural grid pattern style for canvases.

| Variant | Type | Description |
|---|---|---|
| `none` | `` | Plain solid background with no grid. |
| `dots` | `` | Orthogonal dot grid with period spacing. |
| `grid` | `` | Orthogonal ruled square grid lines. |
| `lines` | `` | Horizontal ruled lines. |
| `isometric` | `` | Triangular / isometric 60-degree grid for 3D sketching. |
| `isometric_dots` | `` | Triangular / isometric dot matrix with staggered alternating rows. |

## `CanvasView` (struct)

Camera viewpoint and zoom settings for infinite canvases.

| Field | Type | Description |
|---|---|---|
| `cx` | `f64` | Camera center X coordinate in canvas units. |
| `cy` | `f64` | Camera center Y coordinate in canvas units. |
| `zoom` | `f64` | Camera magnification scale (1.0 = 100%, 2.0 = 200%). |

