import os
import pandas as pd
import warnings
from datetime import datetime, timedelta
from pathlib import Path

# 抑制openpyxl样式警告
warnings.filterwarnings('ignore', category=UserWarning, module='openpyxl')

def read_excel_structure(file_path):
    """读取Excel文件的结构和内容"""
    print(f"\n{'='*80}")
    print(f"分析文件: {file_path}")
    print('='*80)
    
    try:
        xls = pd.ExcelFile(file_path)
        print(f"\n工作表列表: {xls.sheet_names}")
        
        sheet_name = xls.sheet_names[0]
        print(f"\n分析工作表: {sheet_name}")
        
        # 尝试不同的header行
        for header_row in range(6):
            try:
                df = pd.read_excel(file_path, sheet_name=sheet_name, header=header_row)
                print(f"\n--- Header行={header_row} ---")
                print(f"列数: {df.shape[1]}")
                print(f"行数: {df.shape[0]}")
                print(f"列名: {list(df.columns)}")
                
                # 显示前5行数据
                if len(df) > 0:
                    print(f"\n前5行数据:")
                    print(df.head().to_string())
                
                # 查找可能的日期列
                print(f"\n日期相关分析:")
                for idx, col in enumerate(df.columns):
                    col_data = df.iloc[:, idx].dropna()
                    if len(col_data) > 0:
                        # 显示该列的前几个值
                        sample_vals = col_data.head(3).tolist()
                        print(f"  列{idx} ({col}): {sample_vals}")
                
                break
                
            except Exception as e:
                print(f"Header行{header_row}读取失败: {e}")
                continue
        
        # 无header读取
        try:
            df_raw = pd.read_excel(file_path, sheet_name=sheet_name, header=None)
            print(f"\n--- 无Header读取 ---")
            print(f"总行数: {df_raw.shape[0]}")
            print(f"总列数: {df_raw.shape[1]}")
            
            # 显示前10行，前5列
            print(f"\n前10行，前5列数据:")
            print(df_raw.iloc[:10, :5].to_string())
            
        except Exception as e:
            print(f"无Header读取失败: {e}")
        
    except Exception as e:
        print(f"读取文件失败: {e}")

# 测试读取样本文件
sample_file = '/Users/x/Desktop/汉燧智能/夏初数据/doc_verify/exports/实时输电断面约束及阻塞_2025-12-18.xlsx'
if os.path.exists(sample_file):
    read_excel_structure(sample_file)
else:
    print(f"文件不存在: {sample_file}")