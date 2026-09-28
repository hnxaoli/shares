#!/usr/bin/env python3
"""终极 patch: 精确替换 build.py 里的 pip 升级命令字符串。

根因: build.py:878 创建 venv (用 hostpython3 3.11.9) 后升级 pip 会混搭文件。
修法: 在那行 shell 命令里，升级 pip **前**先 patch venv 的 spinners.py。
"""
import os, sys, shutil

def main():
    try:
        import pythonforandroid
        p4a_dir = os.path.dirname(pythonforandroid.__file__)
    except ImportError:
        print("ERROR: python-for-android 未安装")
        sys.exit(1)
    
    print(f"p4a: {p4a_dir}")
    
    # 1. 找 build.py
    build_py = os.path.join(p4a_dir, 'build.py')
    if not os.path.exists(build_py):
        print(f"ERROR: 找不到 build.py 在 {p4a_dir}")
        sys.exit(1)
    
    with open(build_py, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
    
    original = content
    
    # 原始行 (build.py:878)
    OLD = '"source venv/bin/activate && pip install -U pip"'
    
    # 新: 先 patch venv spinners.py，再 force-reinstall
    # venv/bin/python 自带正确的 pip 模块位置，用来 patch venv 自己
    NEW = (
        '"source venv/bin/activate && '
        'venv/bin/python -c "'
        'import os;'
        'import pip._internal.cli.spinners as _s;'
        '_p=_s.__file__;'
        '_c=open(_p).read();'
        '_patch=chr(10)*2+"def open_rich_spinner(*a,**kw):\\n    from pip._internal.cli.spinners import open_spinner\\n    return open_spinner(*a,**kw)\\n";'
        'open(_p,"a").write(_patch) if "def open_rich_spinner" not in _c else None;'
        '" && '
        'pip install --force-reinstall pip"'
    )
    
    if OLD in content:
        content = content.replace(OLD, NEW)
        print(f"✓ 精确匹配并替换了 pip 升级命令")
    elif 'source venv/bin/activate' in content and 'pip install' in content:
        # 模糊搜索（不管引号格式）
        for line in content.split('\n'):
            if 'source venv/bin/activate' in line and 'pip install' in line and 'spinners' not in line:
                print(f"  找到类似行: {line.strip()[:150]}")
                # 直接替换整行字符串
                import re
                new_line = re.sub(
                    r'"source venv/bin/activate && pip install[^"]*"',
                    NEW,
                    line
                )
                if new_line != line:
                    content = content.replace(line, new_line)
                    print(f"✓ 已替换")
                break
    
    if content == original:
        print("⚠️ 没找到要替换的行！可能 build.py 格式变了")
        # 打印所有含 pip install 的行
        for i, line in enumerate(content.split('\n')):
            if 'pip install' in line and 'venv' in line.lower():
                print(f"  [{i}] {line.strip()[:200]}")
        sys.exit(1)
    
    with open(build_py, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"✓ build.py 已更新")
    
    # 2. 清 __pycache__
    print("\n=== 清 __pycache__ ===")
    count = 0
    for root, dirs, files in os.walk(p4a_dir):
        for d in list(dirs):
            if d == '__pycache__':
                shutil.rmtree(os.path.join(root, d))
                count += 1
    print(f"已删 {count} 个 __pycache__")
    
    # 3. 验证
    print("\n=== 验证 ===")
    with open(build_py, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f):
            if 'source venv/bin/activate' in line and 'pip install' in line:
                print(f"  build.py:{i+1}: {line.strip()[:200]}")
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
