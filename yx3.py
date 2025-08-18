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
    CFST_DIR = '/mnt/workspace/cfst'
    XRAY_DIR = '/mnt/workspace/xray'

    # 可执行文件路径
    CFST_EXECUTABLE = f'{CFST_DIR}/cfst'
    XRAY_EXECUTABLE = f'{XRAY_DIR}/xray'

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
    MAX_JINGXUAN_CANDIDATES = 100 # 海选后进入精选的最大IP数量
    
    # 两轮测速参数
    ROUND1_CANDIDATES = 10 # 第一轮测速的IP数量
    ROUND1_TEST_COUNT = 5  # 第一轮每个IP的测速次数
    ROUND1_PASSES = 2      # 第一轮测速执行的总遍数
    ROUND2_CANDIDATES = 3  # 第二轮测速的IP数量（从第一轮结果中选出）

    # 性能提升阈值
    MIN_IMPROVEMENT_THRESHOLD = 10.0  # Mbit/s
    MIN_IMPROVEMENT_PERCENTAGE = 12.0 # %
 
    # 调试选项
    SKIP_SELECTION = 1 # 1: 跳过海选和精选, 0: 正常执行
    LOOP_INTERVAL_SECONDS = 60 * 1 # 主循环间隔时间（秒）
 
    # 测速配置
    SPEED_TEST_URL_1M = 'http://192.74.226.78/testfile_1m.bin'
    SPEED_TEST_URL_10M = 'http://192.74.226.78/testfile_30m.bin'
    SPEED_TEST_TIMEOUT_1M = 3   # 1M文件测速超时时间（秒）
    SPEED_TEST_TIMEOUT_10M = 15 # 10M文件测速超时时间（秒）

    # 命令配置
    HAIXUAN_COMMAND = ['./cfst', '-httping', '-cfcolo', 'SJC,LAX', '-tll', '161', '-t', '4', '-tl', '190', '-n', '1000', '-dd']
    JINGXUAN_COMMAND = ['./cfst', '-n', '200', '-t', '20', '-tl', '250', '-allip', '-dd', '-f', 'preip.txt']

# --- 工具函数 ---

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
    required_files = [Config.CFST_EXECUTABLE, Config.XRAY_EXECUTABLE, Config.XRAY_CONFIG_PATH]
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
    # 注意：result.csv 的清理移至 main 函数
    cleanup_files([Config.PREIP_TXT_PATH, Config.XRAY_TEMP_CONFIG_PATH, Config.XRAY_TEMP_LOG_PATH])
    
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
        # 注意：这里不再截取 TOP_N，而是在 main 逻辑中根据需要截取
        print_success(f"成功获取到 {len(all_sorted_ips)} 个最终候选 IP。")
        return all_sorted_ips
    except Exception as e:
        print_error(f"读取最终候选 IP 时发生错误: {e}", exit_script=True)

def run_speed_test(ip: str, socks_port: int, http_port: int, speed_test_url: str, test_count: int, timeout: int) -> Dict:
    """
    对单个IP进行完整的速度测试流程，支持多次测试并计算平均值。
    """
    result = {'ip': ip, 'speed': 0.0, 'server': 'Self-built', 'status': 'Unknown'}
    temp_xray_process = None
    log_file_handle = None

    try:
        # 1. 创建临时配置
        if not update_xray_config_file(ip, Config.XRAY_TEMP_CONFIG_PATH, new_ports=(socks_port, http_port)):
            result['status'] = 'Config Failed'
            return result
        
        # 2. 启动临时 Xray
        log_file_handle = open(Config.XRAY_TEMP_LOG_PATH, 'w')
        temp_xray_process = subprocess.Popen(
            [Config.XRAY_EXECUTABLE, "-config", Config.XRAY_TEMP_CONFIG_PATH],
            stdout=log_file_handle, stderr=subprocess.STDOUT
        )
        time.sleep(1.5) # 等待进程启动和端口监听

        # 验证 Xray 是否成功监听端口
        if check_port_available(socks_port): # 如果端口仍然可用，说明Xray启动失败
            result['status'] = 'Xray Start Failed'
            print_error(f"临时 Xray 启动后，端口 {socks_port} 仍可用，判定为启动失败。")
            temp_xray_process.terminate()
            with open(Config.XRAY_TEMP_LOG_PATH, 'r') as log:
                print(f"   日志尾部: {log.read()[-300:]}")
            return result

        # 3. 执行 curl 测速（多次）
        speeds = []
        final_status = "Unknown"
        successful_tests = 0
        for i in range(test_count):
            print_info(f"  -> 开始第 {i+1}/{test_count} 次测速...")
            test_result = perform_single_curl_speedtest(socks_port, speed_test_url, timeout, ip)
            
            if test_result['status'] == 'OK' and test_result['speed'] > 0:
                speeds.append(test_result['speed'])
                successful_tests += 1
                print_success(f"    第 {i+1} 次成功，速度: {test_result['speed']:.2f} Mbit/s")
            else:
                speeds.append(0.0) # 将失败的测试计为0
                print_warning(f"    第 {i+1} 次失败，状态: {test_result['status']}")
            
            final_status = test_result['status'] # 记录最后一次的状态
            if i < test_count - 1:
                time.sleep(1)

        # 总是基于总测试次数计算平均值
        average_speed = sum(speeds) / test_count
        result['speed'] = average_speed

        if successful_tests > 0:
            result['status'] = 'OK'
            print_success(f"IP {ip} 平均速度: {average_speed:.2f} Mbit/s (基于 {successful_tests}/{test_count} 次成功测试)")
        else:
            result['status'] = f"All Failed ({final_status})"
            # 即使全部失败，也打印平均速度（为0）
            print_error(f"IP {ip} 的 {test_count} 次测速全部失败，平均速度计为 {average_speed:.2f} Mbit/s。")

        return result

    except Exception as e:
        result['status'] = f'Unexpected Error: {str(e)[:30]}'
        print_warning(f"在 IP: {ip} 的测试过程中发生意外错误: {e}")
        return result
    finally:
        # 4. 清理
        if temp_xray_process and temp_xray_process.poll() is None:
            temp_xray_process.terminate()
            try:
                temp_xray_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                temp_xray_process.kill()
        if log_file_handle:
            log_file_handle.close()
        
        if os.path.exists(Config.XRAY_TEMP_CONFIG_PATH):
            try:
                os.remove(Config.XRAY_TEMP_CONFIG_PATH)
            except OSError:
                pass

def perform_single_curl_speedtest(socks_port: int, speed_test_url: str, timeout: int, ip: str = "N/A") -> Dict:
    """执行单次 curl 测速并解析结果"""
    result = {'ip': ip, 'speed': 0.0, 'server': 'Self-built', 'status': 'Unknown'}
    try:
        command = [
            'curl', '-s', '--socks5-hostname', f'127.0.0.1:{socks_port}',
            '-o', '/dev/null', '-w',
            'time_connect=%{time_connect}|time_starttransfer=%{time_starttransfer}|time_total=%{time_total}|size_download=%{size_download}|speed_download=%{speed_download}',
            '--connect-timeout', '5',       # 连接超时统一为5秒
            '--max-time', str(timeout),     # 最大执行时间
            speed_test_url
        ]
        
        # subprocess的超时要略大于curl的，以确保curl有机会自行超时
        res = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 2)

        if res.returncode == 0 and 'time_total=' in res.stdout and 'size_download=' in res.stdout:
            try:
                metrics = dict(part.split('=', 1) for part in res.stdout.strip().split('|'))
                time_total = float(metrics.get('time_total', 0))
                size_download = float(metrics.get('size_download', 0))

                if time_total > 0:
                    speed_bytes_per_sec = size_download / time_total
                    speed_mbits_per_sec = (speed_bytes_per_sec * 8) / (1000 * 1000)
                else:
                    speed_mbits_per_sec = 0
            except (ValueError, ZeroDivisionError):
                speed_mbits_per_sec = 0
            
            result['speed'] = speed_mbits_per_sec
            
            if speed_mbits_per_sec > 0:
                result['status'] = 'OK'
            else:
                result['status'] = "Result is 0"
        else:
            # curl的错误码28是超时
            if res.returncode == 28:
                result['status'] = "Curl Timeout"
            else:
                result['status'] = f"Curl Failed (Code:{res.returncode})"
            
            if res.stderr and res.stderr.strip():
                print_info(f"   curl stderr: {res.stderr.strip()[:100]}")

    except subprocess.TimeoutExpired:
        result['status'] = "Process Timeout" # 区分是curl超时还是整个进程超时
    except Exception as e:
        result['status'] = f"Exception: {type(e).__name__}"
    
    return result

def get_baseline_performance(socks_port: int, http_port: int) -> Dict:
    """步骤6: 测试当前配置的基准性能（使用10M文件）"""
    print_step("步骤 6: 【基准测试】对当前配置进行速度测试 (10M)")
    current_ip = get_ip_from_config(Config.XRAY_CONFIG_PATH)
    if not current_ip:
        print_warning("无法获取当前IP，基准设为0。")
        return {'ip': 'N/A', 'speed': 0.0, 'server': 'Self-built', 'status': 'Config Error'}

    print_info(f"当前配置 IP: {current_ip}")
    
    # 基准测试只进行1次
    result = run_speed_test(current_ip, socks_port, http_port, Config.SPEED_TEST_URL_10M, 1, Config.SPEED_TEST_TIMEOUT_10M)

    if result['status'] == 'OK' and result['speed'] > 0:
        print_success(f"当前配置基准速度: {result['speed']:.2f} Mbit/s")
    else:
        print_warning(f"当前配置测速失败 (最终状态: {result['status']})。")
        print_info("基准速度将视为 0 Mbit/s，任何有效的候选 IP 都将被视为性能提升。")
        result['speed'] = 0.0
    return result

def analyze_and_decide(final_round_results: List[Dict], baseline: Dict):
    """步骤7 & 8: 分析最终轮结果、决定是否更新，并将结果存档"""
    print_step("步骤 7 & 8: 分析结果、决策、更新配置并存档")
    if not final_round_results:
        print_error("没有任何IP完成最终轮测试，无法选择最佳 IP。", exit_script=True)

    # final_round_results 已经是按速度排序好的
    best_result = final_round_results[0]
    
    shanghai_tz = timezone(timedelta(hours=8))
    now_shanghai_str = datetime.now(shanghai_tz).strftime('%Y-%m-%d %H:%M:%S')

    print("\n【最终轮测速结果排行榜 (10M)】")
    table_width = 80
    print("-" * table_width)
    print(f"{'RANK':<6}{'IP ADDR':<18}{'Mbit/s':<12}{'STATUS':<15}{'TIME':<20}")
    print("-" * table_width)
    print(f"{'now':<6}{baseline['ip']:<18}{baseline['speed']:<12.2f}{baseline['status']:<15}{now_shanghai_str:<20}")
    print("-" * table_width)
    for i, res in enumerate(final_round_results):
        print(f"{i+1:<6}{res['ip']:<18}{res['speed']:<12.2f}{res['status']:<15}{now_shanghai_str:<20}")
    print("-" * table_width)

    # 将最终轮结果和基准追加写入 test.csv
    csv_log_path = os.path.join(Config.CFST_DIR, 'test.csv')
    print_info(f"正在将最终轮测速结果追加到日志文件: {csv_log_path}")
    try:
        file_exists = os.path.isfile(csv_log_path)
        with open(csv_log_path, 'a', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            if not file_exists or os.path.getsize(csv_log_path) == 0:
                writer.writerow(['RANK', 'IP ADDR', 'Mbit/s', 'STATUS', 'TIME'])
            
            writer.writerow(['now', baseline['ip'], f"{baseline['speed']:.2f}", baseline['status'], now_shanghai_str])
            for i, res in enumerate(final_round_results):
                writer.writerow([i+1, res['ip'], f"{res['speed']:.2f}", res['status'], now_shanghai_str])
        print_success("测速日志已成功保存。")
    except Exception as e:
        print_warning(f"写入测速日志文件失败: {e}")

    if best_result['speed'] == 0.0:
        print_warning("\n所有最终候选 IP 的测试速度均为 0，不更新配置。")
        return

    # 决策逻辑
    speed_improvement = best_result['speed'] - baseline['speed']
    improvement_percentage = (speed_improvement / baseline['speed'] * 100) if baseline['speed'] > 0 else float('inf')
    
    print("\n📊 性能对比分析:")
    print(f"   当前基准速度: {baseline['speed']:.2f} Mbit/s")
    print(f"   最佳候选速度: {best_result['speed']:.2f} Mbit/s (IP: {best_result['ip']})")
    print(f"   速度提升: {speed_improvement:+.2f} Mbit/s ({improvement_percentage:+.1f}%)")

    should_update = (
        baseline['status'] != 'OK' or
        speed_improvement >= Config.MIN_IMPROVEMENT_THRESHOLD or
        (baseline['speed'] > 0 and improvement_percentage >= Config.MIN_IMPROVEMENT_PERCENTAGE)
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


def main():
    """主执行函数"""
    temp_socks_port, temp_http_port = pre_flight_checks()
    
    while True:
        print_step("开始新一轮的测速流程")

        if Config.SKIP_SELECTION:
            print_info("配置为跳过海选和精选，将直接使用现有的 result.csv 文件。")
            if not os.path.exists(Config.RESULT_CSV_PATH):
                print_error(f"跳过海选失败：result.csv 文件不存在！请先运行一次完整的海选。", exit_script=True)
        else:
            cleanup_files([Config.RESULT_CSV_PATH])
            run_haixuan()
            parse_haixuan_results()
            run_jingxuan()

        all_candidate_ips = get_candidate_ips()

        # --- 第一轮测速 (多遍取优) ---
        print_step(f"步骤 5.1: 【第一轮】对前 {Config.ROUND1_CANDIDATES} 个IP进行 {Config.ROUND1_PASSES} 遍初选 (1M)")
        round1_ips = all_candidate_ips[:Config.ROUND1_CANDIDATES]
        
        # 使用字典来存储每个IP的最佳结果
        round1_best_results = {ip: {'ip': ip, 'speed': 0.0, 'status': 'Not Tested'} for ip in round1_ips}

        # 进行多遍测速
        for pass_num in range(1, Config.ROUND1_PASSES + 1):
            print_info(f"\n--- 开始第一轮测速 (第 {pass_num}/{Config.ROUND1_PASSES} 遍) ---")
            for i, ip in enumerate(round1_ips):
                print(f"\n  [第一轮-第{pass_num}遍 {i+1}/{len(round1_ips)}] 正在测试 IP: {ip}")
                result = run_speed_test(ip, temp_socks_port, temp_http_port, Config.SPEED_TEST_URL_1M, Config.ROUND1_TEST_COUNT, Config.SPEED_TEST_TIMEOUT_1M)
                
                # 如果当前速度更高，则更新该IP的最佳结果
                if result['speed'] > round1_best_results[ip]['speed']:
                    print_info(f"  -> IP {ip} 发现更高速率: {result['speed']:.2f} Mbit/s (原: {round1_best_results[ip]['speed']:.2f} Mbit/s)")
                    round1_best_results[ip] = result
        
        # 从字典中提取最终结果列表
        round1_results = list(round1_best_results.values())

        # 按速度排序，失败的IP（速度为0）会自动排在后面
        sorted_round1 = sorted(round1_results, key=lambda x: x['speed'], reverse=True)

        # 检查是否所有IP都测速失败
        if not any(r['speed'] > 0 for r in sorted_round1):
            print_warning("第一轮测速所有 IP 均失败，跳过本轮更新。")
            time.sleep(5)
            continue
        
        # 选出优胜者
        round2_ips_results = sorted_round1[:Config.ROUND2_CANDIDATES]
        round2_ips = [res['ip'] for res in round2_ips_results]
        print_success(f"第一轮完成，选出 {len(round2_ips)} 个优胜IP进入第二轮: {round2_ips}")

        # --- 第二轮测速 ---
        print_step(f"步骤 5.2: 【第二轮】对前 {len(round2_ips)} 个IP进行决选 (10M)")
        final_results = []
        for i, ip in enumerate(round2_ips):
            print(f"\n  [第二轮 {i+1}/{len(round2_ips)}] 正在测试 IP: {ip}")
            result = run_speed_test(ip, temp_socks_port, temp_http_port, Config.SPEED_TEST_URL_10M, 1, Config.SPEED_TEST_TIMEOUT_10M)
            final_results.append(result)
        
        # 按速度排序，失败的IP（速度为0）会自动排在后面
        sorted_final_results = sorted(final_results, key=lambda x: x['speed'], reverse=True)

        if not any(r['speed'] > 0 for r in sorted_final_results):
            print_warning("第二轮测速所有 IP 均失败，跳过本轮更新。")
            time.sleep(5)
            continue

        # --- 基准测试与决策 ---
        baseline_performance = get_baseline_performance(temp_socks_port, temp_http_port)
        analyze_and_decide(sorted_final_results, baseline_performance)

        cleanup_files([Config.XRAY_TEMP_LOG_PATH, Config.PREIP_TXT_PATH])

        print(f"\n🎉 本轮脚本执行完毕！将在{Config.LOOP_INTERVAL_SECONDS}秒后开始下一轮...")
        time.sleep(Config.LOOP_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n检测到 Ctrl+C，正在优雅地退出脚本...")
        # 在这里可以执行一些最后的清理工作
        cleanup_files([
            Config.XRAY_TEMP_CONFIG_PATH,
            Config.XRAY_TEMP_LOG_PATH,
            Config.PREIP_TXT_PATH
        ])
        print("清理完成，脚本已终止。")
        sys.exit(0)
