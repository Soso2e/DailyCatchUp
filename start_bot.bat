@echo off
cd /d "%~dp0"
echo Discord Bot を起動しています...
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m discord_bot.bot
) else (
  python -m discord_bot.bot
)
pause
