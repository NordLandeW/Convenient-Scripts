#!/bin/bash
set -e

EXT_DIR="$HOME/.local/share/nautilus-python/extensions"
SCRIPT_PATH="$EXT_DIR/vscode-extension.py"

if [[ "$1" == "remove" || "$1" == "-r" ]]; then
    if [ -f "$SCRIPT_PATH" ]; then
        rm "$SCRIPT_PATH"
    fi
    nautilus -q
    exit 0
fi

if ! pacman -Qi python-nautilus &> /dev/null; then
    sudo pacman -S --needed --noconfirm python-nautilus
fi

mkdir -p "$EXT_DIR"

cat > "$SCRIPT_PATH" << 'EOF'
import os
import subprocess
import gi

try:
    gi.require_version('Nautilus', '4.0')
except:
    pass

from gi.repository import Nautilus, GObject

class VSCodeExtension(GObject.GObject, Nautilus.MenuProvider):
    def __init__(self):
        super().__init__()
        self.code_bin = 'code'
        path = os.getenv('PATH')
        if path:
            for p in path.split(os.path.pathsep):
                for cmd in ['code', 'code-oss', 'vscodium']:
                    fp = os.path.join(p, cmd)
                    if os.path.exists(fp) and os.access(fp, os.X_OK):
                        self.code_bin = fp
                        break
                if self.code_bin != 'code': break
        
        lang = os.environ.get('LANG', 'en')
        self.is_zh = lang.startswith('zh')
        self.label_file = "用 VS Code 打开" if self.is_zh else "Open with VS Code"
        self.label_bg = "在 VS Code 中打开" if self.is_zh else "Open in VS Code"

    def _open(self, files):
        args = [self.code_bin]
        for f in files:
            if hasattr(f, 'get_location') and f.get_location():
                path = f.get_location().get_path()
                if path:
                    args.append(path)
        if len(args) > 1:
            subprocess.Popen(args)

    def menu_activate_cb(self, menu, files):
        self._open(files)

    def menu_background_activate_cb(self, menu, current_folder):
        self._open([current_folder])

    def get_file_items(self, *args):
        files = args[-1]
        item = Nautilus.MenuItem(
            name='VSCodeOpen',
            label=self.label_file,
            icon='com.visualstudio.code'
        )
        item.connect('activate', self.menu_activate_cb, files)
        return [item]

    def get_background_items(self, *args):
        current_folder = args[-1]
        item = Nautilus.MenuItem(
            name='VSCodeOpenBg',
            label=self.label_bg,
            icon='com.visualstudio.code'
        )
        item.connect('activate', self.menu_background_activate_cb, current_folder)
        return [item]
EOF

nautilus -q
echo "VS Code context menu extension installed. Restarting Nautilus..."