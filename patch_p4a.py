#!/usr/bin/env python3
"""精确 patch python-for-android: venv pip 升级改用 --force-reinstall。

p4a 在最后阶段创建 venv 并执行 `pip install -U pip`，
但 hostpython3 自带旧 pip，升级时新旧 pip 文件混搭，导致：
  ImportError: cannot import name 'open_rich_spinner'

修法：把 `pip install -U pip` / `pip install --upgrade pip`
      改成 `pip install --force-reinstall pip`
      → 强制重装所有 pip 文件，完整替换，不会混搭。
"""
import os
import sys
import re

def patch_file(filepath):
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
    
    original = content
    changed = False
    
    # 精确替换：pip install -U pip → pip install --force-reinstall pip
    # 匹配所有变体: -U pip, --upgrade pip, -U 'pip', --upgrade "pip"
    def replace_pip_upgrade(m):
        nonlocal changed
        changed = True
        return m.group(0).replace('-U', '--force-reinstall').replace('--upgrade', '--force-reinstall').replace('  ', ' ')
    
    # 用 re.sub 替换所有 pip install -U pip / --upgrade pip 变体
    new_content = re.sub(
        r'pip\s+install\s+(-U|--upgrade)\s+["\']?pip["\']?',
        replace_pip_upgrade,
        content,
        flags=re.IGNORECASE
    )
    
    if new_content != original:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        # 打印哪些行被改了
        for i, (old_l, new_l) in enumerate(zip(original.split('\n'), new_content.split('\n'))):
            if old_l != new_l and 'pip install' in old_l.lower():
                print(f"  [{i+1}] {old_l.strip()[:120]}")
                print(f"       → {new_l.strip()[:120]}")
        
        return True
    return False

def main():
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
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for f in files:
            if f.endswith('.py'):
                fp = os.path.join(root, f)
                if patch_file(fp):
                    patched_files += 1
    
    print(f"\n=== Patch 完成: {patched_files} 个文件被修改 ===")
    
    # 验证：确认没有遗留的 -U pip / --upgrade pip（除了我们改的 --force-reinstall）
    print("\n=== 验证 ===")
    import subprocess
    result = subprocess.run(
        ['grep', '-rn', 'pip install.*-U.*pip\\|pip install.*--upgrade.*pip', p4a_dir, '--include=*.py'],
        capture_output=True, text=True
    )
    remaining = [l for l in result.stdout.splitlines() if '--force-reinstall' not in l]
    if remaining:
        print("仍有未修改的 pip 升级代码:")
        for l in remaining:
            print(f"  {l[:200]}")
    else:
        print("✓ 所有 pip 升级已改为 --force-reinstall")

if __name__ == '__main__':
    main()
