@echo off
setlocal
set "ROOT=%~dp0"
set "PYTHON=py -3"
if exist "%ROOT%.venv\Scripts\python.exe" set "PYTHON=%ROOT%.venv\Scripts\python.exe"
set "PYTHONPATH=%ROOT%src"
if not exist "%ROOT%.venv\Lib\site-packages\streamlit" (
  echo 未安装可视化依赖，正在安装 streamlit ...
  %PYTHON% -m pip install --disable-pip-version-check -q streamlit
)
echo 启动后可访问 http://127.0.0.1:8501
%PYTHON% -m streamlit run "%ROOT%app.py" --server.port 8501 --browser.gatherUsageStats false
