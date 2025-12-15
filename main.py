import json
import subprocess
import requests
import logging
import os
import time
import zipfile
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# --- 全局配置 ---
LOG_FILE_NAME = "wlan_connector.log"
LOG_DIR = Path("./logs")
# 日志文件大小阈值 (例如：10MB)
LOG_SIZE_THRESHOLD_BYTES = 10 * 1024 * 1024 
# 保留的压缩包数量 (保留最新的 5 个压缩文件)
MAX_ARCHIVE_FILES = 5


# --- 配置日志记录 ---
def setup_logging():
    """配置应用的日志记录"""
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / LOG_FILE_NAME

    # 创建一个logger
    logger = logging.getLogger('NetworkChecker')
    logger.setLevel(logging.DEBUG)  # 设置最低输出级别

    # 必须先移除可能存在的handlers，防止重复添加（尤其是在日志滚动操作后）
    if logger.handlers:
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)

    # 创建一个handler，用于写入日志文件
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.INFO)  # 文件中记录 INFO 及以上级别

    # 创建一个handler，用于输出到控制台
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO) # 控制台只输出 INFO 及以上级别

    # 定义handler的输出格式
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    # 给logger添加handler
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

LOGGER = setup_logging()

# --- 日志管理功能 (新增) ---

def manage_log_files():
    """
    检查主日志文件大小，如果超过阈值，则将其压缩归档，并清理最旧的压缩包。
    """
    current_log_path = LOG_DIR / LOG_FILE_NAME
    
    # 1. 检查日志文件是否存在
    if not current_log_path.exists():
        return

    # 2. 检查日志文件大小
    if current_log_path.stat().st_size < LOG_SIZE_THRESHOLD_BYTES:
        return

    LOGGER.info("日志文件大小超过 %sMB，开始进行归档压缩。", LOG_SIZE_THRESHOLD_BYTES / 1024 / 1024)
    
    # 3. 归档文件名定义
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    archive_name = LOG_DIR / f"archive_{timestamp}.zip"
    
    # 在操作文件前，必须确保所有 FileHandler 都已关闭，释放文件句柄
    handlers_to_remove = []
    for handler in LOGGER.handlers:
        if isinstance(handler, logging.FileHandler):
            handler.close()
            handlers_to_remove.append(handler)
    
    for handler in handlers_to_remove:
        LOGGER.removeHandler(handler)
    
    try:
        # 4. 创建 ZIP 压缩文件
        with zipfile.ZipFile(archive_name, 'w', zipfile.ZIP_DEFLATED) as zf:
            # arcname 确保在压缩包内文件名仍为 wlan_connector.log
            zf.write(current_log_path, arcname=LOG_FILE_NAME) 
        
        # 5. 删除旧日志文件并重置 FileHandler
        current_log_path.unlink() # 删除旧文件
        
        # 重新初始化 FileHandler，使其指向新的空日志文件
        file_handler = logging.FileHandler(current_log_path, encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        LOGGER.addHandler(file_handler)
        LOGGER.info("日志文件已成功归档到 %s，并已重置。", archive_name.name)

    except Exception as e:
        LOGGER.error("日志归档失败: %s", e, exc_info=True)
        # 如果归档失败，重新添加所有 FileHandler 以便继续记录日志
        setup_logging()
        return

    # 6. 清理旧的压缩包
    try:
        # 找到所有压缩包并按修改时间排序 (最旧的在前面)
        archive_files = sorted(
            [f for f in LOG_DIR.glob("archive_*.zip")], 
            key=os.path.getmtime
        )
        
        # 如果文件数量超过最大保留数，则删除最旧的文件
        if len(archive_files) > MAX_ARCHIVE_FILES:
            files_to_delete = archive_files[:-MAX_ARCHIVE_FILES]
            for f in files_to_delete:
                f.unlink()
                LOGGER.info("已删除最旧日志压缩包: %s", f.name)

    except Exception as e:
        LOGGER.error("清理旧日志压缩包失败: %s", e, exc_info=True)


# --- 网络连接检测 ---

def is_online(timeout: float = 10.0) -> bool:
    """
    通过访问百度检测网络连接状态。
    """
    url = "https://www.baidu.com"
    headers = {"User-Agent": "connectivity-check/1.0"}
    try:
        resp = requests.get(
            url, 
            timeout=timeout,
            # 明确禁止代理，确保测试的是本地直连状态
            proxies={"http": None, "https": None},
            allow_redirects=False, 
            headers=headers
        )
        if resp.status_code in (200, 204):
            LOGGER.info("网络连接状态：在线（HTTP Code %d）", resp.status_code)
            return True
        else:
            LOGGER.warning("ping baidu异常，状态码: %d", resp.status_code)
            return False
    except requests.exceptions.Timeout:
        LOGGER.warning("网络连接检测超时（%s秒）", timeout)
        return False
    except requests.exceptions.ConnectionError as e:
        LOGGER.warning("网络连接错误: %s", e)
        return False
    except Exception as e:
        LOGGER.error("网络检测发生其他错误: %s", e, exc_info=True)
        return False

# --- Windows Wi-Fi 名称获取 ---

def get_windows_wifi():
    """
    获取Windows系统当前连接的Wi-Fi名称
    返回: (bool, msg) 
          bool: True表示成功获取到有效Wi-Fi名称，False表示失败或未连接
          msg: 具体信息（Wi-Fi名称或错误描述）
    """
    # 确保只在 Windows 系统上运行
    if os.name != 'nt':
        LOGGER.warning("非 Windows 系统，跳过 Wi-Fi 名称获取。")
        return (False, "非 Windows 系统")

    command = ['netsh', 'wlan', 'show', 'interfaces']
    try:
        # 尝试使用 UTF-8 编码，这是现代 Windows 系统的趋势
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False, # 不抛出 CalledProcessError
            creationflags=subprocess.CREATE_NO_WINDOW # 隐藏可能出现的控制台窗口
        )
        
        # 如果 returncode 不为 0，或者结果中没有明显的 Wi-Fi 信息，则尝试 cp936（GBK）
        if result.returncode != 0 or ("SSID" not in result.stdout and "无线" not in result.stdout):
            LOGGER.debug("UTF-8 解析失败或无信息，尝试 GBK (cp936)")
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding='cp936',
                errors='replace',
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
        
        output = result.stdout
        
        if result.returncode != 0:
            LOGGER.error("netsh wlan show interfaces 命令执行失败，返回码: %d", result.returncode)
            LOGGER.debug("stderr: %s", result.stderr)
            return (False, "命令执行失败")

        # 检查是否有无线网卡或Wi-Fi是否启用
        if not any(keyword in output for keyword in ["无线", "Wireless", "WLAN", "SSID"]):
            return (False, "未检测到活动的无线网卡或 Wi-Fi 已禁用")
        
        # 解析SSID
        for line in output.split('\n'):
            line = line.strip()
            # 兼容中文和英文的 SSID 字段，并排除 BSSID
            if ('SSID' in line or 'SSID 名称' in line) and 'BSSID' not in line:
                if ':' in line:
                    # 分割并清理 Wi-Fi 名称
                    ssid = line.split(':', 1)[1].strip().strip('"') 
                    if ssid and ssid != '---':
                        return (True, ssid)  # 成功获取Wi-Fi名称
        
        # 判断未连接状态
        if "已断开连接" in output or "disconnected" in output.lower():
            return (False, "未连接 Wi-Fi")
        
        return (False, "无法确定 Wi-Fi 名称或未连接")
        
    except FileNotFoundError:
        LOGGER.error("命令未找到: netsh，请确认是 Windows 系统")
        return (False, "命令未找到，请确认是 Windows 系统")
    except Exception as e:
        LOGGER.error("查询 Wi-Fi 信息出错: %s", str(e), exc_info=True)
        return (False, f"查询出错: {str(e)}")

# --- 网页表单填充与提交 ---

def fill_form_and_submit(url: str, stu_id: str, password: str) -> bool:
    """
    使用 Playwright 启动浏览器并尝试登录。
    返回: True表示登录流程完成（不保证登录成功），False表示过程中遇到致命错误。
    """
    # 将 headless 设置为 True，确保在后台运行任务时不弹出浏览器窗口
    # 如果需要调试，可以改为 False
    HEADLESS_MODE = True 

    try:
        # 使用 context 管理资源，更加安全
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=HEADLESS_MODE)
            page = browser.new_page()
            
            try:
                LOGGER.info("正在访问登录页面: %s", url)
                # 设置一个较长的超时时间，防止网络慢导致失败
                page.goto(url, timeout=30000) 
                # 等待网络空闲，确保页面完全加载
                page.wait_for_load_state('networkidle')

                # 1. 检查是否已经登录（是否存在#logout元素或特定的已登录标识）
                logout_selector = "#logout"
                if page.locator(logout_selector).count() > 0:
                    LOGGER.info("检测到已登录元素（%s），跳过登录步骤。", logout_selector)
                    return True

                # 2. 检查登录元素是否存在
                username_selector = "#username"
                password_selector = "#password"
                button_selector = "#login-account"

                if page.locator(username_selector).count() == 0:
                    LOGGER.error("未找到用户名输入框（%s），页面可能未正确加载或结构已变。", username_selector)
                    return False
                
                # 3. 填充表单
                LOGGER.info("开始填充登录表单...")
                page.fill(username_selector, stu_id)
                page.fill(password_selector, password)
                
                # 4. 点击登录按钮
                LOGGER.info("点击登录按钮...")
                page.click(button_selector)
                
                # 等待一会儿，让登录请求完成
                page.wait_for_timeout(3000) 
                
                LOGGER.info("登录操作流程完成。")
                return True

            except PlaywrightTimeoutError:
                LOGGER.error("Playwright 操作超时（可能页面加载过慢或元素长时间未出现）。")
                return False
            except Exception as e:
                LOGGER.error("Playwright 操作出错: %s", e, exc_info=True)
                return False
            finally:
                # 确保浏览器关闭
                browser.close()
                
    except Exception as e:
        # Playwright 启动失败，可能是环境问题（如缺少浏览器驱动）
        LOGGER.critical("Playwright 启动失败，请检查环境配置（例如运行 `playwright install`）。错误: %s", e, exc_info=True)
        return False

# --- 配置加载 ---

def load_config(config_path: str = 'config.json'):
    """
    从 JSON 配置文件加载学号和密码。
    """
    config_file = Path(config_path)
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        stu_id = data.get("stu_id")
        password = data.get("password")

        if not stu_id or not password:
            LOGGER.error("配置文件中缺少 'stu_id' 或 'password' 字段。")
            return None, None

        LOGGER.info("配置加载成功。")
        return stu_id, password
        
    except FileNotFoundError:
        LOGGER.critical("错误: 配置文件 %s 不存在", config_path)
        return None, None
    except json.JSONDecodeError:
        LOGGER.critical("错误: 配置文件 %s 格式不正确，请检查 JSON 语法。", config_path)
        return None, None
    except Exception as e:
        LOGGER.critical("加载配置时发生未知错误: %s", e, exc_info=True)
        return None, None

# --- 新增功能：记录成功连接日志 ---
def log_successful_connection():
    """
    将成功的网络连接记录到单独的日志文件中。
    """
    success_log_file = LOG_DIR / "successful_connections.log"
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(success_log_file, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] 成功连接上网络\n")

# --- 主函数 ---

def main():
    """主执行逻辑"""
    # 0. 日志管理：在程序开始时检查并处理大日志文件
    manage_log_files()
    
    LOGGER.info("--- 程序启动 ---")
    
    # 1. 检测当前网络状态
    online_status = is_online()
    if online_status:
        LOGGER.info("当前网络状态为在线，无需进行登录操作。程序退出。")
        return

    # 2. 获取 Wi-Fi 名称
    wifi_status, wifi_msg = get_windows_wifi()
    if not wifi_status:
        LOGGER.warning("获取 Wi-Fi 名称失败: %s。不进行联网操作。", wifi_msg)
        return
        
    # 3. 检查 Wi-Fi 是否为目标网络
    target_ssids = ["stu-xdwlan", "xd-wlan"]
    current_ssid = wifi_msg
    
    if current_ssid not in target_ssids:
        LOGGER.info("当前连接 Wi-Fi: %s。非指定 Wi-Fi (%s)，不进行登录操作。", 
                    current_ssid, ", ".join(target_ssids))
        return

    LOGGER.info("当前连接 Wi-Fi: %s。开始尝试登录...", current_ssid)
    
    # 4. 加载配置
    target_url = "http://10.103.100.234/srun_portal_pc?ac_id=1&theme=pro"
    stu_id, password = load_config()
    
    if not stu_id or not password:
        LOGGER.error("未能加载有效的学号或密码，终止登录操作。")
        return
    
    # 5. 执行登录操作
    form_submitted = fill_form_and_submit(target_url, stu_id, password)
    
    if not form_submitted:
        LOGGER.error("登录表单提交流程发生致命错误，终止后续检测。")
        return

    # 6. 再次检测网络状态
    LOGGER.info("再次检测网络状态...")
    final_online_status = is_online()
    
    if final_online_status:
        LOGGER.info("✅ 登录成功，网络已连接。")
        log_successful_connection()
    else:
        LOGGER.error("❌ 登录失败，网络仍未连接。请检查学号、密码或网络认证状态。")
        
    LOGGER.info("--- 程序结束 ---")

if __name__ == "__main__":
    main()