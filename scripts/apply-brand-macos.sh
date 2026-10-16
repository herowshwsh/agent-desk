#!/bin/sh
# 把项目本地的 Electron.app 品牌化为 "Agent"（Dock 悬停名 + 内置图标）。
# node_modules 重装后 electron 会被还原，重跑本脚本即可。
set -eu

APP_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
APP="$APP_ROOT/node_modules/electron/dist/Electron.app"
PLIST="$APP/Contents/Info.plist"

[ -d "$APP" ] || { echo "未找到 $APP，先 npm install"; exit 1; }

/usr/libexec/PlistBuddy -c "Set :CFBundleName Agent" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName Agent" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string Agent" "$PLIST"
cp "$APP_ROOT/assets/Agent.icns" "$APP/Contents/Resources/electron.icns"
codesign --force --deep -s - "$APP"

echo "已品牌化：$(/usr/libexec/PlistBuddy -c 'Print :CFBundleName' "$PLIST")"
