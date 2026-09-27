# ═══════════════════════════════════════════════════════════════
# 股票量价分析 — Android APK 打包配置
# ═══════════════════════════════════════════════════════════════
# ⚠️ 构建必须在 Linux / WSL2 / Docker 中进行，Windows 原生不支持 buildozer！
#
#   # Windows 11 上最短构建路径：
#   1. WSL2 + Ubuntu 22.04（管理员 PowerShell 执行：wsl --install）
#   2. Ubuntu 里：
#      sudo apt update && sudo apt install -y openjdk-17-jdk unzip zip python3-pip
#      pip install buildozer python-for-android cython
#      cd /mnt/c/Users/1/WorkBuddy/2026-05-06-task-1/apk_new
#      buildozer android debug   # 首次运行会自动下载 SDK/NDK (~3GB)
#   3. APK 输出在 bin/stockanalyzer-2.3.0-arm64-v8a-debug.apk
#      adb install bin/stockanalyzer-*-debug.apk
#
# ═══════════════════════════════════════════════════════════════

[app]
title = 股票量价分析
package.name = stockanalyzer
package.domain = org.stock
source.dir = .
source.include_exts = py,png,jpg,kv,json,ttf,otf,atlas
version = 2.3.0
requirements = python3,kivy==2.3.1,numpy,pandas,requests,pyjnius
orientation = portrait
fullscreen = 0

# ─── Android SDK/NDK ───
android.api = 33
android.minapi = 21
android.sdk = 24
android.archs = arm64-v8a, armeabi-v7a
android.permissions = INTERNET,ACCESS_NETWORK_STATE
android.accept_sdk_license = True

# ─── Buildozer 选项 ───
[buildozer]
log_level = 2
warn_on_root = 1
clean_build = 1
