@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Nexus Bot - NoneBot2
".venv\Scripts\python.exe" bot.py
pause
