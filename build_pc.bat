@echo off
chcp 65001 >nul
title Skachivatel PC build - do not close
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python -u pc\build\build.py > build_pc.log 2>&1
echo pc exit %errorlevel% >> build_pc.log
echo DONE > build_done.flag
