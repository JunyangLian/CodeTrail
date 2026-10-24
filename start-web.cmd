@echo off
cd /d "%~dp0"
python -X utf8 -m pico.web --cwd examples/interview-demo --model-timeout 180 --open
pause
