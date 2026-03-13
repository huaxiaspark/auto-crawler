"""
特殊数据解析模块
用于处理出清概况等长文本数据的结构化解析，以及日前联络线计划批量文件解析。
"""

import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from utils.logger import get_logger

logger = get_logger()


def parse_tieline_batch_file(filepath: str) -> List[Tuple[str, str, str]]:
    """
    解析「日前联络线计划」批量查询文件，每行一条「日期+筛选条件」或「日期范围+筛选条件」。

    文件格式（UTF-8）：
    - 两列：日期,联络线名称 → 单日单条联络线
    - 三列：开始日期,结束日期,联络线名称 → 日期范围内该联络线
    - 空行、以 # 开头的行会被忽略

    示例：
        2026-01-01,山西-河北
        2026-01-05,2026-01-10,山西-山东

    Args:
        filepath: 批量文件路径

    Returns:
        [(start_date, end_date, 联络线名称), ...]
    """
    result: List[Tuple[str, str, str]] = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 2:
                date_str, option = parts[0], parts[1]
                if not option:
                    logger.warning("第 %d 行：联络线名称为空，已跳过", line_no)
                    continue
                try:
                    datetime.strptime(date_str, "%Y-%m-%d")
                    result.append((date_str, date_str, option))
                except ValueError:
                    logger.warning("第 %d 行：日期格式错误 '%s'，应为 YYYY-MM-DD，已跳过", line_no, date_str)
            elif len(parts) == 3:
                start_str, end_str, option = parts[0], parts[1], parts[2]
                if not option:
                    logger.warning("第 %d 行：联络线名称为空，已跳过", line_no)
                    continue
                try:
                    s = datetime.strptime(start_str, "%Y-%m-%d")
                    e = datetime.strptime(end_str, "%Y-%m-%d")
                    if s > e:
                        logger.warning("第 %d 行：开始日期晚于结束日期，已跳过", line_no)
                        continue
                    result.append((start_str, end_str, option))
                except ValueError as ve:
                    logger.warning("第 %d 行：日期格式错误，应为 YYYY-MM-DD，已跳过: %s", line_no, ve)
            else:
                logger.warning("第 %d 行：应为 2 列(日期,联络线名称) 或 3 列(开始,结束,联络线名称)，已跳过", line_no)
    return result


def parse_clearing_summary(text: str, date: str) -> Dict[str, Optional[str]]:
    """
    解析出清概况长文本，提取各项指标

    示例文本：
    "02月05日,直调用电预测最大负荷3806.48万千瓦，最小2771.97万千瓦；
     外送电最大644.72万千瓦，最小381.80万千瓦；
     日前现货市场出清节点电价最大323.00元/MWh，最小0.00元/MWh..."

    Args:
        text: 出清概况原始文本
        date: 日期字符串

    Returns:
        解析后的指标字典
    """
    result = {"日期": date, "原始文本": text}

    # 定义提取规则列表：(指标名, 正则表达式, 单位)
    patterns = [
        # 负荷
        ("直调用电实际最大负荷(万千瓦)", r"直调用电(?:实际|预测)?最大负荷([\d.]+)万千瓦", "万千瓦"),
        ("直调用电实际最小负荷(万千瓦)", r"最小([\d.]+)万千瓦", "万千瓦"),
        ("直调用电预测最大负荷(万千瓦)", r"直调用电预测最大负荷([\d.]+)万千瓦", "万千瓦"),
        # 外送电
        ("外送电最大(万千瓦)", r"外送电最大([\d.]+)万千瓦", "万千瓦"),
        ("外送电最小(万千瓦)", r"外送电(?:最大[\d.]+万千瓦，)?最小([\d.]+)万千瓦", "万千瓦"),
        # 节点电价
        ("出清节点电价最大(元/MWh)", r"出清节?点?电价最大([\d.]+)元/MWh", "元/MWh"),
        ("出清节点电价最小(元/MWh)", r"出清节?点?电价.*?最小([\d.]+)元/MWh", "元/MWh"),
        # 现货市场
        ("现货市场场均电量价格(元/MWh)", r"(?:资本|现货)平均?(?:为?)([\d.]+)元/MWh", "元/MWh"),
        # 机组
        ("火电机组运行(台)", r"火电机组运行([\d]+)台", "台"),
        ("运行机组总装机(MW)", r"运行机组总装机(?:容量)?([\d.]+)(?:MW)?", "MW"),
        # 调频
        ("调频市场需求最大值(MW)", r"调频?(?:市场)?需求最大值?([\d.]+)(?:MW)?", "MW"),
        ("需求最小值(MW)", r"需求?最小值?(?:为)?([\d.]+)(?:MW)?", "MW"),
        # 中标机组
        ("中标机组最多(台)", r"中标机组最多([\d]+)台", "台"),
        ("中标机组最少(台)", r"中标机组最少([\d]+)台", "台"),
        # 调频指标
        ("中标机组调频期间综合指标平均值", r"综合指标平均值(?:为)?([\d.]+)", ""),
        # 边际出清
        ("边际出清价格最大(元/MWh)", r"边际出清价格最大([\d.]+)元/MWh", "元/MWh"),
        ("边际出清价格最小(元/MWh)", r"边际出清价格.*?最小([\d.]+)元/MWh", "元/MWh"),
        # 火电机组
        ("火电机组已开(台次)", r"火电机组已?开([\d]+)台次", "台次"),
        ("必开容量(MW)", r"必开容量([\d.]+)(?:MW)?", "MW"),
        ("必停(台次)", r"必停([\d]+)台次", "台次"),
        ("必停容量(MW)", r"必停容量([\d.]+)(?:MW)?", "MW"),
    ]

    for name, pattern, unit in patterns:
        match = re.search(pattern, text)
        if match:
            result[name] = match.group(1)
        else:
            result[name] = None

    return result


def parse_clearing_summary_batch(rows: List[Dict], date_col: str = "日期",
                                  text_col: str = "出清概况") -> List[Dict]:
    """
    批量解析出清概况数据

    Args:
        rows: 原始数据行列表
        date_col: 日期列名
        text_col: 出清概况文本列名

    Returns:
        解析后的数据列表
    """
    parsed = []
    for row in rows:
        date = row.get(date_col, "")
        text = row.get(text_col, "")
        if text:
            try:
                parsed_row = parse_clearing_summary(text, date)
                parsed.append(parsed_row)
            except Exception as e:
                logger.warning("解析出清概况失败 [%s]: %s", date, e)
                parsed.append(row)
        else:
            parsed.append(row)
    return parsed
