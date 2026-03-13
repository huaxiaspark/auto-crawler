#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Excel文件分析与数据校验系统

功能：
1. 从 config.yaml 加载校验配置
2. 检查日期覆盖情况
3. 校验文件名与表格内日期一致性
4. 支持通道完整性校验（如日前联络线计划）
5. 生成缺失文件报告和错误详细报告

作者：汉燧智能
"""

import os
import re
import warnings
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

warnings.filterwarnings('ignore', category=UserWarning, module='openpyxl')


# ============================================================
# 配置加载
# ============================================================

def load_config(config_path: str = None) -> dict:
    """加载 YAML 配置文件"""
    if config_path is None:
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


# ============================================================
# 日期工具
# ============================================================

def generate_date_range(start: str, end: str) -> list:
    """生成从 start 到 end 的所有日期字符串列表（含首尾）"""
    start_dt = datetime.strptime(start, '%Y-%m-%d')
    end_dt = datetime.strptime(end, '%Y-%m-%d')
    dates = []
    cur = start_dt
    while cur <= end_dt:
        dates.append(cur.strftime('%Y-%m-%d'))
        cur += timedelta(days=1)
    return dates


def extract_date_from_filename(filename: str) -> str | None:
    """从文件名末尾提取 YYYY-MM-DD 格式日期，返回字符串或 None"""
    # 去掉扩展名后取最后一个 _ 分隔段
    stem = re.sub(r'\.(xlsx|csv)$', '', filename, flags=re.IGNORECASE)
    parts = stem.split('_')
    for candidate in reversed(parts):
        try:
            datetime.strptime(candidate, '%Y-%m-%d')
            return candidate
        except ValueError:
            continue
    return None


def parse_date_channel_filename(filename: str, prefix: str) -> dict | None:
    """解析 {prefix}{date}_{channel}.xlsx 格式文件名
    返回 {'date': ..., 'channel': ...} 或 None
    """
    stem = re.sub(r'\.(xlsx|csv)$', '', filename, flags=re.IGNORECASE)
    if not stem.startswith(prefix.rstrip('_')):
        return None
    # 去掉前缀
    rest = stem[len(prefix.rstrip('_')):].lstrip('_')
    parts = rest.split('_')
    if len(parts) < 2:
        return None
    try:
        datetime.strptime(parts[0], '%Y-%m-%d')
    except ValueError:
        return None
    return {'date': parts[0], 'channel': '_'.join(parts[1:])}


# ============================================================
# 文件发现
# ============================================================

def discover_files(directory: str, validator_cfg: dict) -> list:
    """根据校验对象配置扫描目录，返回文件信息列表"""
    pattern = validator_cfg['file_pattern']
    prefix = pattern['prefix']
    extensions = [e.lower() for e in pattern['extensions']]
    name_format = pattern.get('name_format', 'prefix_date')

    results = []
    for root, _, files in os.walk(directory):
        for fname in files:
            # 支持 Excel 临时文件（.~ 前缀）
            effective_name = fname[2:] if fname.startswith('.~') else fname
            ext = os.path.splitext(effective_name)[1].lower()
            if ext not in extensions:
                continue
            if not effective_name.startswith(prefix.rstrip('_')):
                continue

            filepath = os.path.join(root, fname)

            if name_format == 'prefix_date_channel':
                parsed = parse_date_channel_filename(effective_name, prefix)
                if parsed:
                    results.append({
                        'filename': fname,
                        'filepath': filepath,
                        'date': parsed['date'],
                        'channel': parsed['channel'],
                        'file_type': 'csv' if ext == '.csv' else 'excel',
                    })
            else:  # prefix_date
                date_str = extract_date_from_filename(effective_name)
                if date_str:
                    results.append({
                        'filename': fname,
                        'filepath': filepath,
                        'date': date_str,
                        'channel': None,
                        'file_type': 'csv' if ext == '.csv' else 'excel',
                    })
    return results


# ============================================================
# 底层读取工具
# ============================================================

def _normalize_text(s: str) -> str:
    """规范化文本：去空格、全角括号转半角"""
    if not isinstance(s, str):
        return ''
    s = s.strip().replace('\u3000', ' ').replace(' ', '')
    return s.replace('（', '(').replace('）', ')')


def _try_parse_date(val, date_formats: list) -> datetime | None:
    """尝试将值解析为 datetime，失败返回 None"""
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        for fmt in date_formats:
            try:
                return datetime.strptime(val.strip(), fmt)
            except ValueError:
                pass
        # 从字符串中提取日期片段
        m = re.search(r'(\d{4}-\d{2}-\d{2})', val)
        if m:
            try:
                return datetime.strptime(m.group(1), '%Y-%m-%d')
            except ValueError:
                pass
    return None


def _read_excel_frames(filepath: str, max_rows: int, max_header_attempts: int) -> list:
    """尝试多种 header 行读取 Excel，返回 DataFrame 列表（含 header=None）"""
    xls = pd.ExcelFile(filepath)
    sheet = xls.sheet_names[0]
    frames = []
    for h in range(max_header_attempts):
        try:
            df = pd.read_excel(filepath, sheet_name=sheet, header=h, nrows=max_rows)
            frames.append(df)
        except Exception:
            pass
    try:
        df_raw = pd.read_excel(filepath, sheet_name=sheet, header=None, nrows=max_rows)
        frames.append(df_raw)
    except Exception:
        pass
    return frames


def _read_csv_frames(filepath: str, max_rows: int) -> list:
    """尝试多种编码读取 CSV，返回 DataFrame 列表"""
    for enc in ['utf-8-sig', 'utf-8', 'gbk', 'gb2312', 'gb18030']:
        try:
            df = pd.read_csv(filepath, encoding=enc, header=None, nrows=max_rows)
            return [df]
        except UnicodeDecodeError:
            continue
        except Exception:
            break
    return []


def check_file_readable(filepath: str) -> tuple[bool, str | None]:
    """检查文件是否可读，返回 (ok, error_msg)"""
    try:
        if filepath.lower().endswith('.csv'):
            _read_csv_frames(filepath, max_rows=5)
        else:
            xls = pd.ExcelFile(filepath)
            if not xls.sheet_names:
                return False, "Excel文件没有工作表"
        return True, None
    except Exception as e:
        return False, f"无法打开文件: {e}"


# ============================================================
# 校验逻辑
# ============================================================

def check_date_consistency(file_info: dict, cfg: dict) -> tuple[bool, dict | None]:
    """校验文件名日期与表格内日期是否一致"""
    perf = cfg['global']['performance']
    max_rows = perf['max_rows_to_scan']
    max_header = perf['max_header_attempts']

    file_date_str = file_info['date']
    try:
        file_date = datetime.strptime(file_date_str, '%Y-%m-%d').date()
    except ValueError:
        return False, {'type': 'invalid_filename_date', 'error': f'文件名日期格式错误: {file_date_str}'}

    ok, err = check_file_readable(file_info['filepath'])
    if not ok:
        return False, {'type': 'read_error', 'error': err}

    # 找到当前 validator 配置
    v_cfg = _find_validator_cfg(cfg, file_info)
    date_formats = ['%Y-%m-%d', '%Y/%m/%d']
    date_keywords = ['日期', '时间']
    if v_cfg:
        dc = v_cfg.get('checks', {}).get('date_consistency', {})
        date_formats = dc.get('date_formats', date_formats)
        date_keywords = dc.get('date_header_keywords', date_keywords)

    if file_info['file_type'] == 'csv':
        frames = _read_csv_frames(file_info['filepath'], max_rows)
    else:
        frames = _read_excel_frames(file_info['filepath'], max_rows, max_header)

    has_date_header = False
    found_dates = []

    for df in frames:
        # 检查是否有日期表头
        for col in df.columns:
            col_str = str(col).strip()
            if any(kw in col_str for kw in date_keywords):
                has_date_header = True
                break

        scan_rows = min(perf['scan_rows_for_content'], len(df))
        for row_idx in range(scan_rows):
            for val in df.iloc[row_idx]:
                dt = _try_parse_date(val, date_formats)
                if dt is None:
                    continue
                if dt.date() == file_date:
                    return True, None
                found_dates.append(dt.strftime('%Y-%m-%d'))

    if found_dates:
        unique = list(dict.fromkeys(found_dates))[:5]
        return False, {
            'type': 'date_mismatch',
            'sample_dates': unique,
            'reason': f'表格中未找到日期 "{file_date_str}"，发现的日期: {unique}',
        }
    if has_date_header:
        return False, {
            'type': 'no_date_data',
            'reason': '表格有日期表头但未找到任何日期数据',
        }
    return False, {
        'type': 'no_date_header',
        'reason': '表格中未找到日期列（表头不含"日期""时间"等字段）',
    }


def check_channel_consistency(file_info: dict, standard_channels: list, cfg: dict) -> tuple[bool, dict | None]:
    """校验文件名通道与表格内通道是否一致"""
    perf = cfg['global']['performance']
    max_rows = perf['scan_rows_for_content']
    max_header = perf['max_header_attempts']

    filename_channel = file_info.get('channel', '')
    ok, err = check_file_readable(file_info['filepath'])
    if not ok:
        return False, {'type': 'read_error', 'error': err}

    v_cfg = _find_validator_cfg(cfg, file_info)
    channel_keywords = ['通道']
    if v_cfg:
        cc = v_cfg.get('checks', {}).get('channel_consistency', {})
        channel_keywords = cc.get('channel_header_keywords', channel_keywords)

    frames = _read_excel_frames(file_info['filepath'], max_rows, max_header)

    has_channel_header = False
    found_channels = []

    for df in frames:
        for col in df.columns:
            if any(kw in str(col).strip() for kw in channel_keywords):
                has_channel_header = True
                break

        scan_rows = min(max_rows, len(df))
        for row_idx in range(scan_rows):
            for val in df.iloc[row_idx]:
                if not isinstance(val, str):
                    continue
                norm_val = _normalize_text(val)
                for ch in standard_channels:
                    if ch not in found_channels and (ch in val or _normalize_text(ch) in norm_val):
                        found_channels.append(ch)
                        break
        if found_channels:
            break

    if found_channels:
        if filename_channel in found_channels:
            return True, None
        return False, {
            'type': 'channel_mismatch',
            'filename_channel': filename_channel,
            'table_channels': found_channels[:5],
            'reason': f'文件名通道 "{filename_channel}" 未在表格中出现，表格通道: {found_channels[:5]}',
        }

    if has_channel_header:
        # 读取通道列原始内容
        raw_channel = _read_raw_channel_value(file_info['filepath'], channel_keywords, max_rows, max_header)
        return False, {
            'type': 'channel_mismatch',
            'filename_channel': filename_channel,
            'table_channels': [],
            'table_channel_raw': raw_channel,
            'reason': f'文件名通道 "{filename_channel}"，表格通道列内容非标准通道名',
        }

    return False, {
        'type': 'no_channel_header',
        'reason': '表格中未找到通道列（表头不含"通道"等字段）',
    }


def _read_raw_channel_value(filepath: str, channel_keywords: list, max_rows: int, max_header: int) -> str | None:
    """读取通道列中第一个非空原始值"""
    for h in range(max_header):
        try:
            xls = pd.ExcelFile(filepath)
            df = pd.read_excel(filepath, sheet_name=xls.sheet_names[0], header=h, nrows=max_rows)
            for col in df.columns:
                if not any(kw in str(col).strip() for kw in channel_keywords):
                    continue
                for val in df[col].dropna():
                    s = str(val).strip()
                    if s:
                        return s
        except Exception:
            pass
    return None


def check_completeness(directory: str, target_date: str, validator_cfg: dict) -> tuple[bool, dict]:
    """检查指定日期的文件完整性（通道数量）"""
    prefix = validator_cfg['file_pattern']['prefix']
    standard_channels = validator_cfg.get('standard_channels', [])
    expected = validator_cfg['checks']['completeness'].get('expected_count', len(standard_channels))

    date_files = []
    for root, _, files in os.walk(directory):
        for fname in files:
            effective = fname[2:] if fname.startswith('.~') else fname
            if not effective.startswith(prefix.rstrip('_')):
                continue
            parsed = parse_date_channel_filename(effective, prefix)
            if parsed and parsed['date'] == target_date:
                date_files.append({'filename': fname, 'filepath': os.path.join(root, fname), **parsed})

    found_channels = [f['channel'] for f in date_files]
    missing = [ch for ch in standard_channels if ch not in found_channels]
    counts = Counter(found_channels)
    duplicates = [{'channel': ch, 'count': cnt} for ch, cnt in counts.items() if cnt > 1]
    is_complete = len(date_files) == expected and not missing and not duplicates

    return is_complete, {
        'date': target_date,
        'total_files': len(date_files),
        'expected_count': expected,
        'missing_channels': missing,
        'duplicate_channels': duplicates,
    }


def _find_validator_cfg(cfg: dict, file_info: dict) -> dict | None:
    """根据文件信息找到对应的 validator 配置"""
    for v in cfg.get('validators', []):
        prefix = v['file_pattern']['prefix'].rstrip('_')
        if file_info['filename'].startswith(prefix) or \
           (file_info['filename'].startswith('.~') and file_info['filename'][2:].startswith(prefix)):
            return v
    return None


# ============================================================
# 报告生成
# ============================================================

def _output_path(cfg: dict, filename: str) -> str:
    out_dir = cfg['global'].get('output_directory')
    if not out_dir:
        out_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(out_dir, filename)


def write_missing_report(cfg: dict, missing_files: list) -> str:
    path = _output_path(cfg, cfg['global']['report_files']['missing'])
    with open(path, 'w', encoding='utf-8') as f:
        f.write("# 缺失文件列表\n")
        f.write(f"# 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("# 格式: 日期,通道名称\n\n")
        for item in sorted(missing_files, key=lambda x: (x['date'], x['channel'])):
            f.write(f"{item['date']},{item['channel']}\n")
    print(f"✅ 缺失文件列表已保存至: {path}")
    return path


def _should_skip_error(item: dict, filters: dict) -> bool:
    """根据过滤规则判断是否跳过该错误"""
    date_reason = item.get('date_reason') or {}
    channel_reason = item.get('channel_reason') or {}

    skip_date_types = filters.get('skip_error_types', {}).get('date', [])
    skip_channel_types = filters.get('skip_error_types', {}).get('channel', [])
    skip_empty_ch = filters.get('skip_empty_channel_mismatch', True)

    if not item.get('date_ok') and date_reason.get('type') in skip_date_types:
        return True

    if not item.get('channel_ok') and channel_reason.get('type') in skip_channel_types:
        return True

    if not item.get('channel_ok') and channel_reason.get('type') == 'channel_mismatch' and skip_empty_ch:
        raw = channel_reason.get('table_channel_raw')
        channels = channel_reason.get('table_channels', [])
        display = str(raw).strip() if raw else (channels[0] if channels else '')
        if not display:
            return True

    return False


def write_errors_report(cfg: dict, validator_name: str, combined_errors: list, filters: dict) -> str:
    path = _output_path(cfg, cfg['global']['report_files']['errors'])
    filtered = [e for e in combined_errors if not _should_skip_error(e, filters)]
    filtered.sort(key=lambda x: (x.get('date', ''), x.get('channel', '')))

    date_errors = [e for e in filtered if not e.get('date_ok')]
    channel_errors = [e for e in filtered if not e.get('channel_ok')]

    with open(path, 'w', encoding='utf-8') as f:
        f.write("=" * 80 + "\n")
        f.write(f"{validator_name} 校验错误详细报告\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 80 + "\n\n")

        # 一、日期校验错误
        f.write("-" * 80 + "\n一、日期校验错误\n" + "-" * 80 + "\n\n")
        if date_errors:
            f.write(f"共 {len(date_errors)} 个:\n\n")
            for e in date_errors:
                _write_error_entry(f, e, 'date')
        else:
            f.write("✅ 无日期校验错误\n")

        # 二、通道校验错误
        f.write("\n" + "-" * 80 + "\n二、通道校验错误\n" + "-" * 80 + "\n\n")
        if channel_errors:
            f.write(f"共 {len(channel_errors)} 个:\n\n")
            for e in channel_errors:
                _write_error_entry(f, e, 'channel')
        else:
            f.write("✅ 无通道校验错误\n")

        # 三、综合统计
        f.write("\n" + "=" * 80 + "\n三、综合统计\n" + "=" * 80 + "\n\n")
        date_only = sum(1 for e in filtered if not e.get('date_ok') and e.get('channel_ok'))
        ch_only = sum(1 for e in filtered if e.get('date_ok') and not e.get('channel_ok'))
        both = sum(1 for e in filtered if not e.get('date_ok') and not e.get('channel_ok'))
        f.write(f"日期错误: {date_only} 个\n")
        f.write(f"通道错误: {ch_only} 个\n")
        f.write(f"日期+通道均错误: {both} 个\n")
        f.write(f"总错误文件数: {len(filtered)} 个\n")
        f.write(f"\n报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    print(f"✅ 校验错误报告已保存至: {path}")
    return path


def _write_error_entry(f, entry: dict, kind: str):
    f.write(f"文件: {entry['filename']}\n")
    f.write(f"  日期: {entry.get('date', '-')}\n")
    if entry.get('channel'):
        f.write(f"  通道: {entry['channel']}\n")

    reason = entry.get(f'{kind}_reason') or {}
    etype = reason.get('type', 'unknown')

    if etype == 'read_error':
        f.write(f"  错误类型: 文件无法解析\n")
        f.write(f"  详细原因: {reason.get('error', '')}\n")
    elif etype == 'date_mismatch':
        dates = reason.get('sample_dates', [])
        f.write(f"  错误类型: 日期不一致\n")
        f.write(f"  文件名日期: {entry.get('date', '')}\n")
        f.write(f"  表格内日期: {dates[0] if dates else '无'}\n")
        if len(dates) > 1:
            f.write(f"  其他日期: {dates[1:]}\n")
    elif etype == 'no_date_data':
        f.write(f"  错误类型: 有日期表头但无数据\n")
    elif etype == 'no_date_header':
        f.write(f"  错误类型: 无日期表头\n")
    elif etype == 'channel_mismatch':
        raw = reason.get('table_channel_raw')
        channels = reason.get('table_channels', [])
        display = str(raw).strip() if raw else (channels[0] if channels else '无')
        f.write(f"  错误类型: 通道不一致\n")
        f.write(f"  文件名通道: {reason.get('filename_channel', '')}\n")
        f.write(f"  表格内通道: {display}\n")
        if len(channels) > 1:
            f.write(f"  其他通道: {channels[1:]}\n")
    elif etype == 'no_channel_header':
        f.write(f"  错误类型: 无通道表头\n")
    else:
        f.write(f"  错误类型: {etype}\n")
        if reason.get('reason'):
            f.write(f"  详细原因: {reason['reason']}\n")
    f.write("\n")


# ============================================================
# 主校验流程
# ============================================================

def run_validator(validator_cfg: dict, cfg: dict, target_dates: list):
    """执行单个校验对象的完整校验流程"""
    name = validator_cfg['name']
    directory = cfg['global']['data_directory']
    checks = validator_cfg.get('checks', {})
    standard_channels = validator_cfg.get('standard_channels', [])
    filters = validator_cfg.get('report_filters', {})

    print(f"\n{'=' * 80}")
    print(f"校验对象: {name}")
    print('=' * 80)

    # 发现文件
    files = discover_files(directory, validator_cfg)
    print(f"找到文件: {len(files)} 个")

    # 1. 完整性校验
    all_missing = []
    if checks.get('completeness', {}).get('enabled'):
        print(f"\n--- 文件完整性校验 ---")
        for date in target_dates:
            ok, info = check_completeness(directory, date, validator_cfg)
            status = "✅" if ok else "❌"
            print(f"{status} {date}: {info['total_files']}/{info['expected_count']}")
            if not ok and info['missing_channels']:
                sample = info['missing_channels'][:3]
                more = f"... 共{len(info['missing_channels'])}个" if len(info['missing_channels']) > 3 else ""
                print(f"   缺失: {sample}{more}")
                for ch in info['missing_channels']:
                    all_missing.append({'date': date, 'channel': ch})

        if all_missing:
            write_missing_report(cfg, all_missing)
        print(f"完整性校验完成，缺失文件: {len(all_missing)} 个")

    # 2. 内容一致性校验
    combined_errors = []
    if checks.get('date_consistency', {}).get('enabled') or checks.get('channel_consistency', {}).get('enabled'):
        print(f"\n--- 内容一致性校验 ---")
        total = len(files)
        for i, f in enumerate(files, 1):
            date_ok, date_reason = True, None
            channel_ok, channel_reason = True, None

            if checks.get('date_consistency', {}).get('enabled'):
                date_ok, date_reason = check_date_consistency(f, cfg)

            if checks.get('channel_consistency', {}).get('enabled') and standard_channels:
                channel_ok, channel_reason = check_channel_consistency(f, standard_channels, cfg)

            if not date_ok or not channel_ok:
                combined_errors.append({
                    'filename': f['filename'],
                    'date': f['date'],
                    'channel': f.get('channel'),
                    'date_ok': date_ok,
                    'channel_ok': channel_ok,
                    'date_reason': date_reason,
                    'channel_reason': channel_reason,
                })

            if i % 100 == 0 or i == total:
                print(f"  进度: {i}/{total}")

        date_err_count = sum(1 for e in combined_errors if not e['date_ok'])
        ch_err_count = sum(1 for e in combined_errors if not e['channel_ok'])
        print(f"日期一致性: {total - date_err_count}/{total} 通过")
        print(f"通道一致性: {total - ch_err_count}/{total} 通过")

        if combined_errors:
            write_errors_report(cfg, name, combined_errors, filters)

    return {
        'name': name,
        'total_files': len(files),
        'missing_count': len(all_missing),
        'error_count': len(combined_errors),
    }


def main(config_path: str = None):
    """主入口"""
    cfg = load_config(config_path)
    g = cfg['global']

    print("=" * 80)
    print("Excel文件数据校验系统")
    print("=" * 80)
    print(f"数据目录: {g['data_directory']}")

    dr = g['date_range']
    target_dates = generate_date_range(dr['start'], dr['end'])
    print(f"校验日期范围: {dr['start']} 至 {dr['end']}（共 {len(target_dates)} 天）")

    summaries = []
    for v_cfg in cfg.get('validators', []):
        if not v_cfg.get('enabled', True):
            print(f"\n[跳过] {v_cfg['name']}（已禁用）")
            continue
        summary = run_validator(v_cfg, cfg, target_dates)
        summaries.append(summary)

    print("\n" + "=" * 80)
    print("校验汇总")
    print("=" * 80)
    for s in summaries:
        print(f"[{s['name']}] 文件: {s['total_files']}  缺失: {s['missing_count']}  错误: {s['error_count']}")
    print("=" * 80)
    print("校验完成！")


if __name__ == "__main__":
    main()
