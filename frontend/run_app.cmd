@echo off
rem 研报纠错助手 · 核查台（D 展示层）启动脚本
cd /d %~dp0
if exist ..\.venv\Scripts\python.exe (
  ..\.venv\Scripts\python.exe -m streamlit run app.py
) else (
  python -m streamlit run app.py
)