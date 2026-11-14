#!/usr/bin/env bash
set -euo pipefail

# Define user local data directories
MIME_DIR="$HOME/.local/share/mime/packages"
ICON_DIR="$HOME/.local/share/icons/hicolor"
APP_DIR="$HOME/.local/share/applications"

echo "==> Creating required local directories..."
mkdir -p "$MIME_DIR"
mkdir -p "$ICON_DIR/scalable/mimetypes"
mkdir -p "$ICON_DIR/48x48/mimetypes"
mkdir -p "$APP_DIR"

echo "==> Generating MIME type XML definitions..."

# 1. application/x-xnote
cat << 'EOF' > "$MIME_DIR/application-x-xnote.xml"
<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-xnote">
    <comment>XNote Document</comment>
    <glob pattern="*.xnote"/>
    <icon name="application-x-xnote"/>
  </mime-type>
</mime-info>
EOF

# 2. application/x-xcanvas
cat << 'EOF' > "$MIME_DIR/application-x-xcanvas.xml"
<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-xcanvas">
    <comment>XCanvas Document</comment>
    <glob pattern="*.xcanvas"/>
    <icon name="application-x-xcanvas"/>
  </mime-type>
</mime-info>
EOF

echo "==> Updating system databases..."
update-mime-database "$HOME/.local/share/mime"
update-desktop-database "$APP_DIR"

if command -v gtk-update-icon-cache &> /dev/null; then
    echo "==> Refreshing GTK icon cache..."
    gtk-update-icon-cache -f -t "$ICON_DIR" || true
fi

echo "==> Done! Custom MIME types registered successfully."
echo "    - application/x-xnote (*.xnote)"
echo "    - application/x-xcanvas (*.xcanvas)"
