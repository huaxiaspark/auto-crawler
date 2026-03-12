#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Excel文件分析与日前联络线计划校验系统

功能：
1. 检查日期覆盖情况
2. 校验文件名与表格内日期一致性
3. 日前联络线计划专项校验

作者：汉燧智能
日期：2025-01
"""

import os
import pandas as pd
import warnings
import re
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter

warnings.filterwarnings('ignore', category=UserWarning, module='openpyxl')

# 日前联络线计划的22个通道配置
STANDARD_RIQIAN_CHANNELS = [
    '过境送雁淮(省间现货)',
    '过境送雁淮(月计划)',
    '过境送长南(省间现货)',
    '过境送长南(月计划)',
    '河北负荷',
    '华北负荷',
    '京津唐',
    '山西送河北(华北跨省)',
    '山西送河北(省间现货)',
    '山西送河北(月计划)',
    '山西送京津唐(华北跨省)',
    '山西送京津唐(省间现货)',
    '山西送京津唐(月计划)',
    '山西送锡泰(省间现货)',
    '山西送锡泰(月计划)',
    '山西送雁淮(省间现货)',
    '山西送雁淮(月计划)',
    '山西送长南(省间现货)',
    '山西送长南(月计划)',
    '特高压雁淮直流外送',
    '特高压长南线',
    '总加'
]

def generate_date_range():
    """生成2023.01.01至2026.01.31的所有日期列表"""
    start_date = datetime(2023, 1, 1)
    end_date = datetime(2026, 1, 31)
    date_list = []
    current_date = start_date
    while current_date <= end_date:
        date_list.append(current_date.strftime('%Y-%m-%d'))
        current_date += timedelta(days=1)
    return date_list

def extract_date_from_filename(filename):
    """从文件名中提取日期信息"""
    try:
        # 首先检查是否是日前联络线计划文件
        if '日前联络线计划' in filename:
            parts = filename.split('_')
            if len(parts) >= 2:
                date_str = parts[1]
                datetime.strptime(date_str, '%Y-%m-%d')
                return date_str
        
        date_str = filename.split('_')[-1]
        for ext in ['.xlsx', '.csv']:
            if date_str.endswith(ext):
                date_str = date_str.replace(ext, '')
                break
        datetime.strptime(date_str, '%Y-%m-%d')
        return date_str
    except (ValueError, IndexError):
        return None

def parse_lianluo_filename(filename):
    """解析日前联络线计划文件名
    格式: 日前联络线计划_2025-01-01_通道名称.xlsx
    """
    try:
        base_name = filename.replace('.xlsx', '')
        parts = base_name.split('_')
        
        if len(parts) < 3:
            return None
        
        date_str = parts[1]
        datetime.strptime(date_str, '%Y-%m-%d')
        channel = '_'.join(parts[2:])
        
        return {'date': date_str, 'channel': channel}
        
    except (ValueError, IndexError):
        return None

def get_all_excel_files(directory):
    """获取目录下所有Excel和CSV文件"""
    data_files = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith('.xlsx'):
                file_path = os.path.join(root, file)
                date_str = extract_date_from_filename(file)
                if date_str:
                    is_lianluo = '日前联络线计划' in file
                    parsed = parse_lianluo_filename(file) if is_lianluo else None
                    
                    data_files.append({
                        'filename': file,
                        'filepath': file_path,
                        'date': date_str,
                        'file_type': 'excel',
                        'is_riqian_lianluo': is_lianluo,
                        'channel': parsed['channel'] if parsed else None
                    })
            elif file.endswith('.csv'):
                file_path = os.path.join(root, file)
                date_str = extract_date_from_filename(file)
                if date_str:
                    data_files.append({
                        'filename': file,
                        'filepath': file_path,
                        'date': date_str,
                        'file_type': 'csv'
                    })
    return data_files

def check_excel_readable(filepath):
    """检查Excel文件是否可读"""
    try:
        xls = pd.ExcelFile(filepath)
        if len(xls.sheet_names) == 0:
            return False, "错误: Excel文件没有工作表"
        return True, None
    except Exception as e:
        return False, f"错误: 无法打开Excel文档 - {str(e)}"

def check_date_consistency(excel_file):
    """检查文件名日期与表格内日期是否一致"""
    try:
        file_date = datetime.strptime(excel_file['date'], '%Y-%m-%d').date()
        filepath = excel_file['filepath']
        
        # 首先检查文件是否可读
        is_readable, error_msg = check_excel_readable(filepath)
        if not is_readable:
            return False, {'error': error_msg, 'type': 'read_error'}
        
        xls = pd.ExcelFile(filepath)
        sheet_name = xls.sheet_names[0]
        
        for header_row in [0, 1, 2, 3]:
            try:
                df = pd.read_excel(filepath, sheet_name=sheet_name, 
                                  header=header_row, nrows=100)
                
                scan_rows = min(50, len(df))
                found_dates = []
                found_match = False
                has_date_header = False  # 仅当表头包含日期相关字段（如“日期”“时间”）才视为有日期表头
                
                for col in df.columns[:5]:
                    col_str = str(col).strip()
                    if col_str and ('日期' in col_str or '时间' in col_str):
                        has_date_header = True
                        break
                
                for row_idx in range(scan_rows):
                    row = df.iloc[row_idx]
                    for val in row:
                        if isinstance(val, datetime):
                            if val.date() == file_date:
                                found_match = True
                                break
                            found_dates.append(val.date().strftime('%Y-%m-%d'))
                        elif isinstance(val, str):
                            try:
                                val_date = datetime.strptime(val, '%Y-%m-%d').date()
                                if val_date == file_date:
                                    found_match = True
                                    break
                                found_dates.append(val_date.strftime('%Y-%m-%d'))
                            except ValueError:
                                try:
                                    val_date = datetime.strptime(val, '%Y/%m/%d').date()
                                    if val_date == file_date:
                                        found_match = True
                                        break
                                    found_dates.append(val_date.strftime('%Y-%m-%d'))
                                except ValueError:
                                    continue
                    
                    if found_match:
                        break
                
                if found_match:
                    return True, None
                elif found_dates:
                    return False, {
                        'sample_dates': list(set(found_dates))[:5], 
                        'type': 'date_mismatch',
                        'reason': f'表格中未找到日期 "{excel_file["date"]}"\n表格中发现的日期: {list(set(found_dates))[:5]}'
                    }
                elif has_date_header:
                    return False, {
                        'error': '表格中有日期表头，但无日期数据', 
                        'type': 'no_date_data',
                        'reason': '表格中有有效的日期列，但未找到任何日期数据'
                    }
                else:
                    return False, {
                        'error': '表格中无日期表头信息', 
                        'type': 'no_date_header',
                        'reason': '表格中没有找到有效的日期列（表头缺失或表头不包含“日期”“时间”等字段）'
                    }
                
                break
                
            except Exception as e:
                continue
        
        return False, {
            'error': '表格中未找到日期数据', 
            'type': 'no_date_data',
            'reason': '无法在表格中找到任何有效的日期信息'
        }
        
    except Exception as e:
        return False, {
            'error': str(e), 
            'type': 'unknown_error',
            'reason': f'文件处理过程中发生未知错误: {str(e)}'
        }

def _normalize_cell_for_channel(s):
    """规范化单元格文本用于通道匹配：去空格、全角括号转半角"""
    if not isinstance(s, str):
        return ''
    s = s.strip().replace('\u3000', ' ').replace(' ', '')
    s = s.replace('（', '(').replace('）', ')')
    return s


def check_lianluo_channel_consistency(excel_file):
    """检查日前联络线计划文件的通道一致性：文件名中的通道（字符串）在表格通道列中至少有一处完全一致即通过"""
    try:
        filename_channel = excel_file.get('channel', '')
        filepath = excel_file['filepath']
        
        is_readable, error_msg = check_excel_readable(filepath)
        if not is_readable:
            return False, {'error': error_msg, 'type': 'read_error'}
        
        xls = pd.ExcelFile(filepath)
        sheet_name = xls.sheet_names[0]
        
        found_channels = []  # 按出现顺序收集，第一个即为表格主通道
        has_channel_header = False
        
        for header_row in [0, 1, 2, 3]:
            try:
                df = pd.read_excel(filepath, sheet_name=sheet_name,
                                  header=header_row, nrows=60)
                
                for col in df.columns:
                    col_str = str(col).strip()
                    if '通道' in col_str:
                        has_channel_header = True
                        break
                
                scan_rows = min(60, len(df))
                for row_idx in range(scan_rows):
                    row = df.iloc[row_idx]
                    for val in row:
                        if not isinstance(val, str):
                            continue
                        norm_val = _normalize_cell_for_channel(val)
                        if not norm_val:
                            continue
                        for expected_channel in STANDARD_RIQIAN_CHANNELS:
                            norm_expected = _normalize_cell_for_channel(expected_channel)
                            if (expected_channel in val or norm_expected in norm_val) and expected_channel not in found_channels:
                                found_channels.append(expected_channel)
                                break
                
                if found_channels:
                    break
            except Exception:
                continue
        
        # 无表头方式再扫一遍，避免表头行数多导致通道在数据区被漏扫
        if not found_channels:
            try:
                df = pd.read_excel(filepath, sheet_name=sheet_name, header=None, nrows=60)
                for row_idx in range(min(60, len(df))):
                    for col_idx in range(min(20, df.shape[1])):
                        val = df.iloc[row_idx, col_idx]
                        if not isinstance(val, str):
                            continue
                        norm_val = _normalize_cell_for_channel(val)
                        if not norm_val:
                            continue
                        for expected_channel in STANDARD_RIQIAN_CHANNELS:
                            norm_expected = _normalize_cell_for_channel(expected_channel)
                            if (expected_channel in val or norm_expected in norm_val) and expected_channel not in found_channels:
                                found_channels.append(expected_channel)
                                break
                    if found_channels:
                        break
            except Exception:
                pass
        
        if found_channels:
            # 文件名通道与表格通道列中任一完全一致即通过，不要求“首次出现”或“主通道”
            if filename_channel in found_channels:
                return True, None
            return False, {
                'table_channels': found_channels[:5],
                'filename_channel': filename_channel,
                'type': 'channel_mismatch',
                'reason': f'文件名通道: "{filename_channel}" 未在表格通道列中出现\n表格中发现的通道: {found_channels[:5]}'
            }
        elif has_channel_header:
            # 有通道列但内容不是任一标准通道名（如为'123'等）→ 视为通道名称与文件名不一致，并读取表格内实际内容用于输出
            table_channel_raw = None
            for header_row in [0, 1, 2, 3]:
                try:
                    df = pd.read_excel(filepath, sheet_name=sheet_name, header=header_row, nrows=60)
                    for col in df.columns:
                        if '通道' not in str(col).strip():
                            continue
                        for row_idx in range(len(df)):
                            val = df.iloc[row_idx][col]
                            if val is None or (isinstance(val, float) and pd.isna(val)):
                                continue
                            s = str(val).strip()
                            if s:
                                table_channel_raw = s
                                break
                        if table_channel_raw is not None:
                            break
                    if table_channel_raw is not None:
                        break
                except Exception:
                    continue
            return False, {
                'table_channels': [],
                'filename_channel': filename_channel,
                'type': 'channel_mismatch',
                'table_channel_raw': table_channel_raw,
                'reason': f'文件名通道: "{filename_channel}"，表格通道列中的内容与文件名通道不一致（非标准通道名称，如数字等）'
            }
        else:
            return False, {
                'error': '表格中无通道表头信息', 
                'type': 'no_channel_header',
                'reason': '表格中没有找到有效的通道列（表头缺失或不包含"通道"字段）'
            }
        
    except Exception as e:
        return False, {
            'error': str(e), 
            'type': 'unknown_error',
            'reason': f'通道校验过程中发生错误: {str(e)}'
        }

def get_lianluo_files_by_date(directory, target_date):
    """获取指定日期的所有日前联络线计划文件"""
    target_files = []
    prefix = f"日前联络线计划_{target_date}_"
    
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.startswith(prefix) and file.endswith('.xlsx'):
                parsed = parse_lianluo_filename(file)
                if parsed:
                    target_files.append({
                        'filename': file,
                        'filepath': os.path.join(root, file),
                        'date': parsed['date'],
                        'channel': parsed['channel']
                    })
    
    return target_files

def check_lianluo_file_completeness(directory, target_date):
    """检查指定日期的日前联络线计划文件完整性"""
    files = get_lianluo_files_by_date(directory, target_date)
    found_channels = [f['channel'] for f in files]
    
    missing_channels = []
    duplicate_channels = []
    
    for expected_channel in STANDARD_RIQIAN_CHANNELS:
        if expected_channel not in found_channels:
            missing_channels.append(expected_channel)
    
    channel_counts = Counter(found_channels)
    for channel, count in channel_counts.items():
        if count > 1:
            duplicate_channels.append({'channel': channel, 'count': count})
    
    is_complete = len(missing_channels) == 0 and len(duplicate_channels) == 0 and len(files) == 22
    
    return is_complete, {
        'date': target_date,
        'total_files': len(files),
        'expected_count': 22,
        'missing_channels': missing_channels,
        'duplicate_channels': duplicate_channels
    }

def write_missing_files_report(directory, missing_files):
    """将缺失文件信息写入loss.txt
    格式: 日期,通道名称
    输出到脚本所在根目录
    """
    # 获取脚本所在目录的根目录
    script_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.path.dirname(directory)
    output_path = os.path.join(script_dir, 'loss.txt')
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("# 日前联络线计划缺失文件列表\n")
        f.write(f"# 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("# 格式: 日期,通道名称\n")
        f.write("# 示例:\n")
        f.write("# 2026-01-01,山西送长南(省间现货)\n")
        f.write("# ========================================\n\n")
        
        # 按日期排序
        missing_files_sorted = sorted(missing_files, key=lambda x: (x['date'], x['channel']))
        
        for item in missing_files_sorted:
            f.write(f"{item['date']},{item['channel']}\n")
    
    print(f"✅ 缺失文件列表已保存至: {output_path}")
    return output_path

def write_validation_errors_report(directory, date_errors, channel_errors, combined_errors=None):
    """将校验错误详细信息写入validation_errors.txt
    输出到脚本所在根目录
    详细说明错误原因
    """
    # 获取脚本所在目录的根目录
    script_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.path.dirname(directory)
    output_path = os.path.join(script_dir, 'validation_errors.txt')
    
    # 过滤不需要输出的错误类型
    # 1. 过滤日期校验错误中"有日期表头，但无日期数据"的情况
    filtered_date_errors = []
    for item in date_errors:
        reason = item.get('reason', {})
        error_type = reason.get('type', 'unknown')
        if error_type != 'no_date_data':
            filtered_date_errors.append(item)
    
    # 2. 过滤通道校验错误中"通道名称不一致"且"表格内通道名称: 无"的情况
    filtered_channel_errors = []
    for item in channel_errors:
        reason = item.get('reason', {})
        error_type = reason.get('type', 'unknown')
        if error_type == 'channel_mismatch':
            # 检查表格内通道名称是否为"无"
            table_channels = reason.get('table_channels', [])
            table_channel_raw = reason.get('table_channel_raw')
            if table_channel_raw is not None and str(table_channel_raw).strip() != '':
                table_channel_str = str(table_channel_raw).strip()
            else:
                table_channel_str = table_channels[0] if table_channels else '无'
            # 如果表格内通道名称为"无"，则跳过
            if table_channel_str == '无':
                continue
        filtered_channel_errors.append(item)
    
    # 按日期和通道名称排序
    date_errors_sorted = sorted(filtered_date_errors, key=lambda x: (x.get('date', ''), x.get('channel', '')))
    channel_errors_sorted = sorted(filtered_channel_errors, key=lambda x: (x.get('date', ''), x.get('channel', '')))
    
    if combined_errors is None:
        combined_errors = []
    # 同时过滤combined_errors中对应的错误
    filtered_combined_errors = []
    for item in combined_errors:
        date_reason = item.get('date_reason', {})
        channel_reason = item.get('channel_reason', {})
        date_error_type = date_reason.get('type', 'unknown') if isinstance(date_reason, dict) else 'unknown'
        channel_error_type = channel_reason.get('type', 'unknown') if isinstance(channel_reason, dict) else 'unknown'
        
        # 如果日期错误是"有日期表头，但无日期数据"，跳过
        if not item.get('date_ok') and date_error_type == 'no_date_data':
            continue
        
        # 如果通道错误是"通道名称不一致"且表格内通道名称为"无"，跳过
        if not item.get('channel_ok') and channel_error_type == 'channel_mismatch':
            table_channels = channel_reason.get('table_channels', []) if isinstance(channel_reason, dict) else []
            table_channel_raw = channel_reason.get('table_channel_raw') if isinstance(channel_reason, dict) else None
            if table_channel_raw is not None and str(table_channel_raw).strip() != '':
                table_channel_str = str(table_channel_raw).strip()
            else:
                table_channel_str = table_channels[0] if table_channels else '无'
            if table_channel_str == '无':
                continue
        
        filtered_combined_errors.append(item)
    
    combined_errors_sorted = sorted(filtered_combined_errors, key=lambda x: (x.get('date', ''), x.get('channel', '')))
    
    # 同时无日期表头且无通道表头的文件（输出错误类型为“无日期或无通道表头”）
    no_date_no_channel_filenames = set()
    for item in combined_errors_sorted:
        if not item.get('date_ok') and not item.get('channel_ok'):
            dr, cr = item.get('date_reason') or {}, item.get('channel_reason') or {}
            if dr.get('type') == 'no_date_header' and cr.get('type') == 'no_channel_header':
                no_date_no_channel_filenames.add(item['filename'])
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("=" * 80 + "\n")
        f.write("日前联络线计划校验错误详细报告\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 80 + "\n\n")
        
        # 1. 日期校验错误
        f.write("-" * 80 + "\n")
        f.write("一、日期校验错误\n")
        f.write("-" * 80 + "\n\n")
        
        if date_errors_sorted:
            f.write(f"共发现 {len(date_errors_sorted)} 个日期校验错误:\n\n")
            
            for item in date_errors_sorted:
                f.write(f"文件: {item['filename']}\n")
                f.write(f"  文件日期: {item['date']}\n")
                
                reason = item.get('reason', {})
                error_type = reason.get('type', 'unknown')
                
                if item['filename'] in no_date_no_channel_filenames:
                    f.write(f"  错误类型: 无日期或无通道表头\n")
                    f.write(f"  详细原因: 表格中无日期表头信息且无通道表头信息\n")
                elif error_type == 'read_error':
                    f.write(f"  错误类型: 文件无法解析\n")
                    f.write(f"  详细原因: {reason.get('error', '未知错误')}\n")
                elif error_type == 'date_mismatch':
                    file_date = item.get('date', '')
                    table_dates = reason.get('sample_dates', [])
                    table_date_str = table_dates[0] if table_dates else '无'
                    f.write(f"  错误类型: 日期不一致\n")
                    f.write(f"  文件名日期: {file_date}\n")
                    f.write(f"  表格内日期: {table_date_str}\n")
                    f.write(f"  详细原因: {reason.get('reason', '日期不匹配')}\n")
                    if len(table_dates) > 1:
                        f.write(f"  表格内其他日期: {table_dates[1:]}\n")
                elif error_type == 'no_date_data':
                    f.write(f"  错误类型: 有日期表头，但无日期数据\n")
                    f.write(f"  详细原因: {reason.get('reason', '表格中有有效的日期列，但未找到任何日期数据')}\n")
                elif error_type == 'no_date_header':
                    f.write(f"  错误类型: 无日期表头信息\n")
                    f.write(f"  详细原因: {reason.get('reason', '表格中没有找到有效的日期列（表头缺失或表头不包含“日期”“时间”等字段）')}\n")
                else:
                    f.write(f"  错误类型: {error_type}\n")
                    if 'reason' in reason:
                        f.write(f"  详细原因: {reason['reason']}\n")
                    elif 'error' in reason:
                        f.write(f"  错误信息: {reason['error']}\n")
                
                f.write("\n")
        else:
            f.write("✅ 无日期校验错误\n")
        
        # 2. 通道校验错误
        f.write("-" * 80 + "\n")
        f.write("二、通道校验错误\n")
        f.write("-" * 80 + "\n\n")
        
        if channel_errors_sorted:
            f.write(f"共发现 {len(channel_errors_sorted)} 个通道校验错误:\n\n")
            
            for item in channel_errors_sorted:
                f.write(f"文件: {item['filename']}\n")
                f.write(f"  文件通道: {item['channel']}\n")
                
                reason = item.get('reason', {})
                error_type = reason.get('type', 'unknown')
                
                if item['filename'] in no_date_no_channel_filenames:
                    f.write(f"  错误类型: 无日期或无通道表头\n")
                    f.write(f"  详细原因: 表格中无日期表头信息且无通道表头信息\n")
                elif error_type == 'read_error':
                    f.write(f"  错误类型: 文件无法解析\n")
                    f.write(f"  详细原因: {reason.get('error', '未知错误')}\n")
                elif error_type == 'channel_mismatch':
                    file_channel = item.get('channel', '')
                    table_channels = reason.get('table_channels', [])
                    # 优先使用表格内实际读到的通道内容（如'123'），否则用发现的标准通道名，再否则为'无'
                    table_channel_raw = reason.get('table_channel_raw')
                    if table_channel_raw is not None and str(table_channel_raw).strip() != '':
                        table_channel_str = str(table_channel_raw).strip()
                    else:
                        table_channel_str = table_channels[0] if table_channels else '无'
                    f.write(f"  错误类型: 通道名称不一致\n")
                    f.write(f"  文件名通道名称: {file_channel}\n")
                    f.write(f"  表格内通道名称: {table_channel_str}\n")
                    f.write(f"  详细原因: {reason.get('reason', '通道名称不匹配')}\n")
                    if len(table_channels) > 1:
                        f.write(f"  表格内其他通道: {table_channels[1:]}\n")
                elif error_type == 'no_channel_data':
                    f.write(f"  错误类型: 有通道名称表头信息，但无通道名称数据\n")
                    f.write(f"  详细原因: {reason.get('reason', '表格中未找到有效的通道信息')}\n")
                elif error_type == 'no_channel_header':
                    f.write(f"  错误类型: 无通道名称表头信息\n")
                    f.write(f"  详细原因: {reason.get('reason', '表格中未找到通道列')}\n")
                else:
                    f.write(f"  错误类型: {error_type}\n")
                    if 'reason' in reason:
                        f.write(f"  详细原因: {reason['reason']}\n")
                    elif 'error' in reason:
                        f.write(f"  错误信息: {reason['error']}\n")
                
                f.write("\n")
        else:
            f.write("✅ 无通道校验错误\n")
        
        # 3. 综合校验结果
        f.write("=" * 80 + "\n")
        f.write("三、综合校验结果\n")
        f.write("=" * 80 + "\n\n")
        
        date_only_errors = len([e for e in combined_errors_sorted if not e.get('date_ok') and e.get('channel_ok')])
        channel_only_errors = len([e for e in combined_errors_sorted if e.get('date_ok') and not e.get('channel_ok')])
        both_errors = len([e for e in combined_errors_sorted if not e.get('date_ok') and not e.get('channel_ok')])
        
        f.write(f"校验通过 (日期✓ 通道✓): {len(combined_errors_sorted) - date_only_errors - channel_only_errors - both_errors} 个\n")
        f.write(f"仅日期错误: {date_only_errors} 个\n")
        f.write(f"仅通道错误: {channel_only_errors} 个\n")
        f.write(f"日期和通道均错误: {both_errors} 个\n")
        f.write(f"\n详细错误列表:\n")
        
        if combined_errors_sorted:
            for item in combined_errors_sorted:
                f.write(f"\n文件: {item['filename']}\n")
                f.write(f"  日期校验: {'✓ 通过' if item.get('date_ok') else '✗ 失败'}\n")
                f.write(f"  通道校验: {'✓ 通过' if item.get('channel_ok') else '✗ 失败'}\n")
                
                if not item.get('date_ok') or not item.get('channel_ok'):
                    dr, cr = item.get('date_reason') or {}, item.get('channel_reason') or {}
                    if not item.get('date_ok') and not item.get('channel_ok') and dr.get('type') == 'no_date_header' and cr.get('type') == 'no_channel_header':
                        f.write(f"  错误类型: 无日期或无通道表头\n")
                    else:
                        if not item.get('date_ok'):
                            f.write(f"  日期错误类型: {dr.get('type', 'unknown')}\n")
                        if not item.get('channel_ok'):
                            f.write(f"  通道错误类型: {cr.get('type', 'unknown')}\n")
        else:
            f.write("无\n")
        
        # 4. 旧版分类统计（保留兼容）
        f.write("\n" + "=" * 80 + "\n")
        f.write("四、分类统计\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"日期校验错误: {len(date_errors_sorted)} 个\n")
        f.write(f"通道校验错误: {len(channel_errors_sorted)} 个\n")
        f.write(f"总错误数: {len(combined_errors_sorted)} 个\n")
        f.write(f"\n报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    print(f"✅ 校验错误详细报告已保存至: {output_path}")
    return output_path

def validate_lianluo_files(directory, target_dates):
    """对日前联络线计划文件进行全面校验"""
    print("\n" + "=" * 80)
    print("日前联络线计划文件校验")
    print("=" * 80)
    
    results = {
        'completeness_check': [],
        'date_errors': [],
        'channel_errors': [],
        'target_dates': target_dates
    }
    
    all_lianluo_files = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if not file.endswith('.xlsx'):
                continue
            # 支持正式文件名及 Excel 临时文件（.~ 前缀）
            name_for_parse = file[2:] if file.startswith('.~') else file
            if name_for_parse.startswith('日前联络线计划_'):
                parsed = parse_lianluo_filename(name_for_parse)
                if parsed and parsed['date'] in target_dates:
                    all_lianluo_files.append({
                        'filename': file,
                        'filepath': os.path.join(root, file),
                        'date': parsed['date'],
                        'channel': parsed['channel']
                    })
    
    results['total_files'] = len(all_lianluo_files)
    
    print(f"\n找到日前联络线计划文件: {len(all_lianluo_files)} 个")
    
    # 1. 检查每个日期的文件完整性
    print("\n" + "-" * 80)
    print("1. 文件完整性校验")
    print("-" * 80)
    
    all_missing_files = []
    
    for target_date in target_dates:
        is_complete, info = check_lianluo_file_completeness(directory, target_date)
        results['completeness_check'].append({
            'date': target_date,
            'is_complete': is_complete,
            'info': info
        })
        
        # 收集缺失文件
        if info['missing_channels']:
            for channel in info['missing_channels']:
                all_missing_files.append({
                    'date': target_date,
                    'channel': channel
                })
        
        status = "✅" if is_complete else "❌"
        print(f"{status} {target_date}: {info['total_files']}/22 文件")
        
        if not is_complete:
            if info['missing_channels']:
                print(f"   缺失: {info['missing_channels'][:3]}", end="")
                if len(info['missing_channels']) > 3:
                    print(f"... (共{len(info['missing_channels'])}个缺失)")
                else:
                    print()
            if info['duplicate_channels']:
                for dup in info['duplicate_channels']:
                    print(f"   重复: {dup['channel']} ({dup['count']}个)")
    
    # 写入缺失文件报告
    if all_missing_files:
        write_missing_files_report(directory, all_missing_files)
    
    # 2. 检查文件内容一致性
    print("\n" + "-" * 80)
    print("2. 文件内容一致性校验")
    print("-" * 80)
    
    date_errors = []
    channel_errors = []
    combined_errors = []
    
    total = len(all_lianluo_files)
    for i, f in enumerate(all_lianluo_files, 1):
        excel_file = {
            'filename': f['filename'],
            'filepath': f['filepath'],
            'date': f['date'],
            'file_type': 'excel',
            'channel': f['channel']
        }
        
        is_date_ok, date_reason = check_date_consistency(excel_file)
        is_channel_ok, channel_reason = check_lianluo_channel_consistency(excel_file)
        
        if not is_date_ok:
            date_errors.append({
                'filename': f['filename'],
                'date': f['date'],
                'channel': f['channel'],
                'reason': date_reason
            })
        
        if not is_channel_ok:
            channel_errors.append({
                'filename': f['filename'],
                'date': f['date'],
                'channel': f['channel'],
                'reason': channel_reason
            })
        
        if not is_date_ok or not is_channel_ok:
            combined_errors.append({
                'filename': f['filename'],
                'date': f['date'],
                'channel': f['channel'],
                'date_ok': is_date_ok,
                'channel_ok': is_channel_ok,
                'date_reason': date_reason,
                'channel_reason': channel_reason
            })
        
        if i % 100 == 0 or i == total:
            print(f"进度: {i}/{total} 已处理...")
    
    results['date_errors'] = date_errors
    results['channel_errors'] = channel_errors
    results['combined_errors'] = combined_errors
    
    print(f"\n日期一致性: {total - len(date_errors)}/{total} 一致")
    if date_errors:
        print(f"\n❌ 日期不一致的文件 ({len(date_errors)} 个):")
        for item in date_errors[:10]:
            print(f"  - {item['filename']}")
            reason = item.get('reason', {})
            error_type = reason.get('type', 'unknown')
            if error_type == 'read_error':
                print(f"    原因: 无法打开文档")
            elif error_type == 'date_mismatch':
                print(f"    原因: 日期不匹配")
            elif error_type == 'no_date_data':
                print(f"    原因: 日期信息缺失")
            else:
                print(f"    原因: 其他错误")
    
    print(f"\n通道一致性: {total - len(channel_errors)}/{total} 一致")
    if channel_errors:
        print(f"\n❌ 通道不一致的文件 ({len(channel_errors)} 个):")
        for item in channel_errors[:10]:
            print(f"  - {item['filename']}")
            reason = item.get('reason', {})
            error_type = reason.get('type', 'unknown')
            if error_type == 'read_error':
                print(f"    原因: 无法打开文档")
            elif error_type == 'channel_mismatch':
                print(f"    原因: 通道不匹配")
            elif error_type == 'no_channel_data':
                print(f"    原因: 通道信息缺失")
            elif error_type == 'skipped':
                print(f"    原因: {reason.get('reason', '日期校验未通过')}")
            else:
                print(f"    原因: 其他错误")
    
    valid_date_files = total - len(date_errors)
    valid_channel_files = valid_date_files - len([e for e in channel_errors if e.get('reason', {}).get('type') != 'skipped'])
    
    print(f"\n综合校验: 日期和通道均通过的文件: {valid_channel_files}/{total}")
    
    # 写入错误详细报告
    write_validation_errors_report(directory, date_errors, channel_errors, combined_errors)
    
    # 3. 统计汇总
    complete_dates = sum(1 for r in results['completeness_check'] if r['is_complete'])
    
    print("\n" + "=" * 80)
    print("日前联络线计划校验汇总")
    print("=" * 80)
    print(f"目标日期总数: {len(target_dates)}")
    print(f"日期完整(22个文件): {complete_dates} 个")
    print(f"日前联络线计划文件总数: {total}")
    print(f"日期不一致: {len(date_errors)} 个文件")
    print(f"通道不一致: {len(channel_errors)} 个文件")
    print(f"缺失文件: {len(all_missing_files)} 个")
    print("=" * 80)
    
    return results

def main():
    """主函数"""
    print("=" * 80)
    print("Excel文件分析与日前联络线计划校验系统")
    print("=" * 80)
    
    # 目录配置
    directory = '/Users/x/Desktop/汉燧智能/夏初数据/doc_verify/exports'
    
    # 生成目标日期范围
    target_dates = generate_date_range()
    print(f"\n目标日期范围: 2023-01-01 至 2026-01-31")
    print(f"目标日期总数: {len(target_dates)} 天")
    
    # 获取所有文件
    excel_files = get_all_excel_files(directory)
    print(f"\n找到的Excel/CSV文件总数: {len(excel_files)}")
    
    # 筛选日前联络线计划文件
    riqian_files = [f for f in excel_files if f.get('is_riqian_lianluo', False)]
    other_files = [f for f in excel_files if not f.get('is_riqian_lianluo', False)]
    
    print(f"日前联络线计划文件: {len(riqian_files)} 个")
    print(f"其他文件: {len(other_files)} 个")
    
    # 执行日前联络线计划校验
    if riqian_files:
        results = validate_lianluo_files(directory, target_dates)
    else:
        print("\n未找到日前联络线计划文件，跳过专项校验")
    
    print("\n" + "=" * 80)
    print("校验完成！")
    print("=" * 80)

if __name__ == "__main__":
    main()


def parse_lianluo_filename(filename):
    """解析日前联络线计划文件名
    格式: 日前联络线计划_2025-01-01_通道名.xlsx
    返回: {'date': date_str, 'channel': channel_name}
    """
    if not filename.startswith('日前联络线计划_') or not filename.endswith('.xlsx'):
        return None
    
    try:
        parts = filename.replace('.xlsx', '').split('_')
        if len(parts) >= 3:
            date_str = parts[1]
            datetime.strptime(date_str, '%Y-%m-%d')  # 验证日期格式
            channel = '_'.join(parts[2:])  # 通道名可能包含下划线
            return {'date': date_str, 'channel': channel}
    except (ValueError, IndexError):
        pass
    
    return None

def check_lianluo_file_completeness(directory, target_date):
    """检查指定日期的日前联络线计划文件是否完整(22个标准通道)
    返回: (is_complete, info_dict)
    """
    # 获取所有该日期的文件
    date_files = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.startswith('日前联络线计划_') and file.endswith('.xlsx'):
                parsed = parse_lianluo_filename(file)
                if parsed and parsed['date'] == target_date:
                    date_files.append({
                        'filename': file,
                        'filepath': os.path.join(root, file),
                        'date': parsed['date'],
                        'channel': parsed['channel']
                    })
    
    # 统计各通道数量
    channel_counts = {}
    for f in date_files:
        ch = f['channel']
        channel_counts[ch] = channel_counts.get(ch, 0) + 1
    
    # 检查缺失通道
    found_channels = list(channel_counts.keys())
    missing_channels = [ch for ch in STANDARD_RIQIAN_CHANNELS if ch not in found_channels]
    
    # 检查重复通道
    duplicate_channels = [{'channel': ch, 'count': cnt} for ch, cnt in channel_counts.items() if cnt > 1]
    
    is_complete = len(date_files) == 22 and len(missing_channels) == 0 and len(duplicate_channels) == 0
    
    return is_complete, {
        'total_files': len(date_files),
        'missing_channels': missing_channels,
        'duplicate_channels': duplicate_channels,
        'found_channels': found_channels
    }

def main():
    """主函数 - 执行日前联络线计划文件校验"""
    print("\n" + "=" * 80)
    print("执行日前联络线计划校验...")
    print("=" * 80)


    """从日前联络线计划文件名中提取通道名称
    格式: 日前联络线计划_2025-01-01_通道名.xlsx
    返回: 通道名称或None
    """
    if '日前联络线计划' not in filename:
        return None
    
    try:
        # 格式: 日前联络线计划_2025-01-01_通道名.xlsx
        parts = filename.split('_')
        if len(parts) >= 3:
            # 通道名是最后一部分，去掉扩展名
            channel = '_'.join(parts[2:])  # 处理通道名中可能包含下划线的情况
            # 移除扩展名
            for ext in ['.xlsx', '.csv']:
                if channel.endswith(ext):
                    channel = channel.replace(ext, '')
                    break
            return channel
    except (ValueError, IndexError):
        return None
    
    return None


def is_riqian_lianluoxianhua_file(filename):
    """判断是否是日前联络线计划文件"""
    return '日前联络线计划' in filename and filename.endswith('.xlsx')

def get_all_excel_files(directory):
    """获取目录下所有Excel和CSV文件"""
    data_files = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            # 处理Excel文件
            if file.endswith('.xlsx'):
                file_path = os.path.join(root, file)
                date_str = extract_date_from_filename(file)
                if date_str:
                    is_riqian = is_riqian_lianluoxianhua_file(file)
                    parsed = parse_lianluo_filename(file) if is_riqian else None
                    channel = parsed['channel'] if parsed else None
                    
                    data_files.append({
                        'filename': file,
                        'filepath': file_path,
                        'date': date_str,
                        'file_type': 'excel',
                        'is_riqian_lianluoxianhua': is_riqian,
                        'channel': channel
                    })
            # 处理CSV文件
            elif file.endswith('.csv'):
                file_path = os.path.join(root, file)
                date_str = extract_date_from_filename(file)
                if date_str:
                    data_files.append({
                        'filename': file,
                        'filepath': file_path,
                        'date': date_str,
                        'file_type': 'csv'
                    })
    return data_files


def check_riqian_file_count_by_date(riqian_files, target_date):
    """检查指定日期的日前联络线计划文件数量
    返回: (is_complete, found_channels, missing_channels)
        is_complete: 是否完整(22个文件)
        found_channels: 找到的通道列表
        missing_channels: 缺失的通道列表
    """
    # 筛选该日期的文件
    date_files = [f for f in riqian_files if f['date'] == target_date]
    
    found_channels = []
    for f in date_files:
        if f['channel']:
            found_channels.append(f['channel'])
    
    # 检查缺失的通道
    missing_channels = []
    for channel in STANDARD_RIQIAN_CHANNELS:
        if channel not in found_channels:
            missing_channels.append(channel)
    
    is_complete = len(date_files) == 22 and len(missing_channels) == 0
    
    return is_complete, found_channels, missing_channels


def check_riqian_table_content(excel_file):
    """检查日前联络线计划文件表格内容中的日期和通道是否与文件名一致
    返回: (is_valid, result_info)
        is_valid: 是否有效(日期和通道都与文件名一致)
        result_info: 如果无效，返回包含表格中日期和通道的信息
    """
    try:
        file_date = datetime.strptime(excel_file['date'], '%Y-%m-%d').date()
        file_channel = excel_file.get('channel', '')
        
        if not file_channel:
            return False, {'error': '无法从文件名提取通道信息'}
        
        # 读取Excel文件
        xls = pd.ExcelFile(excel_file['filepath'])
        sheet_name = xls.sheet_names[0]
        
        # 查找日期和通道信息
        found_table_date = None
        found_table_channel = None
        
        # 尝试不同的header行
        for header_row in range(6):
            try:
                df = pd.read_excel(excel_file['filepath'], sheet_name=sheet_name, header=header_row, nrows=50)
                
                # 获取列名
                col_names = [str(col) for col in df.columns]
                
                # 扫描前20行数据
                scan_rows = min(20, len(df))
                
                for row_idx in range(scan_rows):
                    row = df.iloc[row_idx]
                    
                    for col_idx, val in enumerate(row):
                        if pd.isna(val):
                            continue
                        
                        # 检查日期
                        if found_table_date is None:
                            if isinstance(val, datetime):
                                if val.date() == file_date:
                                    found_table_date = val.date().strftime('%Y-%m-%d')
                            elif isinstance(val, str):
                                try:
                                    parsed_date = datetime.strptime(val, '%Y-%m-%d').date()
                                    if parsed_date == file_date:
                                        found_table_date = parsed_date.strftime('%Y-%m-%d')
                                except ValueError:
                                    try:
                                        parsed_date = datetime.strptime(val, '%Y/%m/%d').date()
                                        if parsed_date == file_date:
                                            found_table_date = parsed_date.strftime('%Y-%m-%d')
                                    except ValueError:
                                        pass
                        
                        # 检查通道 - 在单元格文本中查找通道名称
                        if found_table_channel is None and isinstance(val, str):
                            # 遍历所有标准通道名，检查是否在单元格文本中
                            for channel in STANDARD_RIQIAN_CHANNELS:
                                if channel in val:
                                    found_table_channel = channel
                                    break
                            # 也检查简化的通道名（去掉括号内容）
                            if found_table_channel is None:
                                for channel in STANDARD_RIQIAN_CHANNELS:
                                    base_name = channel.split('(')[0] if '(' in channel else channel
                                    if base_name in val:
                                        found_table_channel = channel
                                        break
                        
                        if found_table_date and found_table_channel:
                            break
                    
                    if found_table_date and found_table_channel:
                        break
                
                if found_table_date or found_table_channel:
                    break
                    
            except Exception:
                continue
        
        # 无header读取作为备选
        if found_table_date is None or found_table_channel is None:
            try:
                df = pd.read_excel(excel_file['filepath'], sheet_name=sheet_name, header=None, nrows=50)
                
                scan_rows = min(30, df.shape[0])
                
                for row_idx in range(scan_rows):
                    for col_idx in range(min(df.shape[1], 10)):
                        val = df.iloc[row_idx, col_idx]
                        
                        if pd.isna(val):
                            continue
                        
                        # 检查日期
                        if found_table_date is None:
                            if isinstance(val, datetime):
                                if val.date() == file_date:
                                    found_table_date = val.date().strftime('%Y-%m-%d')
                            elif isinstance(val, str):
                                try:
                                    parsed_date = datetime.strptime(val, '%Y-%m-%d').date()
                                    if parsed_date == file_date:
                                        found_table_date = parsed_date.strftime('%Y-%m-%d')
                                except ValueError:
                                    pass
                        
                        # 检查通道
                        if found_table_channel is None and isinstance(val, str):
                            for channel in STANDARD_RIQIAN_CHANNELS:
                                if channel in val:
                                    found_table_channel = channel
                                    break
                            if found_table_channel is None:
                                for channel in STANDARD_RIQIAN_CHANNELS:
                                    base_name = channel.split('(')[0] if '(' in channel else channel
                                    if base_name in val:
                                        found_table_channel = channel
                                        break
                        
                        if found_table_date and found_table_channel:
                            break
                    
                    if found_table_date and found_table_channel:
                        break
                        
            except Exception:
                pass
        
        # 汇总结果
        date_match = found_table_date == excel_file['date'] if found_table_date else False
        channel_match = found_table_channel == file_channel if found_table_channel else False
        
        if date_match and channel_match:
            return True, None
        else:
            return False, {
                'table_date': found_table_date,
                'table_channel': found_table_channel,
                'file_date': excel_file['date'],
                'file_channel': file_channel,
                'date_match': date_match,
                'channel_match': channel_match
            }
        
    except Exception as e:
        return False, {'error': str(e)}

def check_date_consistency(excel_file):
    """检查文件名日期与表格内日期是否一致
    返回: (is_consistent, result_info)
        is_consistent: 是否一致
        result_info: 如果一致返回None，不一致返回包含表格日期的信息
    """
    try:
        file_date = datetime.strptime(excel_file['date'], '%Y-%m-%d').date()
        file_type = excel_file.get('file_type', 'excel')
        
        # 根据文件类型选择读取方法
        if file_type == 'csv':
            return check_csv_date_consistency(excel_file, file_date)
        else:
            return check_excel_date_consistency(excel_file, file_date)
            
        # 读取Excel文件
        xls = pd.ExcelFile(excel_file['filepath'])
        sheet_name = xls.sheet_names[0]
        
        # 优化：设置最大读取行数为100，提高大文件处理效率
        max_rows = 100
        
        # 方法1: 尝试使用header=1（跳过第一行空行）
        try:
            df = pd.read_excel(excel_file['filepath'], sheet_name=sheet_name, header=1, nrows=max_rows)
            
            # 检查列名
            col_names = [str(col) for col in df.columns]
            # print(f"\n文件: {excel_file['filename']}")
            # print(f"列名: {col_names}")
            
            # 查找日期列
            date_col_idx = None
            for idx, col in enumerate(col_names):
                if '日期' in col or 'date' in col.lower():
                    date_col_idx = idx
                    break
            
            if date_col_idx is not None:
                # 检查日期列
                date_col = df.iloc[:, date_col_idx]
                table_dates = date_col.dropna().unique()
                
                # 检查是否有与文件名匹配的日期
                found_match = False
                for table_date in table_dates:
                    if isinstance(table_date, datetime):
                        if table_date.date() == file_date:
                            found_match = True
                            break
                    elif isinstance(table_date, str):
                        try:
                            table_date_obj = datetime.strptime(table_date, '%Y-%m-%d').date()
                            if table_date_obj == file_date:
                                found_match = True
                                break
                        except ValueError:
                            try:
                                table_date_obj = datetime.strptime(table_date, '%Y/%m/%d').date()
                                if table_date_obj == file_date:
                                    found_match = True
                                    break
                            except ValueError:
                                continue
                
                if found_match:
                    return True, None
                elif len(table_dates) > 0:
                    # 显示找到的日期
                    sample_dates = [str(d)[:10] for d in list(table_dates)[:5]]
                    return False, {'sample_dates': sample_dates, 'type': 'header1'}
        
        except Exception as e:
            print(f"header=1读取失败: {e}")
        
        # 方法2: 尝试其他header行
        for header_row in [0, 2, 3, 4, 5]:
            try:
                df = pd.read_excel(excel_file['filepath'], sheet_name=sheet_name, header=header_row, nrows=max_rows)
                
                # 检查是否有有效的列名（非数字）
                valid_header = False
                for col in df.columns[:5]:
                    col_str = str(col).strip()
                    if len(col_str) > 0 and not col_str.replace('.', '').isdigit():
                        valid_header = True
                        break
                
                if not valid_header:
                    continue
                
                # 在前20行数据中查找与文件名日期匹配的值
                scan_rows = min(20, len(df))
                found_table_dates = []
                found_match = False
                
                for row_idx in range(scan_rows):
                    row = df.iloc[row_idx]
                    for val in row:
                        if isinstance(val, datetime):
                            if val.date() == file_date:
                                found_match = True
                                break
                            found_table_dates.append(val.date().strftime('%Y-%m-%d'))
                        elif isinstance(val, str):
                            try:
                                val_date = datetime.strptime(val, '%Y-%m-%d').date()
                                if val_date == file_date:
                                    found_match = True
                                    break
                                found_table_dates.append(val_date.strftime('%Y-%m-%d'))
                            except ValueError:
                                try:
                                    val_date = datetime.strptime(val, '%Y/%m/%d').date()
                                    if val_date == file_date:
                                        found_match = True
                                        break
                                    found_table_dates.append(val_date.strftime('%Y-%m-%d'))
                                except ValueError:
                                    continue
                    
                    if found_match:
                        break
                
                if found_match:
                    return True, None
                elif found_table_dates:
                    unique_dates = list(set(found_table_dates))[:5]
                    return False, {'sample_dates': unique_dates, 'type': 'header_other', 'header_row': header_row}
                
                break
                
            except Exception:
                continue
        
        # 方法3: 无header读取
        try:
            df = pd.read_excel(excel_file['filepath'], sheet_name=sheet_name, header=None, nrows=max_rows)
            
            # 扫描前50行
            scan_rows = min(50, df.shape[0])
            found_dates = []
            found_match = False
            
            for row_idx in range(scan_rows):
                for col_idx in range(min(df.shape[1], 10)):
                    val = df.iloc[row_idx, col_idx]
                    
                    if isinstance(val, datetime):
                        if val.date() == file_date:
                            found_match = True
                            break
                        found_dates.append(val.date().strftime('%Y-%m-%d'))
                    elif isinstance(val, str):
                        try:
                            val_date = datetime.strptime(val, '%Y-%m-%d').date()
                            if val_date == file_date:
                                found_match = True
                                break
                            found_dates.append(val_date.strftime('%Y-%m-%d'))
                        except ValueError:
                            try:
                                val_date = datetime.strptime(val, '%Y/%m/%d').date()
                                if val_date == file_date:
                                    found_match = True
                                    break
                                found_dates.append(val_date.strftime('%Y-%m-%d'))
                            except ValueError:
                                # 尝试从字符串中提取日期
                                date_match = re.search(r'(\d{4}-\d{2}-\d{2})', val)
                                if date_match:
                                    try:
                                        val_date = datetime.strptime(date_match.group(1), '%Y-%m-%d').date()
                                        if val_date == file_date:
                                            found_match = True
                                            break
                                        found_dates.append(val_date.strftime('%Y-%m-%d'))
                                    except ValueError:
                                        pass
                
                if found_match:
                    break
            
            if found_match:
                return True, None
            elif found_dates:
                unique_dates = list(set(found_dates))[:5]
                return False, {'sample_dates': unique_dates, 'type': 'no_header'}
            
        except Exception as e:
            return False, {'error': str(e), 'type': 'error'}
        
        # 所有方法都失败，尝试检查表头行
        return check_header_row_for_date(excel_file, file_date, xls, sheet_name)
        
    except Exception as e:
        return False, f"处理错误: {str(e)}"


def check_csv_date_consistency(csv_file, file_date):
    """检查CSV文件日期一致性（支持特殊格式的CSV）"""
    try:
        # 读取CSV文件，尝试不同编码
        encodings = ['utf-8-sig', 'utf-8', 'gbk', 'gb2312', 'gb18030']
        df = None
        
        for encoding in encodings:
            try:
                df = pd.read_csv(csv_file['filepath'], encoding=encoding, header=None)
                break
            except UnicodeDecodeError:
                continue
        
        if df is None:
            return False, {'error': '无法读取CSV文件编码', 'type': 'csv_encoding_error'}
        
        # CSV文件格式分析（示例格式）：
        # 第0行: 元数据（更新时间等）
        # 第1行: 列名（日期,出清概况,更新时间）
        # 第2行起: 实际数据
        
        # 查找日期列
        date_col_idx = None
        target_date_str = csv_file['date']
        
        # 方法1: 扫描前3行查找包含"日期"的列
        scan_rows = min(3, len(df))
        
        for row_idx in range(scan_rows):
            row = df.iloc[row_idx]
            for col_idx, val in enumerate(row):
                if isinstance(val, str) and '日期' in val:
                    # 检查这一列的值
                    if row_idx == 1:  # 列名行
                        date_col_idx = col_idx
                        break
            if date_col_idx is not None:
                break
        
        # 方法2: 如果没找到，扫描前20行查找与文件名匹配的日期
        if date_col_idx is None:
            scan_rows = min(20, len(df))
            found_match = False
            found_table_dates = []
            
            for row_idx in range(scan_rows):
                row = df.iloc[row_idx]
                for col_idx, val in enumerate(row):
                    if isinstance(val, datetime):
                        if val.date() == file_date:
                            found_match = True
                            break
                        found_table_dates.append(val.date().strftime('%Y-%m-%d'))
                    elif isinstance(val, str):
                        # 尝试提取日期
                        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', val)
                        if date_match:
                            date_str = date_match.group(1)
                            try:
                                parsed_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                                if parsed_date == file_date:
                                    found_match = True
                                    break
                                found_table_dates.append(date_str)
                            except ValueError:
                                pass
                
                if found_match:
                    break
            
            if found_match:
                return True, None
            elif found_table_dates:
                unique_dates = list(set(found_table_dates))[:5]
                return False, {'sample_dates': unique_dates, 'type': 'csv_scan'}
        
        # 方法3: 如果找到日期列，检查该列数据
        if date_col_idx is not None:
            # 获取该列的所有数据（从第2行开始，跳过元数据和列名）
            col_data = df.iloc[2:, date_col_idx].dropna()
            
            found_match = False
            table_dates = []
            
            for val in col_data:
                if isinstance(val, datetime):
                    val_str = val.date().strftime('%Y-%m-%d')
                    table_dates.append(val_str)
                    if val.date() == file_date:
                        found_match = True
                        break
                elif isinstance(val, str):
                    # 尝试提取日期
                    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', val)
                    if date_match:
                        date_str = date_match.group(1)
                        try:
                            parsed_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                            table_dates.append(date_str)
                            if parsed_date == file_date:
                                found_match = True
                                break
                        except ValueError:
                            pass
            
            if found_match:
                return True, None
            elif table_dates:
                unique_dates = list(set(table_dates))[:5]
                return False, {'sample_dates': unique_dates, 'type': 'csv_date_col'}
        
        return False, {'error': 'CSV表格中未找到日期数据', 'type': 'csv_no_data'}
        
    except Exception as e:
        return False, {'error': str(e), 'type': 'csv_error'}

def check_header_row_for_date(excel_file, file_date, xls, sheet_name):
    """检查表头行中是否包含日期信息（用于实时各时段出清现货电量等文件）"""
    try:
        # 读取前5行，检查表头中是否包含日期
        df_header = pd.read_excel(excel_file['filepath'], sheet_name=sheet_name, header=None, nrows=5)
        
        # 扫描前5行的所有单元格
        for row_idx in range(min(5, len(df_header))):
            for col_idx in range(min(20, df_header.shape[1])):
                val = df_header.iloc[row_idx, col_idx]
                
                if pd.isna(val):
                    continue
                
                # 检查是否是日期对象
                if isinstance(val, datetime):
                    if val.date() == file_date:
                        return True, None
                    continue
                
                # 检查字符串中是否包含日期
                if isinstance(val, str):
                    # 尝试直接解析日期
                    try:
                        val_date = datetime.strptime(val, '%Y-%m-%d').date()
                        if val_date == file_date:
                            return True, None
                        continue
                    except ValueError:
                        pass
                    
                    try:
                        val_date = datetime.strptime(val, '%Y/%m/%d').date()
                        if val_date == file_date:
                            return True, None
                        continue
                    except ValueError:
                        pass
                    
                    # 尝试从字符串中提取日期（如"2026-01-01 实时各时段出清现货电量"）
                    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', val)
                    if date_match:
                        try:
                            val_date = datetime.strptime(date_match.group(1), '%Y-%m-%d').date()
                            if val_date == file_date:
                                return True, None
                        except ValueError:
                            continue
        
        # 未找到匹配的日期
        return False, {'error': '表格中未找到日期数据', 'type': 'no_date_data'}
    except Exception:
        return False, {'error': '检查表头行时发生错误', 'type': 'header_check_error'}

def check_excel_date_consistency(excel_file, file_date):
    """检查Excel文件日期一致性（原有逻辑）"""
    try:
        # 读取Excel文件
        xls = pd.ExcelFile(excel_file['filepath'])
        sheet_name = xls.sheet_names[0]
        
        # 优化：设置最大读取行数为100，提高大文件处理效率
        max_rows = 100
        
        # 方法1: 尝试使用header=1（跳过第一行空行）
        try:
            df = pd.read_excel(excel_file['filepath'], sheet_name=sheet_name, header=1, nrows=max_rows)
            
            # 检查列名
            col_names = [str(col) for col in df.columns]
            # print(f"\n文件: {excel_file['filename']}")
            # print(f"列名: {col_names}")
            
            # 查找日期列
            date_col_idx = None
            for idx, col in enumerate(col_names):
                if '日期' in col or 'date' in col.lower():
                    date_col_idx = idx
                    break
            
            if date_col_idx is not None:
                # 检查日期列
                date_col = df.iloc[:, date_col_idx]
                table_dates = date_col.dropna().unique()
                
                # 检查是否有与文件名匹配的日期
                found_match = False
                for table_date in table_dates:
                    if isinstance(table_date, datetime):
                        if table_date.date() == file_date:
                            found_match = True
                            break
                    elif isinstance(table_date, str):
                        try:
                            table_date_obj = datetime.strptime(table_date, '%Y-%m-%d').date()
                            if table_date_obj == file_date:
                                found_match = True
                                break
                        except ValueError:
                            try:
                                table_date_obj = datetime.strptime(table_date, '%Y/%m/%d').date()
                                if table_date_obj == file_date:
                                    found_match = True
                                    break
                            except ValueError:
                                continue
                
                if found_match:
                    return True, None
                elif len(table_dates) > 0:
                    # 显示找到的日期，只包含成功解析的日期
                    sample_dates = []
                    for d in list(table_dates)[:10]:  # 检查更多样本
                        if isinstance(d, datetime):
                            sample_dates.append(d.date().strftime('%Y-%m-%d'))
                        elif isinstance(d, str):
                            try:
                                datetime.strptime(d, '%Y-%m-%d')
                                sample_dates.append(d[:10])
                            except ValueError:
                                try:
                                    datetime.strptime(d, '%Y/%m/%d')
                                    sample_dates.append(d[:10])
                                except ValueError:
                                    # 尝试从字符串中提取日期
                                    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', d)
                                    if date_match:
                                        try:
                                            datetime.strptime(date_match.group(1), '%Y-%m-%d')
                                            sample_dates.append(date_match.group(1))
                                        except ValueError:
                                            continue
                    if sample_dates:
                        return False, {'sample_dates': sample_dates[:5], 'type': 'header1'}
                    else:
                        # 没有找到有效日期，尝试检查表头行
                        return check_header_row_for_date(excel_file, file_date, xls, sheet_name)
        
        except Exception as e:
            print(f"header=1读取失败: {e}")
        
        # 方法2: 尝试其他header行
        for header_row in [0, 2, 3, 4, 5]:
            try:
                df = pd.read_excel(excel_file['filepath'], sheet_name=sheet_name, header=header_row, nrows=max_rows)
                
                # 检查是否有有效的列名（非数字）
                valid_header = False
                for col in df.columns[:5]:
                    col_str = str(col).strip()
                    if len(col_str) > 0 and not col_str.replace('.', '').isdigit():
                        valid_header = True
                        break
                
                if not valid_header:
                    continue
                
                # 在前20行数据中查找与文件名日期匹配的值
                scan_rows = min(20, len(df))
                found_table_dates = []
                found_match = False
                
                for row_idx in range(scan_rows):
                    row = df.iloc[row_idx]
                    for val in row:
                        if isinstance(val, datetime):
                            if val.date() == file_date:
                                found_match = True
                                break
                            found_table_dates.append(val.date().strftime('%Y-%m-%d'))
                        elif isinstance(val, str):
                            try:
                                val_date = datetime.strptime(val, '%Y-%m-%d').date()
                                if val_date == file_date:
                                    found_match = True
                                    break
                                found_table_dates.append(val_date.strftime('%Y-%m-%d'))
                            except ValueError:
                                try:
                                    val_date = datetime.strptime(val, '%Y/%m/%d').date()
                                    if val_date == file_date:
                                        found_match = True
                                        break
                                    found_table_dates.append(val_date.strftime('%Y-%m-%d'))
                                except ValueError:
                                    continue
                    
                    if found_match:
                        break
                
                if found_match:
                    return True, None
                elif found_table_dates:
                    unique_dates = list(set(found_table_dates))[:5]
                    return False, {'sample_dates': unique_dates, 'type': 'header_other', 'header_row': header_row}
                
                break
                
            except Exception:
                continue
        
        # 方法3: 无header读取
        try:
            df = pd.read_excel(excel_file['filepath'], sheet_name=sheet_name, header=None, nrows=max_rows)
            
            # 扫描前50行
            scan_rows = min(50, df.shape[0])
            found_dates = []
            found_match = False
            
            for row_idx in range(scan_rows):
                for col_idx in range(min(df.shape[1], 10)):
                    val = df.iloc[row_idx, col_idx]
                    
                    if isinstance(val, datetime):
                        if val.date() == file_date:
                            found_match = True
                            break
                        found_dates.append(str(val)[:10])
                    elif isinstance(val, str):
                        try:
                            val_date = datetime.strptime(val, '%Y-%m-%d').date()
                            if val_date == file_date:
                                found_match = True
                                break
                            found_dates.append(val_date.strftime('%Y-%m-%d'))
                        except ValueError:
                            pass
            
            if found_match:
                return True, None
            elif found_dates:
                unique_dates = list(set(found_dates))[:5]
                return False, {'sample_dates': unique_dates, 'type': 'no_header'}
            
        except Exception as e:
            return False, {'error': str(e), 'type': 'error'}
        
        return False, {'error': '表格中未找到日期数据', 'type': 'no_data'}
        
    except Exception as e:
        return False, f"处理错误: {str(e)}"

def get_correct_date_from_table(excel_file):
    """从表格中提取正确的日期信息
    返回: 正确的日期字符串 (格式: YYYY-MM-DD)，如果无法提取则返回None
    """
    try:
        # 根据文件类型选择读取方法
        file_type = excel_file.get('file_type', 'excel')
        
        if file_type == 'csv':
            return get_correct_date_from_csv(excel_file)
        else:
            return get_correct_date_from_excel(excel_file)
            
    except Exception as e:
        print(f"  提取日期错误: {e}")
        return None

def get_correct_date_from_csv(csv_file):
    """从CSV文件中提取正确的日期信息（支持特殊格式的CSV）"""
    try:
        # 读取CSV文件，尝试不同编码
        encodings = ['utf-8-sig', 'utf-8', 'gbk', 'gb2312', 'gb18030']
        df = None
        
        for encoding in encodings:
            try:
                df = pd.read_csv(csv_file['filepath'], encoding=encoding, header=None)
                break
            except UnicodeDecodeError:
                continue
        
        if df is None:
            return None
        
        # CSV文件格式分析（示例格式）：
        # 第0行: 元数据（更新时间等）
        # 第1行: 列名（日期,出清概况,更新时间）
        # 第2行起: 实际数据
        
        # 查找"日期"列
        date_col_idx = None
        
        # 扫描第1行（列名行）查找"日期"列
        if len(df) >= 2:
            header_row = df.iloc[1]
            for col_idx, val in enumerate(header_row):
                if isinstance(val, str) and '日期' in val:
                    date_col_idx = col_idx
                    break
        
        # 如果找到日期列，从第2行开始提取第一个有效日期
        if date_col_idx is not None:
            data_rows = df.iloc[2:]
            
            for val in data_rows.iloc[:, date_col_idx]:
                if pd.isna(val):
                    continue
                    
                if isinstance(val, datetime):
                    return val.strftime('%Y-%m-%d')
                elif isinstance(val, str):
                    # 尝试提取日期
                    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', val)
                    if date_match:
                        try:
                            datetime.strptime(date_match.group(1), '%Y-%m-%d')
                            return date_match.group(1)
                        except ValueError:
                            continue
        
        # 如果没找到日期列，扫描前20行查找第一个有效日期
        scan_rows = min(20, len(df))
        
        for row_idx in range(scan_rows):
            row = df.iloc[row_idx]
            for val in row:
                if pd.isna(val):
                    continue
                    
                if isinstance(val, datetime):
                    return val.strftime('%Y-%m-%d')
                elif isinstance(val, str):
                    # 尝试提取日期
                    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', val)
                    if date_match:
                        try:
                            datetime.strptime(date_match.group(1), '%Y-%m-%d')
                            return date_match.group(1)
                        except ValueError:
                            continue
        
        return None
        
    except Exception as e:
        print(f"  CSV提取日期错误: {e}")
        return None

def get_correct_date_from_excel(excel_file):
    """从Excel文件中提取正确的日期信息（原有逻辑）"""
    # 读取Excel文件
    xls = pd.ExcelFile(excel_file['filepath'])
    sheet_name = xls.sheet_names[0]
    max_rows = 100
    
    # 方法1: 尝试使用header=1（跳过第一行空行）
    try:
        df = pd.read_excel(excel_file['filepath'], sheet_name=sheet_name, header=1, nrows=max_rows)
        
        # 检查列名
        col_names = [str(col) for col in df.columns]
        
        # 查找日期列
        date_col_idx = None
        for idx, col in enumerate(col_names):
            if '日期' in col or 'date' in col.lower():
                date_col_idx = idx
                break
        
        if date_col_idx is not None:
            # 获取日期列的第一个有效日期
            date_col = df.iloc[:, date_col_idx]
            for table_date in date_col.dropna().unique():
                if isinstance(table_date, datetime):
                    return table_date.strftime('%Y-%m-%d')
                elif isinstance(table_date, str):
                    try:
                        return datetime.strptime(table_date, '%Y-%m-%d').strftime('%Y-%m-%d')
                    except ValueError:
                        try:
                            return datetime.strptime(table_date, '%Y/%m/%d').strftime('%Y-%m-%d')
                        except ValueError:
                            continue
    except Exception:
        pass
    
    # 方法2: 尝试其他header行
    for header_row in [0, 2, 3, 4, 5]:
        try:
            df = pd.read_excel(excel_file['filepath'], sheet_name=sheet_name, header=header_row, nrows=max_rows)
            
            # 检查是否有有效的列名（非数字）
            valid_header = False
            for col in df.columns[:5]:
                col_str = str(col).strip()
                if len(col_str) > 0 and not col_str.replace('.', '').isdigit():
                    valid_header = True
                    break
            
            if not valid_header:
                continue
            
            # 在前20行数据中查找日期值
            scan_rows = min(20, len(df))
            for row_idx in range(scan_rows):
                row = df.iloc[row_idx]
                for val in row:
                    if isinstance(val, datetime):
                        return val.strftime('%Y-%m-%d')
                    elif isinstance(val, str):
                        try:
                            val_date = datetime.strptime(val, '%Y-%m-%d')
                            return val_date.strftime('%Y-%m-%d')
                        except ValueError:
                            try:
                                val_date = datetime.strptime(val, '%Y/%m/%d')
                                return val_date.strftime('%Y-%m-%d')
                            except ValueError:
                                continue
            
            break
            
        except Exception:
            continue
    
    # 方法3: 无header读取
    try:
        df = pd.read_excel(excel_file['filepath'], sheet_name=sheet_name, header=None, nrows=max_rows)
        
        # 扫描前50行
        scan_rows = min(50, df.shape[0])
        
        for row_idx in range(scan_rows):
            for col_idx in range(min(df.shape[1], 10)):
                val = df.iloc[row_idx, col_idx]
                
                if isinstance(val, datetime):
                    return val.strftime('%Y-%m-%d')
                elif isinstance(val, str):
                    try:
                        val_date = datetime.strptime(val, '%Y-%m-%d')
                        return val_date.strftime('%Y-%m-%d')
                    except ValueError:
                        pass
        
    except Exception as e:
        pass
    
    return None


def get_unique_temp_filename(filepath):
    """生成唯一的temp_前缀文件名，避免冲突
    返回: 新的文件路径
    """
    if not os.path.exists(filepath):
        return filepath
    
    dirname = os.path.dirname(filepath)
    basename = os.path.basename(filepath)
    
    # 生成 temp_前缀文件名
    temp_name = f"temp_{basename}"
    temp_filepath = os.path.join(dirname, temp_name)
    
    if not os.path.exists(temp_filepath):
        return temp_filepath
    
    # 如果已存在，添加数字后缀
    counter = 1
    while counter <= 100:
        temp_name = f"temp_{basename.rstrip('.xlsx')}_{counter}.xlsx"
        temp_filepath = os.path.join(dirname, temp_name)
        if not os.path.exists(temp_filepath):
            return temp_filepath
        counter += 1
    
    # 如果尝试太多次，使用时间戳
    import time
    timestamp = int(time.time())
    temp_name = f"temp_{basename.rstrip('.xlsx')}_{timestamp}.xlsx"
    temp_filepath = os.path.join(dirname, temp_name)
    
    return temp_filepath


def repair_filename(excel_file, correct_date):
    """修复文件名，将文件名中的日期替换为正确的日期
    返回: (result, new_filename, extra_info)
        result: 'renamed'(成功重命名), 'conflict_temp'(冲突已用temp处理), 'skipped'(跳过), 'error'(错误)
        new_filename: 新的文件名
        extra_info: 额外信息
    """
    try:
        old_filepath = excel_file['filepath']
        old_filename = excel_file['filename']
        
        # 构建新的文件名
        if old_filename.startswith('实时各时段出清现货电量_'):
            prefix = '实时各时段出清现货电量_'
        elif old_filename.startswith('实时输电断面约束及阻塞_'):
            prefix = '实时输电断面约束及阻塞_'
        elif old_filename.startswith('日前机组开机安排_'):
            prefix = '日前机组开机安排_'
        else:
            # 通用处理：替换日期部分
            date_str = excel_file['date']
            new_filename = old_filename.replace(f'_{date_str}.xlsx', f'_{correct_date}.xlsx')
            new_filepath = os.path.join(os.path.dirname(old_filepath), new_filename)
            
            if old_filepath != new_filepath:
                # 检查目标文件是否存在
                if os.path.exists(new_filepath):
                    # 冲突处理：使用_temp后缀
                    temp_filepath = get_unique_temp_filename(new_filepath)
                    os.rename(old_filepath, temp_filepath)
                    temp_filename = os.path.basename(temp_filepath)
                    return 'conflict_temp', temp_filename, {'original_target': new_filepath}
                else:
                    os.rename(old_filepath, new_filepath)
                    return 'renamed', new_filename, None
            return 'skipped', None, None
        
        new_filename = f"{prefix}{correct_date}.xlsx"
        new_filepath = os.path.join(os.path.dirname(old_filepath), new_filename)
        
        # 检查目标文件是否存在
        if os.path.exists(new_filepath) and new_filepath != old_filepath:
            # 冲突处理：使用_temp后缀
            temp_filepath = get_unique_temp_filename(new_filepath)
            os.rename(old_filepath, temp_filepath)
            temp_filename = os.path.basename(temp_filepath)
            return 'conflict_temp', temp_filename, {'original_target': new_filepath}
        
        # 重命名文件
        if old_filepath != new_filepath:
            os.rename(old_filepath, new_filepath)
            return 'renamed', new_filename, None
        
        return 'skipped', None, None
        
    except Exception as e:
        print(f"❌ 修复失败: {e}")
        return 'error', None, None

def delete_file_with_confirm(file_path, filename):
    """删除文件前确认"""
    try:
        file_size = os.path.getsize(file_path)
        file_size_str = f"{file_size / 1024:.2f} KB" if file_size < 1024*1024 else f"{file_size / (1024*1024):.2f} MB"
        
        print(f"\n⚠️  发现文件名冲突!")
        print(f"  要保留的文件: {filename}")
        print(f"  将被删除的冲突文件: {os.path.basename(file_path)}")
        print(f"  文件大小: {file_size_str}")
        
        user_input = input("\n是否删除冲突文件? (输入 'y' 确认删除, 其他键跳过): ")
        
        if user_input.lower() == 'y':
            try:
                os.remove(file_path)
                print(f"✅ 已删除冲突文件: {os.path.basename(file_path)}")
                return True
            except Exception as e:
                print(f"❌ 删除失败: {e}")
                return False
        else:
            print("已跳过删除操作")
            return False
    except Exception as e:
        print(f"❌ 删除确认失败: {e}")
        return False

def get_temp_filename(original_filename, correct_date):
    """生成temp前缀的文件名
    格式: temp_原文件名前缀_正确日期.xlsx
    例如: temp_日前机组开机安排_2025-09-22.xlsx
    """
    # 获取文件前缀
    if original_filename.startswith('实时各时段出清现货电量_'):
        prefix = '实时各时段出清现货电量_'
    elif original_filename.startswith('实时输电断面约束及阻塞_'):
        prefix = '实时输电断面约束及阻塞_'
    elif original_filename.startswith('日前机组开机安排_'):
        prefix = '日前机组开机安排_'
    else:
        # 通用处理
        prefix = original_filename.split('_')[0] + '_' if '_' in original_filename else ''
    
    return f"temp_{prefix}{correct_date}.xlsx"

def repair_filename_step1(excel_file, correct_date):
    """步骤1: 将文件名重命名为temp_前缀格式
    返回: (success, temp_filename, target_filepath)
        success: 是否成功
        temp_filename: 生成的临时文件名
        target_filepath: 临时文件的完整路径
    """
    try:
        old_filepath = excel_file['filepath']
        old_filename = excel_file['filename']
        
        # 生成temp前缀的新文件名
        temp_filename = get_temp_filename(old_filename, correct_date)
        temp_filepath = os.path.join(os.path.dirname(old_filepath), temp_filename)
        
        # 检查临时文件是否已存在
        if os.path.exists(temp_filepath):
            print(f"  ⚠️  临时文件已存在: {temp_filename}")
            return 'temp_exists', temp_filename, temp_filepath
        
        # 执行重命名
        os.rename(old_filepath, temp_filepath)
        return 'success', temp_filename, temp_filepath
        
    except Exception as e:
        print(f"  ❌ 步骤1重命名失败: {e}")
        return 'error', None, None

def repair_filename_step2(temp_filename, temp_filepath, target_filepath):
    """步骤2: 将temp前缀文件名重命名为正常文件名
    如果目标文件存在冲突，则删除temp文件
    返回: (result, final_filename)
        result: 'success'(成功), 'deleted'(因冲突删除), 'skipped'(跳过)
        final_filename: 最终的文件名
    """
    try:
        target_filename = os.path.basename(target_filepath)
        
        # 检查目标文件是否存在
        if os.path.exists(target_filepath):
            # 目标文件已存在，删除临时文件
            try:
                os.remove(temp_filepath)
                print(f"  ⚠️  文件名冲突，已删除临时文件:")
                print(f"     临时文件: {temp_filename}")
                print(f"     冲突文件: {target_filename} (已存在)")
                return 'deleted', temp_filename
            except Exception as e:
                print(f"  ❌ 删除临时文件失败: {e}")
                return 'skipped', temp_filename
        else:
            # 目标文件不存在，执行重命名
            os.rename(temp_filepath, target_filepath)
            print(f"  ✅ 已完成: {temp_filename} -> {target_filename}")
            return 'success', target_filename
        
    except Exception as e:
        print(f"  ❌ 步骤2重命名失败: {e}")
        return 'error', None

def repair_inconsistent_files(inconsistent_files):
    """修复文件名与表格日期不一致的文件
    采用两步修复策略:
    步骤1: 将文件重命名为 temp_前缀格式
    步骤2: 移除temp前缀，如果目标文件已存在则删除temp文件
    返回: 成功修复的文件数量
    """
    print("\n" + "=" * 60)
    print("文件修复功能 (两步修复策略)")
    print("=" * 60)
    
    if not inconsistent_files:
        print("\n没有需要修复的文件。")
        return 0
    
    print(f"\n发现 {len(inconsistent_files)} 个文件需要修复。")
    print("\n修复规则:")
    print("  步骤1: 以表格内日期为准，将文件名重命名为 temp_前缀格式")
    print("  步骤2: 移除temp前缀，如遇文件名冲突则删除临时文件")
    
    # 统计各类型文件
    file_types = {}
    for item in inconsistent_files:
        filename = item['filename']
        if '日前机组开机安排' in filename:
            file_type = '日前机组开机安排'
        elif '实时输电断面约束及阻塞' in filename:
            file_type = '实时输电断面约束及阻塞'
        elif '实时各时段出清现货电量' in filename:
            file_type = '实时各时段出清现货电量'
        else:
            file_type = '其他'
        
        if file_type not in file_types:
            file_types[file_type] = []
        file_types[file_type].append(item)
    
    print(f"\n文件类型统计:")
    for file_type, files in file_types.items():
        print(f"  - {file_type}: {len(files)} 个文件")
    
    # 询问用户是否确认修复
    print("\n" + "-" * 60)
    user_input = input("是否开始修复所有文件? (输入 'y' 确认修复, 其他键取消): ")
    
    if user_input.lower() != 'y':
        print("\n已取消修复操作。")
        return 0
    
    # ==================== 步骤1: 生成临时文件 ====================
    print("\n" + "=" * 60)
    print("步骤1: 生成临时文件 (temp_前缀格式)")
    print("=" * 60)
    
    temp_files = []
    step1_success = 0
    step1_error = 0
    step1_skipped = 0
    
    for i, item in enumerate(inconsistent_files, 1):
        print(f"\n[{i}/{len(inconsistent_files)}] 处理: {item['filename']}")
        
        # 构建excel_file信息
        excel_file = {
            'filename': item['filename'],
            'filepath': item['filepath'],
            'date': item['filename'].split('_')[-1].replace('.xlsx', '')
        }
        
        # 从表格中提取正确的日期
        correct_date = get_correct_date_from_table(excel_file)
        
        if correct_date:
            print(f"  表格日期: {correct_date}")
            result, temp_filename, temp_filepath = repair_filename_step1(excel_file, correct_date)
            
            if result == 'success':
                print(f"  ✅ 已生成临时文件: {temp_filename}")
                temp_files.append({
                    'original_filename': item['filename'],
                    'temp_filename': temp_filename,
                    'temp_filepath': temp_filepath,
                    'target_filename': get_temp_filename(item['filename'], correct_date).replace('temp_', '', 1),
                    'target_filepath': os.path.join(os.path.dirname(temp_filepath), get_temp_filename(item['filename'], correct_date).replace('temp_', '', 1))
                })
                step1_success += 1
            elif result == 'temp_exists':
                print(f"  ⏭️  临时文件已存在，跳过")
                step1_skipped += 1
            else:
                print(f"  ❌ 步骤1失败")
                step1_error += 1
        else:
            print(f"  ❌ 无法从表格中提取日期")
            step1_error += 1
    
    print("\n" + "-" * 60)
    print(f"步骤1完成: 成功 {step1_success}, 跳过 {step1_skipped}, 错误 {step1_error}")
    
    # ==================== 步骤2: 移除temp前缀 ====================
    print("\n" + "=" * 60)
    print("步骤2: 移除temp前缀 (处理文件名冲突)")
    print("=" * 60)
    
    step2_success = 0
    step2_deleted = 0
    step2_error = 0
    step2_skipped = 0
    
    for i, temp_info in enumerate(temp_files, 1):
        print(f"\n[{i}/{len(temp_files)}] 处理: {temp_info['temp_filename']}")
        
        result, final_filename = repair_filename_step2(
            temp_info['temp_filename'],
            temp_info['temp_filepath'],
            temp_info['target_filepath']
        )
        
        if result == 'success':
            step2_success += 1
        elif result == 'deleted':
            step2_deleted += 1
        elif result == 'error':
            step2_error += 1
        else:
            step2_skipped += 1
    
    # 修复完成统计
    total_repaired = step1_success
    print("\n" + "=" * 60)
    print("修复完成统计")
    print("=" * 60)
    print(f"\n步骤1 (生成temp文件):")
    print(f"  - 成功: {step1_success} 个文件")
    print(f"  - 跳过: {step1_skipped} 个文件")
    print(f"  - 错误: {step1_error} 个文件")
    
    print(f"\n步骤2 (移除temp前缀):")
    print(f"  - 成功重命名: {step2_success} 个文件")
    print(f"  - 因冲突删除: {step2_deleted} 个文件")
    print(f"  - 跳过: {step2_skipped} 个文件")
    print(f"  - 错误: {step2_error} 个文件")
    
    print(f"\n总计:")
    print(f"  - 处理文件: {len(inconsistent_files)} 个")
    print(f"  - 成功修复: {total_repaired} 个文件")
    print(f"  - 因冲突删除: {step2_deleted} 个临时文件")
    
    return total_repaired

def main():
    print("=" * 60)
    print("Excel文件分析报告")
    print("=" * 60)
    
    # 获取所有Excel文件
    dir_path = '/Users/x/Desktop/汉燧智能/夏初数据/doc_verify/exports'
    excel_files = get_all_excel_files(dir_path)
    
    print(f"\n找到的Excel文件总数: {len(excel_files)}")
    
    # 生成2023.01.01至2026.01.31的所有日期
    all_target_dates = generate_date_range()
    print(f"目标日期范围(2023.01.01-2026.01.31)总天数: {len(all_target_dates)}")
    
    # 检查缺失的日期
    file_dates = [f['date'] for f in excel_files]
    missing_dates = [date for date in all_target_dates if date not in file_dates]
    
    print("\n" + "=" * 60)
    print("1. 日期覆盖情况检查 (2023.01.01-2026.01.31)")
    print("=" * 60)
    
    if missing_dates:
        print(f"\n❌ 发现缺失的日期 ({len(missing_dates)} 个):")
        # 按月份分组显示
        months = {}
        for date in missing_dates:
            month = date[:7]  # 提取年月
            if month not in months:
                months[month] = []
            months[month].append(date)
        
        for month in sorted(months.keys()):
            print(f"\n{month}:")
            for date in months[month]:
                day = int(date[-2:])
                weekday = ['一','二','三','四','五','六','日'][datetime.strptime(date, '%Y-%m-%d').weekday()]
                print(f"  - {month}-{day:02d} (周{weekday})")
    else:
        print("\n✅ 2023.01.01-2026.01.31 范围内所有日期都已覆盖，无缺失日期！")
    
    # 检查文件名日期与表格内日期一致性
    print("\n" + "=" * 60)
    print("2. 文件名与表格日期一致性检查")
    print("=" * 60)
    
    # 筛选非日前联络线计划的文件进行日期一致性检查
    non_riqian_files = [f for f in excel_files if not f.get('is_riqian_lianluoxianhua', False)]
    riqian_files = [f for f in excel_files if f.get('is_riqian_lianluoxianhua', False)]
    
    inconsistent_files = []
    consistent_count = 0
    
    total = len(non_riqian_files)
    for i, excel_file in enumerate(non_riqian_files, 1):
        is_consistent, reason = check_date_consistency(excel_file)
        if is_consistent:
            consistent_count += 1
        else:
            inconsistent_files.append({
                'filename': excel_file['filename'],
                'filepath': excel_file['filepath'],
                'reason': reason
            })
        
        # 每处理100个文件显示进度
        if i % 100 == 0 or i == total:
            print(f"进度: {i}/{total} 已处理...")
    
    print(f"\n一致性检查完成: {consistent_count}/{total} 文件日期一致")
    
    if inconsistent_files:
        print(f"\n❌ 发现文件名与表格日期不一致的文件 ({len(inconsistent_files)} 个):")
        print("-" * 60)
        
        for item in inconsistent_files:
            filename = item['filename']
            reason_info = item['reason']
            
            # 解析reason信息
            if isinstance(reason_info, dict):
                sample_dates = reason_info.get('sample_dates', [])
                date_info = f"表格日期示例: {sample_dates}" if sample_dates else reason_info.get('error', '未知错误')
            else:
                date_info = str(reason_info)
            
            print(f"\n文件: {filename}")
            print(f"原因: {date_info}")
        
        # 询问用户是否需要修复
        print("\n" + "=" * 60)
        user_input = input(f"是否修复这 {len(inconsistent_files)} 个文件名? (输入 'y' 确认修复, 其他键跳过): ")
        
        if user_input.lower() == 'y':
            # 调用修复函数
            repaired_count = repair_inconsistent_files(inconsistent_files)
            print(f"\n✅ 已成功修复 {repaired_count} 个文件")
        else:
            print("\n⏭️  已跳过修复操作")
    else:
        print("\n✅ 所有文件的文件名日期与表格内日期一致！")
    
    # ==================== 日前联络线计划文件校验 ====================
    print("\n" + "=" * 60)
    print("3. 日前联络线计划文件校验")
    print("=" * 60)
    
    if not riqian_files:
        print("\n未找到日前联络线计划文件")
    else:
        # 获取所有有日前联络线计划文件的日期
        riqian_dates = sorted(set(f['date'] for f in riqian_files))
        print(f"\n找到日前联络线计划文件的日期: {len(riqian_dates)} 个")
        
        # 3.1 检查每个日期是否有22个文件
        print("\n--- 3.1 文件数量检查 ---")
        
        date_file_count = {}
        for date in riqian_dates:
            count = len([f for f in riqian_files if f['date'] == date])
            date_file_count[date] = count
        
        complete_dates = [d for d, c in date_file_count.items() if c == 22]
        incomplete_dates = [d for d, c in date_file_count.items() if c != 22]
        
        print(f"\n完整的日期(22个文件): {len(complete_dates)} 个")
        print(f"不完整的日期: {len(incomplete_dates)} 个")
        
        if incomplete_dates:
            print("\n❌ 文件数量不足的日期:")
            for date in sorted(incomplete_dates)[:10]:  # 最多显示10个
                count = date_file_count[date]
                print(f"  {date}: {count}/22 个文件")
            if len(incomplete_dates) > 10:
                print(f"  ... 还有 {len(incomplete_dates) - 10} 个日期")
        else:
            print("\n✅ 所有日期的日前联络线计划文件数量完整！")
        
        # 3.2 检查每个日期缺失的通道
        print("\n--- 3.2 通道完整性检查 ---")
        
        missing_channels_by_date = {}
        for date in riqian_dates:
            is_complete, found_channels, missing_channels = check_riqian_file_count_by_date(riqian_files, date)
            if missing_channels:
                missing_channels_by_date[date] = missing_channels
        
        if missing_channels_by_date:
            print(f"\n❌ 存在通道缺失的日期 ({len(missing_channels_by_date)} 个):")
            for date in sorted(missing_channels_by_date.keys())[:5]:  # 最多显示5个日期
                missing = missing_channels_by_date[date]
                print(f"\n  {date} 缺失 ({len(missing)} 个):")
                for ch in missing[:5]:  # 每个日期最多显示5个缺失通道
                    print(f"    - {ch}")
                if len(missing) > 5:
                    print(f"    ... 还有 {len(missing) - 5} 个")
        else:
            print("\n✅ 所有日期的通道文件完整！")
        
        # 3.3 检查表格内容与文件名一致性
        print("\n--- 3.3 表格内容一致性检查 ---")
        
        riqian_inconsistent = []
        riqian_consistent = 0
        
        total_riqian = len(riqian_files)
        for i, excel_file in enumerate(riqian_files, 1):
            is_valid, result_info = check_riqian_table_content(excel_file)
            if is_valid:
                riqian_consistent += 1
            else:
                riqian_inconsistent.append({
                    'filename': excel_file['filename'],
                    'filepath': excel_file['filepath'],
                    'reason': result_info
                })
            
            if i % 50 == 0 or i == total_riqian:
                print(f"进度: {i}/{total_riqian} 已处理...")
        
        print(f"\n表格内容一致性检查完成: {riqian_consistent}/{total_riqian} 文件一致")
        
        if riqian_inconsistent:
            print(f"\n❌ 发现表格内容与文件名不一致的日前联络线计划文件 ({len(riqian_inconsistent)} 个):")
            print("-" * 60)
            
            for item in riqian_inconsistent[:20]:  # 最多显示20个
                filename = item['filename']
                reason_info = item['reason']
                
                print(f"\n文件: {filename}")
                if isinstance(reason_info, dict):
                    if 'error' in reason_info:
                        print(f"  错误: {reason_info['error']}")
                    else:
                        table_date = reason_info.get('table_date', '未找到')
                        table_channel = reason_info.get('table_channel', '未找到')
                        file_date = reason_info.get('file_date', '')
                        file_channel = reason_info.get('file_channel', '')
                        date_match = reason_info.get('date_match', False)
                        channel_match = reason_info.get('channel_match', False)
                        
                        print(f"  文件日期: {file_date}, 表格日期: {table_date}, 匹配: {date_match}")
                        print(f"  文件通道: {file_channel}, 表格通道: {table_channel}, 匹配: {channel_match}")
            
            if len(riqian_inconsistent) > 20:
                print(f"\n... 还有 {len(riqian_inconsistent) - 20} 个文件")
        else:
            print("\n✅ 所有日前联络线计划文件的表格内容与文件名一致！")
    
    print("\n" + "=" * 60)
    print("分析完成")
    print("=" * 60)

if __name__ == "__main__":
    main()