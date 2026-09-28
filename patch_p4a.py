#!/usr/bin/env python3
"""精确 patch python-for-android: 禁用 venv 里的 pip 升级。

p4a 在最后阶段创建 venv 并执行 `pip install -U pip`，
但 hostpython3 自带旧 pip，升级时新旧 pip 文件混搭，导致：
  ImportError: cannot import name 'open_rich_spinner'

修法：找到 pip 升级相关代码，注释掉。
"""
import os
import sys
import re

def patch_file(filepath):
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
    
    original = content
    lines = content.split('\n')
    new_lines = []
    changed = False
    
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        
        # 匹配 pip 升级相关行
        # 1. 注释里有 "Upgrade pip"
        # 2. 执行 pip install -U pip / pip install --upgrade pip
        if re.search(r'Upgrade pip', line, re.IGNORECASE):
            # 注释掉这行
            if not line.strip().startswith('#'):
                new_lines.append('#PATCHED-PIP-UPGRADE: ' + line)
                changed = True
                print(f"  [注释掉] 'Upgrade pip' 注释 @ {filepath}:{i+1}")
                i += 1
                continue
        
        if re.search(r'pip\s+install.*(-U|--upgrade)\s+pip', line, re.IGNORECASE):
            if not line.strip().startswith('#'):
                new_lines.append('#PATCHED-PIP-UPGRADE: ' + line)
                changed = True
                print(f"  [注释掉] pip 升级命令 @ {filepath}:{i+1}")
                i += 1
                continue
        
        new_lines.append(line)
        i += 1
    
    if changed:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(new_lines))
        return True
    return False

def main():
    # 找到 p4a 安装目录
    try:
        import pythonforandroid
        p4a_dir = os.path.dirname(pythonforandroid.__file__)
    except ImportError:
        print("ERROR: python-for-android 未安装")
        sys.exit(1)
    
    print(f"p4a 目录: {p4a_dir}")
    print("")
    
    patched_files = 0
    for root, dirs, files in os.walk(p4a_dir):
        # 跳过 __pycache__
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for f in files:
            if f.endswith('.py'):
                fp = os.path.join(root, f)
                if patch_file(fp):
                    patched_files += 1
    
    print(f"\n=== Patch 完成: {patched_files} 个文件被修改 ===")
    
    # 验证
    print("\n=== 验证剩余 pip 升级代码 ===")
    import subprocess
    result = subprocess.run(
        ['grep', '-rn', 'pip install.*-U.*pip\|pip install.*--upgrade.*pip\|Upgrade pip', p4a_dir, '--include=*.py'],
        capture_output=True, text=True
    )
    remaining = [l for l in result.stdout.splitlines() if not l.strip().startswith('#')]
    if remaining:
        print("仍有未注释的 pip 升级代码:")
        for l in remaining:
            print(f"  {l[:200]}")
    else:
        print("✓ 所有 pip 升级代码已注释")

if __name__ == '__main__':
    main()
