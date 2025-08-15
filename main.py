#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import csv
import os
import sys
import time
import subprocess
import socket
from typing import List, Dict, Tuple, Optional
from datetime import datetime, timedelta, timezone

# --- 配置区 ---
class Config:
    """存储所有配置项"""
    # 核心路径
    CFST_DIR = './cfst'
    XRAY_DIR = './xray'

    # 可执行文件路径
    CFST_EXECUTABLE = f'{CFST_DIR}/cfst'
    XRAY_EXECUTABLE = f'{XRAY_DIR}/xray'
    SPEEDTEST_EXECUTABLE = '/usr/local/bin/speedtest'

    # 文件路径
    RESULT_CSV_PATH = f'{CFST_DIR}/result.csv'
    PREIP_TXT_PATH = f'{CFST_DIR}/preip.txt'
    XRAY_CONFIG_PATH = f'{XRAY_DIR}/config.json'
    
    # 临时环境配置
    XRAY_TEMP_CONFIG_PATH = f'{XRAY_DIR}/temp_xray_config.json'
    XRAY_TEMP_LOG_PATH = '/tmp/xray_temp_test.log'
    XRAY_MAIN_LOG_PATH = '/tmp/xray.log'
    
    # 端口配置
    DEFAULT_TEMP_SOCKS_PORT = 20808
    DEFAULT_TEMP_HTTP_PORT = 20809

    # 测试参数
    MIN_HAIXUAN_IPS = 50
    TOP_N_CANDIDATES = 10
    SPEED_TEST_RETRIES = 3 # Speedtest测速重试次数
    MAX_JINGXUAN_CANDIDATES = 100 # 海选后进入精选的最大IP数量
    
    # 性能提升阈值
    MIN_IMPROVEMENT_THRESHOLD = 1.0  # Mbit/s
    MIN_IMPROVEMENT_PERCENTAGE = 5.0 # %

    # Speedtest 服务器 ID (将在预检查时动态获取)
    SPEEDTEST_SERVER_ID = None

    # 命令配置 '--single',
    HAIXUAN_COMMAND = ['./cfst', '-httping', '-cfcolo', 'SJC,LAX', '-tll', '161', '-t', '6', '-tl', '190', '-n', '1000', '-dd']
    JINGXUAN_COMMAND = ['./cfst', '-n', '200', '-t', '20', '-tl', '250', '-allip', '-dd', '-f', 'preip.txt']
    SPEEDTEST_LIST_COMMAND = ['/usr/local/bin/speedtest', '--list']
    SPEEDTEST_RUN_COMMAND_SINGLE = ['/usr/local/bin/speedtest', '--json', '--no-upload', '--single', '--timeout', '20']
    SPEEDTEST_RUN_COMMAND_MULTI = ['/usr/local/bin/speedtest', '--json', '--no-upload', '--timeout', '20']

# --- 工具函数 ---

def get_speedtest_server_id(proxy_address: str, city_name: str = "San Jose") -> Optional[str]:
   """通过指定代理执行 speedtest --list 并查找服务器ID"""
   print_info(f"正在通过代理 {proxy_address} 查找 {city_name} 的 Speedtest 服务器 ID...")
   try:
       proxy_env = os.environ.copy()
       proxy_env['HTTP_PROXY'] = proxy_address
       proxy_env['HTTPS_PROXY'] = proxy_address

       process = subprocess.run(
           Config.SPEEDTEST_LIST_COMMAND,
           capture_output=True,
           text=True,
           timeout=60,
           env=proxy_env
       )
       
       if process.returncode != 0 and "unable to retrieve" in process.stderr.lower():
            print_warning(f"Speedtest --list 可能失败，尝试解析已有输出。错误: {process.stderr.strip()}")

       lines = process.stdout.strip().split('\n')
       for line in lines:
           if city_name in line:
               parts = line.strip().split(')')
               if len(parts) > 0 and parts[0].isdigit():
                   server_id = parts[0]
                   print_success(f"成功找到服务器 ID: {server_id} ({line.strip()})")
                   return server_id
       
       print_error(f"未能在列表中找到位于 {city_name} 的服务器。", exit_script=True)
       return None
   except FileNotFoundError:
       print_error(f"命令未找到: {Config.SPEEDTEST_EXECUTABLE}", exit_script=True)
   except subprocess.TimeoutExpired:
       print_error("查找 Speedtest 服务器列表超时！", exit_script=True)
   except Exception as e:
       print_error(f"查找 Speedtest 服务器时发生未知错误: {e}", exit_script=True)
   return None

def print_step(title: str):
    """打印步骤标题"""
    print(f"\n--- {title} ---")

def print_info(message: str):
    """打印参考信息"""
    print(f"  -> {message}")

def print_success(message: str):
    """打印成功信息"""
    print(f"✅ {message}")

def print_warning(message: str):
    """打印警告信息"""
    print(f"⚠️ {message}")

def print_error(message: str, exit_script: bool = False):
    """打印错误信息并可选择退出脚本"""
    print(f"❌ {message}")
    if exit_script:
        sys.exit(f"脚本因错误中止。")

def run_command(command: List[str], cwd: str, timeout: int = None, env: Optional[Dict] = None) -> subprocess.CompletedProcess:
    """统一的子进程执行函数"""
    print_info(f"执行命令: {' '.join(command)}")
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=True  # 如果返回非0状态码则抛出 CalledProcessError
        )
        return result
    except FileNotFoundError:
        print_error(f"命令未找到: {command[0]}", exit_script=True)
    except subprocess.TimeoutExpired:
        print_error(f"命令执行超时: {' '.join(command)}", exit_script=True)
    except subprocess.CalledProcessError as e:
        print_error(f"命令执行失败 (返回码: {e.returncode}): {' '.join(command)}")
        print(f"   错误输出: {e.stderr.strip()}")
        sys.exit("子进程执行失败，中止。")
    except Exception as e:
        print_error(f"执行命令时发生未知错误: {e}", exit_script=True)


def check_port_available(port: int) -> bool:
    """检查端口是否可用"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', port))
        return True
    except OSError:
        return False

def find_available_ports(start_port: int = 20800, count: int = 2) -> List[int]:
    """寻找指定数量的可用端口"""
    available_ports = []
    port = start_port
    while len(available_ports) < count and port < start_port + 100:
        if check_port_available(port):
            available_ports.append(port)
        port += 1
    return available_ports

def cleanup_files(files: List[str]):
    """清理指定的临时文件"""
    print_step("清理临时文件")
    for f in files:
        if os.path.exists(f):
            try:
                os.remove(f)
                print_info(f"已删除: {f}")
            except OSError as e:
                print_warning(f"清理失败 {f}: {e}")

# --- Xray 配置核心函数 ---

def update_xray_config_file(ip_address: str, output_path: str, new_ports: Optional[Tuple[int, int]] = None) -> bool:
    """读取原始Xray配置，更新IP和端口，并写入新文件"""
    try:
        with open(Config.XRAY_CONFIG_PATH, 'r', encoding='utf-8') as f:
            config_data = json.load(f)

        # 更新 outbound IP
        vnext = config_data['outbounds'][0]['settings']['vnext']
        if not vnext:
            raise KeyError("vnext 数组为空")
        vnext[0]['address'] = ip_address

        # 更新 inbound 端口
        if new_ports:
            socks_port, http_port = new_ports
            for inbound in config_data.get('inbounds', []):
                inbound['listen'] = '127.0.0.1'
                if inbound.get('protocol') == 'socks':
                    inbound['port'] = socks_port
                elif inbound.get('protocol') == 'http':
                    inbound['port'] = http_port
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=4, ensure_ascii=False)
        return True
    except (FileNotFoundError, json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        print_error(f"更新配置文件 '{output_path}' 失败: {e}")
        return False

def get_ip_from_config(config_path: str) -> Optional[str]:
    """从配置文件中提取IP地址"""
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
        return config_data['outbounds'][0]['settings']['vnext'][0]['address']
    except Exception as e:
        print_warning(f"无法从 '{config_path}' 读取IP: {e}")
        return None

def get_socks_port_from_config(config_path: str) -> Optional[int]:
    """从配置文件中提取SOCKS端口"""
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
        for inbound in config_data.get('inbounds', []):
            if inbound.get('protocol') == 'socks':
                return inbound.get('port')
        return None
    except Exception as e:
        print_warning(f"无法从 '{config_path}' 读取SOCKS端口: {e}")
        return None

# --- 业务流程函数 ---

def pre_flight_checks() -> Tuple[int, int]:
    """执行预检查，确保环境就绪"""
    print_step("预检查")
    required_files = [Config.CFST_EXECUTABLE, Config.XRAY_EXECUTABLE, Config.XRAY_CONFIG_PATH, Config.SPEEDTEST_EXECUTABLE]
    for path in required_files:
        if not os.path.exists(path):
            print_error(f"关键文件 '{path}' 不存在！", exit_script=True)

    ports = find_available_ports(Config.DEFAULT_TEMP_SOCKS_PORT, 2)
    if len(ports) < 2:
        print_warning(f"预设端口 {Config.DEFAULT_TEMP_SOCKS_PORT}, {Config.DEFAULT_TEMP_HTTP_PORT} 可能被占用，尝试自动查找...")
        ports = find_available_ports(20800, 2)
        if len(ports) < 2:
            print_error("无法找到足够的可用端口。", exit_script=True)
    
    temp_socks_port, temp_http_port = ports
    print_info(f"使用临时端口: SOCKS={temp_socks_port}, HTTP={temp_http_port}")
    print_success("预检查通过")
    return temp_socks_port, temp_http_port

def run_haixuan():
    """步骤1: 大范围延迟测试（海选）"""
    print_step("步骤 1: 【海选】大范围延迟测试")
    cleanup_files([Config.RESULT_CSV_PATH, Config.PREIP_TXT_PATH, Config.XRAY_TEMP_CONFIG_PATH, Config.XRAY_TEMP_LOG_PATH])
    
    run_command(Config.HAIXUAN_COMMAND, cwd=Config.CFST_DIR)

    if not os.path.exists(Config.RESULT_CSV_PATH) or os.path.getsize(Config.RESULT_CSV_PATH) == 0:
        print_error("海选测试未生成有效结果文件 (result.csv)。", exit_script=True)

    # 校验IP数量
    try:
        with open(Config.RESULT_CSV_PATH, mode='r', encoding='utf-8') as infile:
            total_ips_found = sum(1 for row in csv.reader(infile) if row and row[0].strip()) - 1 # 减去标题行
        print_info(f"海选共找到 {total_ips_found} 个 IP。")
        if total_ips_found < Config.MIN_HAIXUAN_IPS:
            print_error(f"海选得到的 IP 数量 ({total_ips_found}) 少于最低要求 ({Config.MIN_HAIXUAN_IPS})。", exit_script=True)
    except Exception as e:
        print_error(f"读取 result.csv 检查 IP 数量时出错: {e}", exit_script=True)

    print_success("海选测试完成并通过校验")

def parse_haixuan_results():
    """步骤2: 解析海选结果并生成 preip.txt"""
    print_step(f"步骤 2: 解析海选结果并生成预选 IP 文件 ({Config.PREIP_TXT_PATH})")
    try:
        with open(Config.RESULT_CSV_PATH, mode='r', encoding='utf-8') as infile:
            reader = csv.reader(infile)
            next(reader)  # 跳过标题
            all_ips = [row[0] for row in reader if row and row[0].strip()]

        total_ips_found = len(all_ips)
        if total_ips_found == 0:
            raise ValueError("未能从 result.csv 中解析出任何 IP。")
        
        print_info(f"海选共发现 {total_ips_found} 个IP。")

        if total_ips_found > Config.MAX_JINGXUAN_CANDIDATES:
            print_info(f"IP数量超过 {Config.MAX_JINGXUAN_CANDIDATES}，将只取前 {Config.MAX_JINGXUAN_CANDIDATES} 个进行精选。")
            ips_to_write = all_ips[:Config.MAX_JINGXUAN_CANDIDATES]
        else:
            ips_to_write = all_ips

        with open(Config.PREIP_TXT_PATH, mode='w', encoding='utf-8') as outfile:
            for ip in ips_to_write:
                outfile.write(f"{ip}\n")

        print_success(f"成功提取 {len(ips_to_write)} 个 IP 并写入到 {Config.PREIP_TXT_PATH}")
    except Exception as e:
        print_error(f"步骤 2 发生错误: {e}", exit_script=True)

def run_jingxuan():
    """步骤3: 基于预选 IP 进行 HTTPing 测试（精选）"""
    print_step("步骤 3: 【精选】对预选 IP 进行更精确的 HTTPing 测试")
    result = run_command(Config.JINGXUAN_COMMAND, cwd=Config.CFST_DIR)
    print(result.stdout)

    if not os.path.exists(Config.RESULT_CSV_PATH) or os.path.getsize(Config.RESULT_CSV_PATH) == 0:
        print_error("精选测试未更新或生成有效的结果文件。", exit_script=True)
    print_success("精选测试完成")

def get_candidate_ips() -> List[str]:
    """步骤4: 读取并验证最终候选 IP 列表"""
    print_step("步骤 4: 读取并验证最终候选 IP 列表")
    try:
        with open(Config.RESULT_CSV_PATH, mode='r', encoding='utf-8') as csvfile:
            reader = csv.reader(csvfile)
            next(reader) # 跳过标题
            all_sorted_ips = [row[0] for row in reader if row and row[0].strip()]
        
        if not all_sorted_ips:
            raise ValueError("最终的 result.csv 文件中没有找到任何 IP 数据。")
        
        print_info(f"精选后共找到 {len(all_sorted_ips)} 个有效 IP。")
        candidate_ips = all_sorted_ips[:Config.TOP_N_CANDIDATES]
        print_success(f"成功获取到 {len(candidate_ips)} 个最终候选 IP: {candidate_ips}")
        return candidate_ips
    except Exception as e:
        print_error(f"读取最终候选 IP 时发生错误: {e}", exit_script=True)

def run_speed_test(ip: str, socks_port: int, http_port: int, command_override: Optional[List[str]] = None) -> Dict:
    """对单个IP进行完整的速度测试流程"""
    result = {'ip': ip, 'speed': 0.0, 'server': 'N/A', 'status': 'Unknown'}
    temp_xray_process = None
    log_file_handle = None

    try:
        # 1. 创建临时配置
        if not update_xray_config_file(ip, Config.XRAY_TEMP_CONFIG_PATH, new_ports=(socks_port, http_port)):
            result['status'] = 'Config Failed'
            return result
        print_info(f"临时配置已更新，使用端口 {socks_port}/{http_port}")

        # 2. 启动临时 Xray
        print_info("启动临时 Xray 进程...")
        log_file_handle = open(Config.XRAY_TEMP_LOG_PATH, 'w')
        temp_xray_process = subprocess.Popen(
            [Config.XRAY_EXECUTABLE, "-config", Config.XRAY_TEMP_CONFIG_PATH],
            stdout=log_file_handle, stderr=subprocess.STDOUT
        )
        time.sleep(3)

        if temp_xray_process.poll() is not None:
            result['status'] = 'Xray Start Failed'
            print_error("临时 Xray 启动失败！")
            with open(Config.XRAY_TEMP_LOG_PATH, 'r') as log:
                print(f"   日志尾部: {log.read()[-200:]}")
            return result

        # 3. 执行 Speedtest
        return perform_speedtest(f"socks5://127.0.0.1:{socks_port}", ip, command_override=command_override)

    except Exception as e:
        result['status'] = f'Unexpected Error: {str(e)[:30]}'
        print_warning(f"在 IP: {ip} 的测试过程中发生意外错误: {e}")
        return result
    finally:
        # 4. 清理
        if temp_xray_process and temp_xray_process.poll() is None:
            print_info("停止临时 Xray 进程...")
            temp_xray_process.terminate()
            try:
                temp_xray_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                temp_xray_process.kill()
        if log_file_handle:
            log_file_handle.close()
        
        # 直接清理临时配置文件，避免打印不必要的header
        if os.path.exists(Config.XRAY_TEMP_CONFIG_PATH):
            try:
                os.remove(Config.XRAY_TEMP_CONFIG_PATH)
            except OSError:
                pass # 清理失败则忽略


def perform_speedtest(proxy_address: str, ip: str = "N/A", command_override: Optional[List[str]] = None) -> Dict:
    """执行 speedtest-cli 并解析结果，包含重试逻辑"""
    result = {'ip': ip, 'speed': 0.0, 'server': 'N/A', 'status': 'Unknown'}

    for attempt in range(Config.SPEED_TEST_RETRIES):
        print_info(f"执行 Speedtest (代理: {proxy_address}, 尝试: {attempt + 1}/{Config.SPEED_TEST_RETRIES})...")
        try:
            proxy_env = os.environ.copy()
            proxy_env['HTTP_PROXY'] = proxy_address
            proxy_env['HTTPS_PROXY'] = proxy_address
            base_command = command_override if command_override is not None else Config.SPEEDTEST_RUN_COMMAND_MULTI
            command = base_command.copy()
            if Config.SPEEDTEST_SERVER_ID:
                command.extend(['--server', Config.SPEEDTEST_SERVER_ID])
            
            res = subprocess.run(command, capture_output=True, text=True, timeout=90, env=proxy_env)


            if res.returncode == 0 and res.stdout:
                data = json.loads(res.stdout)
                speed = data.get('download', 0) / 10**6
                server = data.get('server', {})
                server_info = f"{server.get('name', 'N/A')}, {server.get('country', 'N/A')}"
                result.update({'speed': speed, 'server': server_info})
                
                if speed > 0:
                    result['status'] = 'OK'
                    print_success(f"测速成功！服务器: {server_info}, 下载速度: {speed:.2f} Mbit/s")
                    return result # 成功则直接返回
                else:
                    result['status'] = "Result is 0"
                    print_warning(f"Speedtest 返回速度为 0。服务器: {server_info}")
            else:
                result['status'] = "Speedtest Failed"
                print_error(f"Speedtest 执行失败: {res.stderr.strip()[:100] or res.stdout.strip()[:100]}")

        except json.JSONDecodeError:
            result['status'] = "Parse JSON Failed"
            print_error(f"Speedtest 返回的不是有效 JSON: {res.stdout[:100]}")
        except subprocess.TimeoutExpired:
            result['status'] = "Timeout"
            print_error("Speedtest 超时！")
        except Exception as e:
            result['status'] = f"Error: {str(e)[:50]}"
            print_error(f"Speedtest 发生未知错误: {e}")
        
        # 如果不是最后一次尝试，则等待一小段时间再重试
        if attempt < Config.SPEED_TEST_RETRIES - 1:
            print_info("将在3秒后重试...")
            time.sleep(3)

    print_warning(f"IP {ip} 在 {Config.SPEED_TEST_RETRIES} 次尝试后仍未测速成功。")
    return result

def get_baseline_performance() -> Dict:
    """步骤6: 测试当前配置的基准性能"""
    print_step("步骤 6: 【基准测试】对当前配置进行速度测试")
    current_ip = get_ip_from_config(Config.XRAY_CONFIG_PATH)
    if not current_ip:
        print_warning("无法获取当前IP，基准设为0。")
        return {'ip': 'N/A', 'speed': 0.0, 'server': 'N/A', 'status': 'Config Error'}

    socks_port = get_socks_port_from_config(Config.XRAY_CONFIG_PATH)
    if not socks_port:
        print_warning("无法找到当前SOCKS端口，基准设为0。")
        return {'ip': current_ip, 'speed': 0.0, 'server': 'N/A', 'status': 'Config Error'}

    print_info(f"当前配置 IP: {current_ip}, SOCKS 端口: {socks_port}")
    
    result = perform_speedtest(f"socks5://127.0.0.1:{socks_port}", current_ip)

    if result['status'] == 'OK' and result['speed'] > 0:
        print_success(f"当前配置基准速度: {result['speed']:.2f} Mbit/s (服务器: {result['server']})")
    else:
        print_warning(f"当前配置在 {Config.SPEED_TEST_RETRIES} 次尝试后测速失败 (最终状态: {result['status']})。")
        print_info("基准速度将视为 0 Mbit/s，任何有效的候选 IP 都将被视为性能提升。")
        result['speed'] = 0.0
        
    return result

def analyze_and_decide(speed_results: List[Dict], baseline: Dict):
    """步骤7 & 8: 分析结果、决定是否更新，并将结果存档"""
    print_step("步骤 7 & 8: 分析结果、决策、更新配置并存档")
    if not speed_results:
        print_error("没有任何测试成功完成，无法选择最佳 IP。", exit_script=True)

    sorted_results = sorted(speed_results, key=lambda x: x['speed'], reverse=True)
    best_result = sorted_results[0]
    
    # --- 新增：获取上海时间 ---
    shanghai_tz = timezone(timedelta(hours=8))
    now_shanghai_str = datetime.now(shanghai_tz).strftime('%Y-%m-%d %H:%M:%S')

    # --- 修改：打印新的排行榜表格 ---
    print("\n【测速结果排行榜】")
    table_width = 113
    print("-" * table_width)
    print(f"{'RANK':<6}{'IP ADDR':<18}{'Mbit/s':<12}{'SERVER':<40}{'STATUS':<15}{'TIME':<20}")
    print("-" * table_width)
    print(f"{'now':<6}{baseline['ip']:<18}{baseline['speed']:<12.2f}{baseline['server']:<40}{baseline['status']:<15}{now_shanghai_str:<20}")
    print("-" * table_width)
    for i, res in enumerate(sorted_results):
        print(f"{i+1:<6}{res['ip']:<18}{res['speed']:<12.2f}{res['server']:<40}{res['status']:<15}{now_shanghai_str:<20}")
    print("-" * table_width)

    # --- 新增：将结果追加写入 CSV 文件 ---
    csv_log_path = os.path.join(Config.CFST_DIR, 'test.csv')
    print_info(f"正在将本次测速结果追加到日志文件: {csv_log_path}")
    try:
        file_exists = os.path.isfile(csv_log_path)
        with open(csv_log_path, 'a', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            # 如果文件是新创建的，则写入表头
            if not file_exists or os.path.getsize(csv_log_path) == 0:
                writer.writerow(['RANK', 'IP ADDR', 'Mbit/s', 'SERVER', 'STATUS', 'TIME'])
            
            # 写入基准数据
            writer.writerow([
                'now', baseline['ip'], f"{baseline['speed']:.2f}", baseline['server'], baseline['status'], now_shanghai_str
            ])
            # 写入本次候选IP测试数据
            for i, res in enumerate(sorted_results):
                writer.writerow([
                    i+1, res['ip'], f"{res['speed']:.2f}", res['server'], res['status'], now_shanghai_str
                ])
        print_success("测速日志已成功保存。")
    except Exception as e:
        print_warning(f"写入测速日志文件失败: {e}")

    if best_result['speed'] == 0.0:
        print_warning("\n所有候选 IP 的测试速度均为 0，不更新配置。")
        return

    # 决策逻辑
    speed_improvement = best_result['speed'] - baseline['speed']
    improvement_percentage = (speed_improvement / baseline['speed'] * 100) if baseline['speed'] > 0 else float('inf')
    
    print("\n📊 性能对比分析:")
    print(f"   当前基准速度: {baseline['speed']:.2f} Mbit/s")
    print(f"   最佳候选速度: {best_result['speed']:.2f} Mbit/s")
    print(f"   速度提升: {speed_improvement:+.2f} Mbit/s ({improvement_percentage:+.1f}%)")

    should_update = (
        baseline['speed'] == 0.0 or
        speed_improvement >= Config.MIN_IMPROVEMENT_THRESHOLD or
        improvement_percentage >= Config.MIN_IMPROVEMENT_PERCENTAGE
    )

    if should_update:
        print_success(f"决定: 性能有显著提升，更新为最佳 IP: {best_result['ip']}")
        if update_xray_config_file(best_result['ip'], Config.XRAY_CONFIG_PATH):
            print_success("主配置文件更新成功。")
            print("\n💡 提示: 如需应用新配置，请手动重启您的 Xray 服务，或运行以下命令:")
            print(f"   pkill -f {Config.XRAY_EXECUTABLE} && nohup {Config.XRAY_EXECUTABLE} -config {Config.XRAY_CONFIG_PATH} > {Config.XRAY_MAIN_LOG_PATH} 2>&1 &")
        else:
            print_error("最终更新主配置文件失败！")
    else:
        print_info("决定: 性能提升不明显，保持当前配置。")


def setup_and_get_speedtest_server_id(socks_port: int, http_port: int):
   """启动临时Xray，获取Speedtest服务器ID，然后关闭Xray"""
   print_step("初始化：获取 Speedtest 服务器 ID")
   
   current_ip = "rs.meiyoukaoshang.dpdns.org"
   print_info(f"使用固定 IP '{current_ip}' 获取 Speedtest 服务器 ID。")
   if not current_ip:
       print_warning("无法获取当前IP，将跳过服务器ID获取，使用 speedtest 默认服务器。")
       return

   temp_xray_process = None
   log_file_handle = None
   try:
       if not update_xray_config_file(current_ip, Config.XRAY_TEMP_CONFIG_PATH, new_ports=(socks_port, http_port)):
           print_error("为获取服务器ID创建临时配置文件失败。", exit_script=True)
           return

       log_file_handle = open(Config.XRAY_TEMP_LOG_PATH, 'w')
       temp_xray_process = subprocess.Popen(
           [Config.XRAY_EXECUTABLE, "-config", Config.XRAY_TEMP_CONFIG_PATH],
           stdout=log_file_handle, stderr=subprocess.STDOUT
       )
       time.sleep(3)

       if temp_xray_process.poll() is not None:
           print_error("为获取服务器ID启动临时 Xray 失败！", exit_script=True)
           return

       proxy_address = f"socks5://127.0.0.1:{socks_port}"
       Config.SPEEDTEST_SERVER_ID = get_speedtest_server_id(proxy_address)
       
       if not Config.SPEEDTEST_SERVER_ID:
           print_warning("未能通过代理获取指定城市的服务器ID，将使用 speedtest 默认服务器。")

   finally:
       if temp_xray_process and temp_xray_process.poll() is None:
           temp_xray_process.terminate()
           try:
               temp_xray_process.wait(timeout=5)
           except subprocess.TimeoutExpired:
               temp_xray_process.kill()
       if log_file_handle:
           log_file_handle.close()
       if os.path.exists(Config.XRAY_TEMP_CONFIG_PATH):
           os.remove(Config.XRAY_TEMP_CONFIG_PATH)


def main():
   """主执行函数"""
   temp_socks_port, temp_http_port = pre_flight_checks()
   
   # 在所有测试开始前，设置好 Speedtest 服务器
   setup_and_get_speedtest_server_id(temp_socks_port, temp_http_port)
   
   run_haixuan()
   parse_haixuan_results()
   run_jingxuan()
   candidate_ips = get_candidate_ips()

   print_step(f"步骤 5: 【第一阶段】对 {len(candidate_ips)} 个候选 IP 进行单线程速度测试")
   
   single_thread_results = []
   for i, ip in enumerate(candidate_ips):
       print(f"\n  [单线程测试 {i+1}/{len(candidate_ips)}] 正在测试 IP: {ip}")
       result = run_speed_test(ip, temp_socks_port, temp_http_port, command_override=Config.SPEEDTEST_RUN_COMMAND_SINGLE)
       single_thread_results.append(result)

   # 筛选出单线程测速结果的前3名
   sorted_single_results = sorted(single_thread_results, key=lambda x: x['speed'], reverse=True)
   top_3_ips = [res['ip'] for res in sorted_single_results if res['status'] == 'OK' and res['speed'] > 0][:3]

   if not top_3_ips:
       print_warning("单线程测速未能找到有效的前3名IP，将跳过多线程测速。")
       final_results = single_thread_results # 如果没有前3名，则以单线程结果作为最终结果
   else:
       print_step(f"步骤 5: 【第二阶段】对前 {len(top_3_ips)} 名 IP 进行多线程速度测试")
       multi_thread_results = []
       for i, ip in enumerate(top_3_ips):
           print(f"\n  [多线程测试 {i+1}/{len(top_3_ips)}] 正在测试 IP: {ip}")
           result = run_speed_test(ip, temp_socks_port, temp_http_port, command_override=Config.SPEEDTEST_RUN_COMMAND_MULTI)
           multi_thread_results.append(result)
       
       # 将多线程结果合并到最终结果中，并保留所有单线程结果
       final_results = multi_thread_results + [res for res in single_thread_results if res['ip'] not in top_3_ips]

   baseline_performance = get_baseline_performance()
   
   analyze_and_decide(final_results, baseline_performance)

   cleanup_files([Config.XRAY_TEMP_LOG_PATH, Config.PREIP_TXT_PATH])

   print("\n🎉 脚本执行完毕！")


if __name__ == "__main__":
    main()
