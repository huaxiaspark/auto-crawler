#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Excel 文件结构分析工具

用法:
  python read_sample.py <文件路径>
  python read_sample.py  # 使用 config.yaml 中 data_directory 下的第一个 xlsx 文件
"""

import os
import sys
import warnings
from pathlib import Path

import pandas as pd
import yaml

warnings.filterwarnings('ignore', category=UserWarning, module='openpyxl')


def load_config() -> dict:
    config_path = Path(__file__).parent / 'config.yaml'
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}


def read_excel_structure(file_path: str):
    print(f"\n{'=' * 80}")
    print(f"分析文件: {file_path}")
    print('=' * 80)

    try:
        xls = pd.ExcelFile(file_path)
        print(f"\n工作表: {xls.sheet_names}")
        sheet = xls.sheet_names[0]

        for h in range(6):
            try:
                df = pd.read_excel(file_path, sheet_name=sheet, header=h)
                print(f"\n--- header={h} | 行:{df.shape[0]} 列:{df.shape[1]} ---")
                print(f"列名: {list(df.columns)}")
                if len(df) > 0:
                    print(df.head().to_string())
                break
            except Exception as e:
                print(f"header={h} 失败: {e}")

        df_raw = pd.read_excel(file_path, sheet_name=sheet, header=None)
        print(f"\n--- 无Header | 总行:{df_raw.shape[0]} 总列:{df_raw.shape[1]} ---")
        print(df_raw.iloc[:10, :5].to_string())

    except Exception as e:
        print(f"读取失败: {e}")


def main():
    if len(sys.argv) > 1:
        target = sys.argv[1]
    else:
        cfg = load_config()
        data_dir = cfg.get('global', {}).get('data_directory', '')
        if data_dir and os.path.isdir(data_dir):
            xlsx_files = list(Path(data_dir).glob('*.xlsx'))
            if xlsx_files:
                target = str(xlsx_files[0])
                print(f"未指定文件，使用目录中第一个文件: {target}")
            else:
                print(f"目录中未找到 xlsx 文件: {data_dir}")
                return
        else:
            print("用法: python read_sample.py <文件路径>")
            print("或在 config.yaml 中配置 global.data_directory")
            return

    if os.path.exists(target):
        read_excel_structure(target)
    else:
        print(f"文件不存在: {target}")


if __name__ == "__main__":
    main()
