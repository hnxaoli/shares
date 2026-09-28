#!/usr/bin/env python3
"""终极修法：patch 系统上**所有** pip spinners.py。

p4a 用 hostpython3 (3.11.9 编译版) 创建 venv，venv 复制 hostpython3 的旧 pip。
然后 venv 里 pip install -U pip 升级时文件混搭 → ImportError。
修法：在构建开始前，patch **所有能找到的** spinners.py，给 open_rich_spinner 加 dummy。
这样 venv 不管从哪复制 pip，都不会 ImportError。
"""
import os, sys, subprocess, glob

def patch_spinners(path):
    """给一个 spinners.py 文件加 open_rich_spinner fallback。"""
    if not os.path.exists(path):
        return False
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
    # 检查有没有我们的 patch 或原生的 open_rich_spinner
    if '# PATCH: open_rich_spinner fallback' in content:
        return False  # 已 patch
    if 'def open_rich_spinner' in content:
        # 原生就有 — 可能 pip 版本较新
        return False
    patch = '''

# PATCH: open_rich_spinner fallback (防止 pip 升级混搭导致 ImportError)
def open_rich_spinner(*args, **kwargs):
    try:
        from pip._internal.cli.spinners import open_spinner
        return open_spinner(*args, **kwargs)
    except Exception:
        return None
'''
    with open(path, 'a', encoding='utf-8') as f:
        f.write(patch)
    print(f'  ✓ patched: {path}')
    return True

def find_all_spinners():
    """找系统里所有 pip/_internal/cli/spinners.py。"""
    found = []
    
    # 1. 当前 python 的 pip
    try:
        import pip
        p = os.path.join(os.path.dirname(pip.__file__), '_internal', 'cli', 'spinners.py')
        found.append(p)
    except Exception:
        pass
    
    # 2. 遍历常见 python site-packages 路径
    common_dirs = [
        '/usr/local/lib',
        '/usr/lib',
        os.path.expanduser('~/.local/lib'),
        os.path.expanduser('~/.buildozer'),
    ]
    for base in common_dirs:
        for pattern in ['*', 'python*', '*/*']:
            matches = glob.glob(os.path.join(base, pattern, 'site-packages', 'pip', '_internal', 'cli', 'spinners.py'))
            found.extend(matches)
    
    # 3. find 全盘搜（限制在 10 秒内）
    try:
        result = subprocess.run(
            ['find', '/usr', os.path.expanduser('~'), '-name', 'spinners.py',
             '-path', '*/pip/_internal/cli/*', '-not', '-path', '*__pycache__*'],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.strip().splitlines():
            if line and line not in found:
                found.append(line)
    except Exception:
        pass
    
    # 去重
    return list(dict.fromkeys(found))

def main():
    print('=== patch_pip.py: 给所有 pip spinners.py 加 open_rich_spinner fallback ===')
    print()
    
    all_spinners = find_all_spinners()
    print(f'找到 {len(all_spinners)} 个 spinners.py:')
    for p in all_spinners:
        print(f'  {p}')
    print()
    
    patched = 0
    for p in all_spinners:
        if patch_spinners(p):
            patched += 1
    
    # 也 patch 未来可能出现的 —— 在 .buildozer 目录加个 find-and-patch
    print(f'\n=== Patch 了 {patched} 个文件 ===')
    
    # 验证当前 python
    try:
        import subprocess
        r = subprocess.run(
            [sys.executable, '-c', 
             "from pip._internal.cli.spinners import open_rich_spinner; print('current python open_rich_spinner: OK')"],
            capture_output=True, text=True
        )
        print(r.stdout.strip())
        if r.returncode != 0:
            print(f'WARNING: {r.stderr.strip()[:200]}')
    except Exception as e:
        print(f'验证失败: {e}')
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
