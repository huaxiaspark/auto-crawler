# 数据校验工具

对电力数据 Excel/CSV 文件进行批量校验，包括文件完整性、日期一致性和通道一致性检查。所有校验规则通过 `config.yaml` 配置，无需修改代码即可扩展新的校验对象。

## 文件结构

```
data-verify/
├── config.yaml            # 校验配置（校验对象、日期范围、路径等）
├── analyze_excel.py       # 主校验脚本
├── read_sample.py         # Excel 结构分析工具（调试用）
├── requirements.txt       # Python 依赖
├── loss.txt               # 输出：缺失文件列表
└── validation_errors.txt  # 输出：错误详细报告
```

## 快速开始

**安装依赖**

```bash
pip install -r requirements.txt
```

**修改配置**

编辑 `config.yaml`，至少设置数据目录和日期范围：

```yaml
global:
  data_directory: "/path/to/your/data"
  date_range:
    start: "2023-01-01"
    end: "2026-01-31"
```

**运行校验**

```bash
python analyze_excel.py
```

校验完成后，结果输出到：
- `loss.txt` — 缺失文件列表（日期 + 通道）
- `validation_errors.txt` — 错误详细报告

## 配置说明

### 全局配置 `global`

| 字段 | 说明 | 示例 |
|------|------|------|
| `data_directory` | 数据文件目录（递归扫描） | `"/data/exports"` |
| `date_range.start` | 校验起始日期 | `"2023-01-01"` |
| `date_range.end` | 校验结束日期 | `"2026-01-31"` |
| `output_directory` | 报告输出目录，`null` 表示脚本所在目录 | `null` |
| `report_files.missing` | 缺失文件报告文件名 | `"loss.txt"` |
| `report_files.errors` | 错误报告文件名 | `"validation_errors.txt"` |

**性能参数 `global.performance`**

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `max_rows_to_scan` | 读取 Excel 时最大行数 | `50` |
| `scan_rows_for_content` | 扫描内容时最大行数 | `50` |
| `max_header_attempts` | 尝试的 header 行数（0 到 N-1） | `4` |
| `max_cols_to_scan` | 扫描列数上限 | `20` |

### 校验对象配置 `validators`

每个校验对象对应一类文件，支持独立开关和校验规则。

```yaml
validators:
  - name: "校验对象名称"
    enabled: true          # false 则跳过此对象

    file_pattern:
      prefix: "文件名前缀_"
      extensions: [".xlsx"]
      name_format: "prefix_date"   # 见下方说明

    checks:
      completeness:
        enabled: false
        expected_count: 22
      date_consistency:
        enabled: true
        date_header_keywords: ["日期", "时间"]
        date_formats: ["%Y-%m-%d", "%Y/%m/%d"]
      channel_consistency:
        enabled: false
        channel_header_keywords: ["通道"]

    standard_channels: []  # 仅 name_format=prefix_date_channel 时需要

    report_filters:
      skip_error_types:
        date: ["no_date_data"]
        channel: []
      skip_empty_channel_mismatch: true
```

**`name_format` 取值**

| 值 | 文件名格式 | 示例 |
|----|-----------|------|
| `prefix_date` | `{前缀}_{日期}.xlsx` | `实时输电断面约束及阻塞_2025-01-01.xlsx` |
| `prefix_date_channel` | `{前缀}_{日期}_{通道}.xlsx` | `日前联络线计划_2025-01-01_山西送河北(省间现货).xlsx` |

**三类校验项**

| 校验项 | 说明 | 适用场景 |
|--------|------|----------|
| `completeness` | 每个日期下文件数量是否达到 `expected_count` | 有固定通道数量要求的数据 |
| `date_consistency` | 文件名中的日期与表格内容中的日期是否一致 | 所有文件类型 |
| `channel_consistency` | 文件名中的通道名与表格内容中的通道名是否一致 | `prefix_date_channel` 格式文件 |

**报告过滤 `report_filters`**

| 字段 | 说明 |
|------|------|
| `skip_error_types.date` | 不输出到报告的日期错误类型列表 |
| `skip_error_types.channel` | 不输出到报告的通道错误类型列表 |
| `skip_empty_channel_mismatch` | 通道不一致但表格内通道为空时跳过 |

错误类型枚举：`read_error` / `date_mismatch` / `no_date_data` / `no_date_header` / `channel_mismatch` / `no_channel_data` / `no_channel_header`

## 新增校验对象

在 `config.yaml` 的 `validators` 列表末尾追加一个配置块即可，无需修改代码。`config.yaml` 末尾提供了完整模板：

```yaml
- name: "新数据类型名称"
  enabled: true

  file_pattern:
    prefix: "文件名前缀_"
    extensions: [".xlsx"]
    name_format: "prefix_date"

  checks:
    completeness:
      enabled: false
    date_consistency:
      enabled: true
      date_header_keywords: ["日期", "时间"]
      date_formats: ["%Y-%m-%d", "%Y/%m/%d"]
    channel_consistency:
      enabled: false

  standard_channels: []
```

## 调试工具

`read_sample.py` 用于分析单个 Excel 文件的结构，帮助确认表头行位置和列名：

```bash
# 分析指定文件
python read_sample.py /path/to/file.xlsx

# 不传参数时，自动使用 config.yaml 中 data_directory 下的第一个 xlsx 文件
python read_sample.py
```

输出逻辑：从 header=0 开始依次尝试，找到第一个可成功读取的 header 行后打印列名和前 5 行数据，然后额外输出无 header 模式下的前 10 行原始内容（前 5 列）。

## 输出报告格式

**`loss.txt`** — 缺失文件列表

```
# 缺失文件列表
# 生成时间: 2025-01-01 10:00:00
# 格式: 名称,日期,通道名称

日前联络线计划,2025-01-05,山西送河北(省间现货)
日前联络线计划,2025-01-05,总加
机组实际发电曲线,2025-01-06,
```

**`validation_errors.txt`** — 错误详细报告，每个校验对象独立一节，每节包含：
1. 日期校验错误（文件名日期与表格内容不符）
2. 通道校验错误（文件名通道与表格内容不符）
3. 本对象小计（仅日期错误数 / 仅通道错误数 / 两者均错误数 / 总计）

最后附综合统计（所有校验对象的日期错误合计 / 通道错误合计）。
