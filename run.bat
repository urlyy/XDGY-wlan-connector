@echo off

:: 1. 定义 Conda 路径和环境名称（请根据您的实际安装路径修改）
set CONDA_ROOT=D:/ide_env/Miniconda
set CONDA_ENV_NAME=xdgy_wlan_connector

:: 2. 激活 Conda 环境
call "%CONDA_ROOT%\Scripts\activate.bat" %CONDA_ENV_NAME%

:: 3. 切换到脚本所在目录（确保 config.json 和 main.py 在正确的目录下被找到）
cd /d "%~dp0"

:: 4. 运行 Python 主脚本
python main.py

:: 5. 退出 Conda 环境（可选，但推荐）
call conda deactivate

:: 6. 结束批处理
exit