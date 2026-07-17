@echo off
title Gap Radar
cd /d "%~dp0"
echo Starting app... browser will open shortly.
echo (Closing this window stops the app)
python -m streamlit run app.py
pause
