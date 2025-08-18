import subprocess
import time
import csv
import os

# --- 配置区 ---

# 工作目录 (请根据实际情况修改)
# 使用 os.path.dirname(__file__) 来获取脚本所在目录，确保路径的相对性
WORK_DIR = "/mnt/workspace/cfst"

# CFST 可执行文件路径
CFST_EXEC = os.path.join(WORK_DIR, "cfst")

# 文件路径
TEMP_CSV_PATH = os.path.join(WORK_DIR, "temp.csv")
RESULT_CSV_PATH = os.path.join(WORK_DIR, "result.csv")
IP_TXT_PATH = os.path.join(WORK_DIR, "temp.txt")
IP_TXT_O_PATH = os.path.join(WORK_DIR, "ip.txt")

# 从每个 CSV 文件中提取的 IP 数量
IP_COUNT_PER_FILE = 50

# 循环之间的延迟时间 (秒)
LOOP_DELAY_SECONDS = 60 * 1  # 10 分钟

# --- CFST 命令 ---

# 第一次测速命令
CMD_PASS_1 = [
    CFST_EXEC,
    "-t", "2",
    "-n", "500",
    "-tl", "180",
    "-dd",
    "-o", TEMP_CSV_PATH,
    "-f", IP_TXT_O_PATH
]

# 第二次测速命令 (基于生成的 IP 列表)
CMD_PASS_2 = [
    CFST_EXEC,
    "-t", "200",
    "-n", "100",
    "-tlr", "0.2",
    "-dd",
    "-o", RESULT_CSV_PATH,
    "-f", IP_TXT_PATH
]

# --- 辅助函数 ---

def extract_ips_from_csv(file_path, count):
    """
    从指定的 CSV 文件中提取第一列的 IP 地址。

    :param file_path: CSV 文件的路径。
    :param count: 需要提取的 IP 地址数量。
    :return: 包含 IP 地址的列表。
    """
    ips = []
    if not os.path.exists(file_path):
        print(f"警告：文件 '{file_path}' 不存在，跳过提取。")
        return ips

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            # 跳过表头
            next(reader, None)
            for i, row in enumerate(reader):
                if i >= count:
                    break
                if row:
                    ips.append(row[0])
    except Exception as e:
        print(f"错误：读取文件 '{file_path}' 时发生错误: {e}")
    
    return ips

import pty

# --- 辅助函数 ---

def run_command_streamed(command):
    """
    使用伪终端 (pty) 执行一个命令，以实现真正的流式输出，支持动画。

    :param command: 要执行的命令列表。
    :return: 进程的退出码。
    """
    print(f"执行命令: {' '.join(command)}")
    
    master_fd, slave_fd = pty.openpty()

    try:
        # 在伪终端环境中启动子进程
        process = subprocess.Popen(
            command,
            stdout=slave_fd,
            stderr=slave_fd,
            stdin=subprocess.DEVNULL, # 关闭标准输入
            close_fds=True
        )
        
        # 子进程不再需要 slave, 在父进程中关闭它
        os.close(slave_fd)

        # 从 master 读取子进程的输出
        while True:
            try:
                # 读取最多 1024 字节
                output = os.read(master_fd, 1024)
            except OSError:
                # 当子进程关闭其 pty slave 端时，会触发 OSError
                break
            
            if not output:
                break
            
            # 直接打印原始字节解码后的字符串
            print(output.decode(errors='ignore'), end='', flush=True)

        return process.wait()

    except FileNotFoundError:
        print(f"\n致命错误：无法找到命令 '{command[0]}'。请确保路径正确且文件存在。")
        return -1
    except Exception as e:
        print(f"\n执行命令时发生未知错误: {e}")
        return -1
    finally:
        # 确保 master 文件描述符被关闭
        os.close(master_fd)

# --- 主逻辑区 ---

def main():
    """
    主函数，包含脚本的核心逻辑。
    """
    print("脚本启动...")
    
    while True:
        print("\n--- 开始新一轮测速 ---")
        
        # --- 步骤 1: 执行第一次测速 ---
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 正在执行第一次测速...")
        
        return_code_1 = run_command_streamed(CMD_PASS_1)
        
        if return_code_1 == 0:
            print("\n第一次测速成功完成。")
        elif return_code_1 == -1:
            # run_command_streamed 内部已打印错误，直接退出
            break
        else:
            print(f"\n错误：第一次测速失败，返回码: {return_code_1}。")
            print(f"将在 {LOOP_DELAY_SECONDS} 秒后重试...")
            time.sleep(LOOP_DELAY_SECONDS)
            continue
        
        # --- 步骤 2: 提取和合并 IP ---
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 开始提取 IP 地址...")
        ips_from_temp = extract_ips_from_csv(TEMP_CSV_PATH, IP_COUNT_PER_FILE)
        ips_from_result = extract_ips_from_csv(RESULT_CSV_PATH, IP_COUNT_PER_FILE)
        
        all_ips = ips_from_temp + ips_from_result
        
        if not all_ips:
            print("警告：未能从任何文件中提取到 IP 地址。跳过第二次测速。")
        else:
            # 去重
            unique_ips = sorted(list(set(all_ips)))
            print(f"共提取到 {len(all_ips)} 个 IP，去重后剩余 {len(unique_ips)} 个。")
            
            # --- 步骤 3: 写入 IP 到 txt 文件 ---
            try:
                with open(IP_TXT_PATH, 'w', encoding='utf-8') as f:
                    for ip in unique_ips:
                        f.write(f"{ip}\n")
                print(f"已将 IP 列表写入 '{IP_TXT_PATH}'")
            except IOError as e:
                print(f"错误：无法写入 IP 列表文件: {e}")
                print(f"将在 {LOOP_DELAY_SECONDS} 秒后重试...")
                time.sleep(LOOP_DELAY_SECONDS)
                continue

            # --- 步骤 4: 执行第二次测速 ---
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 正在执行第二次测速...")
            
            return_code_2 = run_command_streamed(CMD_PASS_2)

            if return_code_2 == 0:
                print("\n第二次测速成功完成。")
            elif return_code_2 == -1:
                break
            else:
                print(f"\n错误：第二次测速失败，返回码: {return_code_2}。")
        
        print(f"本轮操作完成，将在 {LOOP_DELAY_SECONDS} 秒后开始下一轮...")
        time.sleep(LOOP_DELAY_SECONDS)

if __name__ == "__main__":
    # 确保工作目录存在
    if not os.path.exists(WORK_DIR):
        print(f"错误：工作目录 '{WORK_DIR}' 不存在。请检查路径配置。")
        exit(1)
    
    # 确保 CFST 可执行文件存在且有执行权限
    if not os.access(CFST_EXEC, os.X_OK):
        print(f"错误：'{CFST_EXEC}' 不存在或没有执行权限。")
        exit(1)

    main()