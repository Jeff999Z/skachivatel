@echo off
chcp 65001 >nul
title Skachivatel build - do not close
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set JAVA_HOME=C:\Program Files\Android\Android Studio\jbr
echo === Android APK
cd android
call gradlew.bat assembleRelease --console=plain > ..\build_android.log 2>&1
echo android exit %errorlevel% >> ..\build_android.log
cd ..
echo === Windows installer
python -u pc\build\build.py > build_pc.log 2>&1
echo pc exit %errorlevel% >> build_pc.log
echo DONE > build_done.flag
