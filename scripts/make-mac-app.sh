#!/bin/bash
# macOS：建（或更新）~/Applications/HWGlance.app，Mac 版的 make-shortcut.ps1。
# App 指向「執行這支腳本的那份 HWGlance」的 run.sh；之後可以從 Launchpad、Spotlight 打開，或拖到 Dock。
# 搬了 HWGlance 資料夾就重跑一次。圖示是 assets/AppIcon.icns（scripts/make_mac_icon.py 產生）。
#
#   scripts/make-mac-app.sh                建在 ~/Applications（不需要管理員權限）
#   scripts/make-mac-app.sh /Applications  建在別的地方
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST_DIR="${1:-$HOME/Applications}"
APP="$DEST_DIR/HWGlance.app"
ICON="$ROOT/assets/AppIcon.icns"
BUNDLE_ID="com.jacky0788.hwglance"          # 跟 src/hwglance/ui.py 的 MAC_BUNDLE_ID 一樣
VERSION="$(sed -nE 's/^version = "(.*)"/\1/p' "$ROOT/pyproject.toml" | head -1)"

[ -f "$ICON" ] || { echo "缺 $ICON：先執行 .venv/bin/python scripts/make_mac_icon.py"; exit 1; }
chmod +x "$ROOT/run.sh"

# 只換掉自己建的那個（Info.plist 的識別碼對得上），別人同名的 App 不動
if [ -e "$APP" ]; then
    existing="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Contents/Info.plist" 2>/dev/null || true)"
    [ "$existing" = "$BUNDLE_ID" ] || { echo "$APP 已經存在、但不是 HWGlance 建的，沒有動它"; exit 1; }
    rm -rf "$APP"
fi
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$ICON" "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>HWGlance</string>
    <key>CFBundleDisplayName</key><string>HWGlance</string>
    <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
    <key>CFBundleExecutable</key><string>launcher</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
    <key>LSApplicationCategoryType</key><string>public.app-category.utilities</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/launcher" <<LAUNCHER
#!/bin/bash
# 由 scripts/make-mac-app.sh 產生：開 $ROOT 的 HWGlance。資料夾搬了就重跑 make-mac-app.sh。
export HWGLANCE_GUI=1
export HWGLANCE_APP="\$(cd "\$(dirname "\$0")/../.." && pwd)"
exec "$ROOT/run.sh" --wait
LAUNCHER
chmod +x "$APP/Contents/MacOS/launcher"

# 讓 Finder、Launchpad 馬上認得新圖示
touch "$APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" 2>/dev/null || true
echo "已建立 $APP（指向 $ROOT）"
