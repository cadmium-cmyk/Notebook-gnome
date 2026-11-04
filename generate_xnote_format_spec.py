#!/usr/bin/env python3
"""
generate_xnote_format_spec.py
Generates the specification for .xnote and .xcanvas file formats,
modeled after generate_format_spec.py for Rnote.
Synchronized with xnotes-android-master DocumentCodec.kt and CanvasCodec.kt.
"""

import os
import json

def get_xnote_types():
    return [
        {
            "name": "XNotePackage",
            "kind": "struct",
            "doc": "Top-level ZIP archive representation of an .xnote notebook or .xcanvas workspace.",
            "elements": [
                {"name": "manifest.json", "rename": "manifest.json", "type": "ManifestDocument", "doc": "JSON metadata and vector stroke content tree."},
                {"name": "flow.xml", "rename": "flow.xml", "type": "Option<FlowXmlDocument>", "doc": "OpenDocument flat XML body text stream for accessibility and layout flow."},
                {"name": "document.pdf", "rename": "document.pdf", "type": "Option<Vec<u8>>", "doc": "Embedded source PDF when annotating or importing a PDF document."},
                {"name": "templates/", "rename": "templates/", "type": "Option<Vec<XTemplateDocument>>", "doc": "Embedded vector page template definitions."},
                {"name": "assets/", "rename": "assets/", "type": "Option<Vec<u8>>", "doc": "Embedded raster images (assets/image-*.png) and PDF renders."},
            ]
        },
        {
            "name": "ManifestDocument",
            "kind": "struct",
            "doc": "Root JSON object stored in manifest.json inside the .xnote package.",
            "elements": [
                {"name": "format", "rename": "format", "type": "FormatType", "doc": "Document format identifier: \"xnote\" for multi-page notebooks, \"xcanvas\" for infinite canvases."},
                {"name": "version", "rename": "version", "type": "u32", "doc": "Manifest schema version number (currently 1)."},
                {"name": "writer", "rename": "writer", "type": "u32", "doc": "Application build / writer version that created or saved this file."},
                {"name": "created", "rename": "created", "type": "String", "doc": "ISO-8601 creation timestamp string (UTC)."},
                {"name": "dpi", "rename": "dpi", "type": "f64", "doc": "Base reference DPI for content coordinate dimensions (default 150.0)."},
                {"name": "has_pdf", "rename": "has_pdf", "type": "bool", "doc": "True if an embedded document.pdf is present in the archive."},
                {"name": "bookmarks", "rename": "bookmarks", "type": "Vec<Bookmark>", "doc": "Array of page bookmarks and table of contents markers."},
                {"name": "pages", "rename": "pages", "type": "Vec<Page>", "doc": "Ordered list of discrete document pages (for .xnote format)."},
                {"name": "style", "rename": "style", "type": "DocumentStyle", "doc": "Default document-wide paper background color, template identifier, and pattern colors."},
            ]
        },
        {
            "name": "FormatType",
            "kind": "enum",
            "doc": "Document format category.",
            "elements": [
                {"name": "xnote", "rename": "xnote", "type": "", "doc": "Multi-page notebook with discrete page boundaries and per-page styling."},
                {"name": "xcanvas", "rename": "xcanvas", "type": "", "doc": "Infinite 2D canvas with unbounded panning, zooming, and dynamic pattern backgrounds."},
            ]
        },
        {
            "name": "DocumentStyle",
            "kind": "struct",
            "doc": "Default visual styling inherited by all pages unless overridden at the page level.",
            "elements": [
                {"name": "page_color", "rename": "page_color", "type": "RgbaColor", "doc": "Default paper background color as [R, G, B, A] (components 0-255). Default [255, 255, 255, 255]."},
                {"name": "template", "rename": "template", "type": "String", "doc": "Identifier or filename of the default background template (e.g. \"lines\", \"grid\", \"dots\", \"isometric\", or xtemplate ID)."},
                {"name": "pattern_color", "rename": "pattern_color", "type": "RgbaColor", "doc": "Color used for primary template pattern lines, dots, or grid rules. Default [0, 56, 183, 95]."},
                {"name": "accent_color", "rename": "accent_color", "type": "Option<RgbaColor>", "doc": "Color used for secondary template accent rules (such as vertical left margin lines). Default [215, 45, 75, 95]."},
            ]
        },
        {
            "name": "Bookmark",
            "kind": "struct",
            "doc": "User navigation bookmark pointing to a specific page index.",
            "elements": [
                {"name": "page", "rename": "page", "type": "usize", "doc": "0-based index of the target page in the document pages array."},
                {"name": "title", "rename": "title", "type": "Option<String>", "doc": "Optional user-defined bookmark name or chapter label."},
            ]
        },
        {
            "name": "Page",
            "kind": "struct",
            "doc": "A single page in an .xnote document with dimensions, background template binding, and content items.",
            "elements": [
                {"name": "width", "rename": "width", "type": "f64", "doc": "Page width in pixels at document DPI (e.g. ~1240.16 px for A4 at 150 DPI)."},
                {"name": "height", "rename": "height", "type": "f64", "doc": "Page height in pixels at document DPI (e.g. ~1753.94 px for A4 at 150 DPI)."},
                {"name": "pdf_page", "rename": "pdf_page", "type": "Option<usize>", "doc": "0-based page index in document.pdf to render as this page's background layer, or null."},
                {"name": "cover", "rename": "cover", "type": "Option<bool>", "doc": "True if this page is rendered and treated as the notebook cover."},
                {"name": "style", "rename": "style", "type": "PageStyle", "doc": "Per-page template and color overrides that take precedence over DocumentStyle."},
                {"name": "items", "rename": "items", "type": "Vec<PageItem>", "doc": "Ordered list of vector, ink, shape, text, and image items rendered on this page (bottom to top)."},
            ]
        },
        {
            "name": "PageStyle",
            "kind": "struct",
            "doc": "Page-specific visual style overrides for background colors and templates.",
            "elements": [
                {"name": "template", "rename": "template", "type": "Option<String>", "doc": "Template ID override for this page, or \"none\" for a blank background."},
                {"name": "page_color", "rename": "page_color", "type": "Option<RgbaColor>", "doc": "Override paper background color for this specific page."},
                {"name": "pattern_color", "rename": "pattern_color", "type": "Option<RgbaColor>", "doc": "Override template rule/pattern color for this specific page."},
                {"name": "accent_color", "rename": "accent_color", "type": "Option<RgbaColor>", "doc": "Override template accent color for this specific page."},
            ]
        },
        {
            "name": "PageItem",
            "kind": "enum",
            "doc": "Discriminant union of graphical items that can be placed on a page or canvas.",
            "elements": [
                {"name": "stroke", "rename": "stroke", "type": "(StrokeItem)", "doc": "Freehand vector ink stroke with variable width, pressure dynamics, or dashed styling."},
                {"name": "text", "rename": "text", "type": "(TextItem)", "doc": "Positioned typography text box with font family, size, weight, and styling."},
                {"name": "image", "rename": "image", "type": "(ImageItem)", "doc": "Embedded raster image (PNG, JPEG, WebP) with rotation and transformation options."},
                {"name": "shape", "rename": "shape", "type": "(ShapeItem)", "doc": "Vector geometric primitive (rectangle, ellipse, line, arrow, triangle, star)."},
                {"name": "connector", "rename": "connector", "type": "(ConnectorItem)", "doc": "Dynamic line connecting shapes with automatic routing and endpoint arrow heads."},
                {"name": "tape", "rename": "tape", "type": "(TapeItem)", "doc": "Study tape overlay item for active recall masking and reveal."},
                {"name": "audio", "rename": "audio", "type": "(AudioItem)", "doc": "Audio clip recorded during notetaking with timestamp sync markers."},
            ]
        },
        {
            "name": "StrokeItem",
            "kind": "struct",
            "doc": "Freehand vector ink stroke recorded from a pen, stylus, or mouse.",
            "elements": [
                {"name": "kind", "rename": "kind", "type": "String", "doc": "Fixed item discriminator: \"stroke\"."},
                {"name": "tool", "rename": "tool", "type": "ToolType", "doc": "Stylus tool type determining rendering model and smoothing dynamics."},
                {"name": "config", "rename": "config", "type": "StrokeConfig", "doc": "Configuration parameters for stroke width, color, and dynamic sensitivities."},
                {"name": "samples", "rename": "samples", "type": "Vec<SamplePoint>", "doc": "Sequential array of recorded stylus coordinate and pressure samples [x, y, pressure]."},
                {"name": "smooth_scale", "rename": "smooth_scale", "type": "Option<f64>", "doc": "Coordinate scale factor used during spline smoothing (defaults to 1.0)."},
                {"name": "points", "rename": "points", "type": "Option<Vec<SamplePoint>>", "doc": "Alternative legacy alias key for samples."},
            ]
        },
        {
            "name": "ToolType",
            "kind": "enum",
            "doc": "Drawing tool identifier defining brush dynamics and rendering behavior.",
            "elements": [
                {"name": "pen", "rename": "pen", "type": "", "doc": "Standard pressure-sensitive ballpoint/gel pen with smooth Bezier contour generation."},
                {"name": "highlighter", "rename": "highlighter", "type": "", "doc": "Translucent flat highlighter stroke rendered with multiply alpha blending."},
                {"name": "calligraphy", "rename": "calligraphy", "type": "", "doc": "Chisel-tip pen modulating thickness based on stroke movement angle."},
                {"name": "dashed", "rename": "dashed", "type": "", "doc": "Uniform-width dashed line stroke with configurable dash length and gap."},
                {"name": "taper", "rename": "taper", "type": "", "doc": "Tapered pen stroke with smooth start and end tip fade."},
                {"name": "speed", "rename": "speed", "type": "", "doc": "Velocity-sensitive pen thinning down at higher drawing speeds."},
                {"name": "pencil", "rename": "pencil", "type": "", "doc": "Textured graphite pencil stroke with grain distribution."},
            ]
        },
        {
            "name": "StrokeConfig",
            "kind": "struct",
            "doc": "Detailed rendering and dynamics parameters for a freehand stroke.",
            "elements": [
                {"name": "rgba", "rename": "rgba", "type": "RgbaColor", "doc": "Stroke color as [R, G, B, A] with components in [0, 255]."},
                {"name": "base_width", "rename": "base_width", "type": "f64", "doc": "Nominal base stroke width in document pixels."},
                {"name": "pressure_enabled", "rename": "pressure_enabled", "type": "bool", "doc": "Whether stylus pressure modulates stroke width."},
                {"name": "pressure_min_factor", "rename": "pressure_min_factor", "type": "Option<f64>", "doc": "Minimum stroke width scale factor at zero pressure (e.g. 0.35)."},
                {"name": "direction_strength", "rename": "direction_strength", "type": "Option<f64>", "doc": "Calligraphic angle influence strength factor (typically 0.78 for calligraphy)."},
                {"name": "speed_strength", "rename": "speed_strength", "type": "Option<f64>", "doc": "Velocity sensitivity factor reducing width at fast speeds (e.g. 0.869)."},
                {"name": "highlighter_alpha", "rename": "highlighter_alpha", "type": "Option<f64>", "doc": "Explicit alpha transparency override for highlighter strokes (e.g. 0.50)."},
                {"name": "dash_length", "rename": "dash_length", "type": "Option<f64>", "doc": "Length of each drawn dash segment in pixels for dashed strokes (e.g. 10.0)."},
                {"name": "dash_gap", "rename": "dash_gap", "type": "Option<f64>", "doc": "Spacing gap between dashes in pixels for dashed strokes (e.g. 8.0)."},
                {"name": "taper_enabled", "rename": "taper_enabled", "type": "Option<bool>", "doc": "Whether the stroke start and end tips are tapered."},
            ]
        },
        {
            "name": "SamplePoint",
            "kind": "struct",
            "doc": "Individual coordinate sample recorded along a stroke trajectory. Serialized as a 3-element array [x, y, pressure].",
            "elements": [
                {"name": "x", "rename": "[0] x", "type": "f64", "doc": "Horizontal coordinate in page pixels."},
                {"name": "y", "rename": "[1] y", "type": "f64", "doc": "Vertical coordinate in page pixels."},
                {"name": "pressure", "rename": "[2] pressure", "type": "f64", "doc": "Normalized stylus tip pressure in range [0.0, 1.0]."},
            ]
        },
        {
            "name": "TextItem",
            "kind": "struct",
            "doc": "Positioned typographical text element on a page or canvas.",
            "elements": [
                {"name": "kind", "rename": "kind", "type": "String", "doc": "Fixed item discriminator: \"text\"."},
                {"name": "text", "rename": "text", "type": "String", "doc": "Plain text UTF-8 string content."},
                {"name": "rect", "rename": "rect", "type": "Option<Rect4>", "doc": "Explicit bounding box [x, y, width, height] in page pixels."},
                {"name": "pos", "rename": "pos", "type": "Option<Point2>", "doc": "Top-left position [x, y] when rect is omitted."},
                {"name": "width", "rename": "width", "type": "Option<f64>", "doc": "Bounding box width in pixels."},
                {"name": "height", "rename": "height", "type": "Option<f64>", "doc": "Bounding box height in pixels."},
                {"name": "font_family", "rename": "font_family", "type": "Option<String>", "doc": "Font family name (e.g. \"Sans\", \"Serif\", \"Monospace\"). Default \"Sans\"."},
                {"name": "font_size", "rename": "font_size", "type": "Option<f64>", "doc": "Font point size in pixels/points. Default 18.0."},
                {"name": "rgba", "rename": "rgba", "type": "Option<RgbaColor>", "doc": "Text color as [R, G, B, A] (0-255). Default [30, 30, 30, 255]."},
                {"name": "bold", "rename": "bold", "type": "Option<bool>", "doc": "Whether the text is rendered with bold font weight."},
                {"name": "italic", "rename": "italic", "type": "Option<bool>", "doc": "Whether the text is rendered with italic slant."},
                {"name": "angle", "rename": "angle", "type": "Option<f64>", "doc": "Clockwise rotation angle in radians around the text center."},
            ]
        },
        {
            "name": "ImageItem",
            "kind": "struct",
            "doc": "Positioned raster image element referencing an asset stored within the ZIP archive.",
            "elements": [
                {"name": "kind", "rename": "kind", "type": "String", "doc": "Fixed item discriminator: \"image\" or \"picture\"."},
                {"name": "asset", "rename": "asset", "type": "String", "doc": "Relative path to image inside archive (e.g. \"assets/image-9b3f.png\"). Also aliased as \"src\"."},
                {"name": "rect", "rename": "rect", "type": "Option<Rect4>", "doc": "Destination rectangle [x, y, width, height] in page coordinates."},
                {"name": "angle", "rename": "angle", "type": "Option<f64>", "doc": "Clockwise rotation angle in radians around the image center."},
                {"name": "flip_h", "rename": "flip_h", "type": "Option<bool>", "doc": "Whether the image is horizontally mirrored across its vertical axis."},
                {"name": "flip_v", "rename": "flip_v", "type": "Option<bool>", "doc": "Whether the image is vertically mirrored across its horizontal axis."},
            ]
        },
        {
            "name": "ShapeItem",
            "kind": "struct",
            "doc": "Vector geometric primitive (rectangle, ellipse, line, arrow, triangle, star).",
            "elements": [
                {"name": "kind", "rename": "kind", "type": "String", "doc": "Fixed item discriminator: \"shape\"."},
                {"name": "shape_type", "rename": "shape_type", "type": "ShapeType", "doc": "Geometric shape category (e.g. \"rectangle\", \"ellipse\", \"line\", \"arrow\")."},
                {"name": "rect", "rename": "rect", "type": "Rect4", "doc": "Bounding box [x, y, width, height] in page coordinates."},
                {"name": "stroke_width", "rename": "stroke_width", "type": "f64", "doc": "Outline stroke thickness in pixels (default 3.5)."},
                {"name": "rgba", "rename": "rgba", "type": "RgbaColor", "doc": "Stroke outline color as [R, G, B, A] (0-255)."},
                {"name": "fill", "rename": "fill", "type": "bool", "doc": "Whether the interior of the shape is filled."},
                {"name": "fill_solid", "rename": "fill_solid", "type": "Option<bool>", "doc": "True for 100% opaque fill; false for tinted 22% fill."},
                {"name": "angle", "rename": "angle", "type": "Option<f64>", "doc": "Clockwise rotation angle in radians around the shape center."},
                {"name": "start_pt", "rename": "start_pt", "type": "Option<Point2>", "doc": "Starting endpoint coordinate [x, y] for line and arrow shapes."},
                {"name": "end_pt", "rename": "end_pt", "type": "Option<Point2>", "doc": "Terminal endpoint coordinate [x, y] for line and arrow shapes."},
            ]
        },
        {
            "name": "ShapeType",
            "kind": "enum",
            "doc": "Geometric shape type identifier.",
            "elements": [
                {"name": "rectangle", "rename": "rectangle", "type": "", "doc": "Orthogonal rectangle."},
                {"name": "rounded_rect", "rename": "rounded_rect", "type": "", "doc": "Rectangle with rounded corner arcs."},
                {"name": "ellipse", "rename": "ellipse", "type": "", "doc": "Ellipse or circle bounded by rect."},
                {"name": "circle", "rename": "circle", "type": "", "doc": "Constrained symmetrical circle."},
                {"name": "line", "rename": "line", "type": "", "doc": "Straight single line segment from start_pt to end_pt."},
                {"name": "arrow", "rename": "arrow", "type": "", "doc": "Directional arrow with filled arrowhead tip at end_pt."},
                {"name": "triangle", "rename": "triangle", "type": "", "doc": "Isosceles or equilateral triangle."},
                {"name": "star", "rename": "star", "type": "", "doc": "5-point vector star."},
            ]
        },
        {
            "name": "ConnectorItem",
            "kind": "struct",
            "doc": "Dynamic connector line linking items or points across a document or canvas.",
            "elements": [
                {"name": "kind", "rename": "kind", "type": "String", "doc": "Fixed item discriminator: \"connector\"."},
                {"name": "start_pt", "rename": "start_pt", "type": "Point2", "doc": "Starting connection coordinate [x, y]."},
                {"name": "end_pt", "rename": "end_pt", "type": "Point2", "doc": "Ending connection coordinate [x, y]."},
                {"name": "routing", "rename": "routing", "type": "String", "doc": "Connector routing path algorithm: \"straight\", \"curved\", or \"elbow\"."},
                {"name": "head", "rename": "head", "type": "String", "doc": "Terminal arrow head style: \"none\", \"arrow\", or \"dot\"."},
                {"name": "rgba", "rename": "rgba", "type": "RgbaColor", "doc": "Connector stroke color [R, G, B, A]."},
                {"name": "stroke_width", "rename": "stroke_width", "type": "f64", "doc": "Connector line width in pixels."},
            ]
        },
        {
            "name": "TapeItem",
            "kind": "struct",
            "doc": "Active-recall revision tape overlaying text or diagrams.",
            "elements": [
                {"name": "kind", "rename": "kind", "type": "String", "doc": "Fixed item discriminator: \"tape\"."},
                {"name": "rect", "rename": "rect", "type": "Rect4", "doc": "Tape coverage rectangle [x, y, width, height]."},
                {"name": "tape_mode", "rename": "tape_mode", "type": "String", "doc": "Tape mask mode: \"mask\" or \"reveal\"."},
                {"name": "tape_pattern", "rename": "tape_pattern", "type": "String", "doc": "Visual tape texture pattern: \"solid\", \"stripes\", or \"dots\"."},
                {"name": "angle", "rename": "angle", "type": "Option<f64>", "doc": "Clockwise rotation angle in radians."},
            ]
        },
        {
            "name": "AudioItem",
            "kind": "struct",
            "doc": "Audio voice note recording synchronized with ink timestamps.",
            "elements": [
                {"name": "kind", "rename": "kind", "type": "String", "doc": "Fixed item discriminator: \"audio\"."},
                {"name": "asset", "rename": "asset", "type": "String", "doc": "Relative path to audio file inside archive (e.g. \"audio/rec-01.m4a\")."},
                {"name": "rect", "rename": "rect", "type": "Rect4", "doc": "Player widget bounding box [x, y, width, height]."},
                {"name": "duration", "rename": "duration", "type": "f64", "doc": "Recording playback duration in seconds."},
            ]
        },
        {
            "name": "RgbaColor",
            "kind": "struct",
            "doc": "Color representation as a 4-element array [R, G, B, A] with components in [0, 255].",
            "elements": [
                {"name": "r", "rename": "[0] red", "type": "u8", "doc": "Red channel [0, 255]."},
                {"name": "g", "rename": "[1] green", "type": "u8", "doc": "Green channel [0, 255]."},
                {"name": "b", "rename": "[2] blue", "type": "u8", "doc": "Blue channel [0, 255]."},
                {"name": "a", "rename": "[3] alpha", "type": "u8", "doc": "Alpha opacity channel [0, 255], where 255 is fully opaque."},
            ]
        },
        {
            "name": "Rect4",
            "kind": "struct",
            "doc": "Bounding box coordinates serialized as a 4-element array [x, y, width, height].",
            "elements": [
                {"name": "x", "rename": "[0] x", "type": "f64", "doc": "Left horizontal coordinate."},
                {"name": "y", "rename": "[1] y", "type": "f64", "doc": "Top vertical coordinate."},
                {"name": "width", "rename": "[2] width", "type": "f64", "doc": "Box width."},
                {"name": "height", "rename": "[3] height", "type": "f64", "doc": "Box height."},
            ]
        },
        {
            "name": "Point2",
            "kind": "struct",
            "doc": "2D coordinate serialized as a 2-element array [x, y].",
            "elements": [
                {"name": "x", "rename": "[0] x", "type": "f64", "doc": "X coordinate."},
                {"name": "y", "rename": "[1] y", "type": "f64", "doc": "Y coordinate."},
            ]
        },
        {
            "name": "XCanvasDocument",
            "kind": "struct",
            "doc": "Root schema structure for infinite canvas documents (.xcanvas).",
            "elements": [
                {"name": "format", "rename": "format", "type": "FormatType", "doc": "Fixed format string: \"xcanvas\"."},
                {"name": "version", "rename": "version", "type": "u32", "doc": "Schema version (1)."},
                {"name": "created", "rename": "created", "type": "String", "doc": "ISO-8601 creation timestamp string."},
                {"name": "dpi", "rename": "dpi", "type": "f64", "doc": "Base reference DPI (typically 150.0)."},
                {"name": "background", "rename": "background", "type": "CanvasBackground", "doc": "Infinite background pattern, color, and grid spacing configuration."},
                {"name": "view", "rename": "view", "type": "CanvasView", "doc": "Initial/last camera viewport offset and zoom level."},
                {"name": "items", "rename": "items", "type": "Vec<PageItem>", "doc": "Unbounded 2D items placed across the infinite canvas."},
            ]
        },
        {
            "name": "CanvasBackground",
            "kind": "struct",
            "doc": "Background paper color and repeating grid pattern configuration for infinite canvases.",
            "elements": [
                {"name": "pattern", "rename": "pattern", "type": "PatternStyle", "doc": "Repeating pattern style (e.g. \"none\", \"dots\", \"grid\", \"lines\", \"isometric\", \"isometric_dots\")."},
                {"name": "pattern_color", "rename": "pattern_color", "type": "RgbaColor", "doc": "Color of grid lines or pattern dots [R, G, B, A]. Default [150, 150, 150, 64]."},
                {"name": "spacing", "rename": "spacing", "type": "f64", "doc": "Grid period spacing in canvas pixels (default 64.0)."},
                {"name": "paper_color", "rename": "paper_color", "type": "RgbaColor", "doc": "Canvas background paper color [R, G, B, A]. Default [255, 255, 255, 255]."},
            ]
        },
        {
            "name": "PatternStyle",
            "kind": "enum",
            "doc": "Procedural grid pattern style for canvases.",
            "elements": [
                {"name": "none", "rename": "none", "type": "", "doc": "Plain solid background with no grid."},
                {"name": "dots", "rename": "dots", "type": "", "doc": "Orthogonal dot grid with period spacing."},
                {"name": "grid", "rename": "grid", "type": "", "doc": "Orthogonal ruled square grid lines."},
                {"name": "lines", "rename": "lines", "type": "", "doc": "Horizontal ruled lines."},
                {"name": "isometric", "rename": "isometric", "type": "", "doc": "Triangular / isometric 60-degree grid for 3D sketching."},
                {"name": "isometric_dots", "rename": "isometric_dots", "type": "", "doc": "Triangular / isometric dot matrix with staggered alternating rows."},
            ]
        },
        {
            "name": "CanvasView",
            "kind": "struct",
            "doc": "Camera viewpoint and zoom settings for infinite canvases.",
            "elements": [
                {"name": "cx", "rename": "cx", "type": "f64", "doc": "Camera center X coordinate in canvas units."},
                {"name": "cy", "rename": "cy", "type": "f64", "doc": "Camera center Y coordinate in canvas units."},
                {"name": "zoom", "rename": "zoom", "type": "f64", "doc": "Camera magnification scale (1.0 = 100%, 2.0 = 200%)."},
            ]
        },
    ]

def generate_xnote_spec_md(out_path):
    types = get_xnote_types()

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# XNote File Format Specification\n\n")
        f.write("The `.xnote` file format is a ZIP archive containing a JSON manifest (`manifest.json`), an optional OpenDocument XML text flow layer (`flow.xml`), an optional embedded PDF document (`document.pdf`), and embedded templates or bitmap assets.\n\n")
        f.write("## Archive Layout\n\n")
        f.write("An `.xnote` or `.xcanvas` file is packaged as a standard deflate-compressed ZIP archive with the following directory structure:\n\n")
        f.write("```text\n")
        f.write("notebook.xnote/\n")
        f.write("├── manifest.json            # Document metadata, page hierarchy, and vector items\n")
        f.write("├── flow.xml                 # (Optional) OpenDocument body text transcription layer\n")
        f.write("├── document.pdf             # (Optional) Embedded source PDF document\n")
        f.write("├── templates/               # (Optional) Embedded custom background templates\n")
        f.write("│   └── <template-id>.xtemplate\n")
        f.write("└── assets/                  # (Optional) Embedded raster images and PDF page renders\n")
        f.write("    ├── image-<hash>.png\n")
        f.write("    └── template-<hash>.xtemplate\n")
        f.write("```\n\n")

        f.write("## Root Structure (`manifest.json`)\n\n")
        f.write("```json\n{\n")
        f.write('  "format": "xnote",\n')
        f.write('  "version": 1,\n')
        f.write('  "writer": 48,\n')
        f.write('  "created": "2026-10-20T11:26:46.054Z",\n')
        f.write('  "dpi": 150,\n')
        f.write('  "has_pdf": false,\n')
        f.write('  "bookmarks": [],\n')
        f.write('  "pages": [\n')
        f.write('    {\n')
        f.write('      "width": 1240.157,\n')
        f.write('      "height": 1753.937,\n')
        f.write('      "pdf_page": null,\n')
        f.write('      "style": { "template": "lines" },\n')
        f.write('      "items": []\n')
        f.write('    }\n')
        f.write('  ],\n')
        f.write('  "style": {\n')
        f.write('    "page_color": [255, 255, 255, 255],\n')
        f.write('    "template": "lines",\n')
        f.write('    "pattern_color": [0, 56, 183, 95]\n')
        f.write('  }\n')
        f.write("}\n```\n\n")

        for t in types:
            f.write(f"## `{t['name']}` ({t['kind']})\n\n")
            if t.get("doc"):
                f.write(f"{t['doc']}\n\n")

            if t["kind"] == "struct":
                f.write("| Field | Type | Description |\n")
                f.write("|---|---|---|\n")
                for el in t["elements"]:
                    f.write(f"| `{el['rename']}` | `{el['type']}` | {el['doc']} |\n")
            elif t["kind"] == "enum":
                f.write("| Variant | Type | Description |\n")
                f.write("|---|---|---|\n")
                for el in t["elements"]:
                    f.write(f"| `{el['rename']}` | `{el['type']}` | {el['doc']} |\n")
            f.write("\n")

def main():
    root_spec = os.path.join(os.path.dirname(__file__), "xnote_file_format_spec.md")
    rnote_spec = os.path.join(os.path.dirname(__file__), "rnote", "xnote_file_format_spec.md")
    generate_xnote_spec_md(root_spec)
    generate_xnote_spec_md(rnote_spec)
    print(f"Generated XNote specification: {root_spec} and {rnote_spec}")

if __name__ == "__main__":
    main()
