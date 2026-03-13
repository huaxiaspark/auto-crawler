# post-process 数据批量处理工具

将 `data/` 目录下各子目录中的 Excel/CSV 文件转换为规范的宽表 CSV，输出到 `output/`。

所有处理逻辑通过 `config.yaml` 驱动，无需修改 Python 代码即可调整参数或新增数据类型。

## 目录结构

```
post-process/
├── batch_process_data.py   # 主处理脚本
├── config.yaml             # 配置文件（所有可调参数）
├── requirements.txt        # Python 依赖
├── data/                   # 输入数据（各子目录对应一类数据）
│   ├── 断面约束/
│   ├── 实时备用总量/
│   └── ...
└── output/                 # 输出结果（自动创建）
    ├── 断面约束/
    ├── 实时备用总量/
    └── ...
```

## 安装依赖

```bash
pip install -r requirements.txt
```

## 运行

```bash
# 处理全部任务
python batch_process_data.py

# 指定配置文件
python batch_process_data.py --config /path/to/config.yaml

# 只处理指定任务（任务名称与 config.yaml 中的 name 字段一致）
python batch_process_data.py --task 二次调频出清结果 机组实际发电曲线
```

## 配置文件说明

`config.yaml` 分为四个部分：

### 1. global — 全局设置

```yaml
global:
  time_format: "%Y-%m-%d %H:%M:%S.000"  # 输出时间戳格式
  data_root: "data"                       # 输入数据根目录
  output_root: "output"                   # 输出根目录
  max_file_size_mb: 5                     # 单文件超过此大小时自动拆分
```

### 2. value_maps — 文本值映射表

将文本列转换为数值，未命中的值使用 `其他` 对应的数值。

```yaml
value_maps:
  limit_exceed:       # 是否越限
    否: 0
    是: 1
    其他: 2
  unit_status:        # 开停状态
    停机: 0
    开机: 1
    其他: 2
  test_result:        # 当日测试结果
    不合格: 0
    合格: 1
    不确定: 2
    其他: 3
```

### 3. secondary_freq_period_offsets — 二次调频时段偏移

时段编号到 `[小时, 分钟]` 的映射，5 时段/天：

```yaml
secondary_freq_period_offsets:
  1: [0, 0]
  2: [4, 48]
  3: [9, 36]
  4: [14, 24]
  5: [19, 12]
```

### 4. tasks — 任务列表

每个任务对应 `data/` 下的一个子目录，`type` 字段决定使用哪种处理逻辑。

## 处理类型（type）说明

| type | 适用场景 | 关键参数 |
|------|---------|---------|
| `section_constraints` | 断面约束（多输出列） | `sheet_name`, `skip_rows`, `columns`, `outputs` |
| `reserve_capacity` | 备用总量（单行取值） | `skip_rows`, `date_col_idx`, `value_cols` |
| `clearing_quantity` | 出清现货电量 | `skip_rows`, `columns` |
| `channel_numeric` | 通道/断面数值宽表 | `skip_rows`, `columns`, `name_col`, `value_col` |
| `channel_text` | 通道/断面文本宽表（映射为数值） | `skip_rows`, `columns`, `name_col`, `value_col`, `value_map` |
| `node_lmp` | 节点边际电价（多价格列） | `output_prefix`, `lmp_columns`, `dir_prefix_match` |
| `reservoir_level` | 抽蓄电站水位 | `skip_rows`, `columns`, `name_col`, `value_col` |
| `reserve_demand` | 正负备用需求 | `skip_rows`, `columns`, `name_col`, `value_col` |
| `clearing_overview_excel` | 市场出清概况（Excel） | `skip_rows`, `columns` |
| `clearing_overview_csv` | 市场出清概况（CSV） | `file_encoding`, `header_row` |
| `unit_commitment` | 机组开机安排（文本映射） | `columns`, `name_col`, `status_col_keywords`, `value_map` |
| `node_factor` | 节点分配因子 | `columns`, `name_col`, `value_col` |
| `maintenance_plan` | 设备检修计划（保留行结构） | `skip_rows`, `columns` |
| `secondary_freq_clearing` | 二次调频出清结果 | `sheet_name`, `date_col`, `period_col`, `output_cols` |
| `coal_unit_capacity` | 煤电机组最大出力认定 | `sheet_name`, `name_col`, `ffill_cols`, `outputs`, `value_map` |
| `unit_generation_curve` | 机组实际发电曲线（宽转长再转宽） | `sheet_name`, `name_col`, `date_col`, `exclude_cols` |

## 输出规范

- 编码：UTF-8
- 时间列名：`timestamp`，格式 `YYYY-MM-DD HH:MM:SS.000`
- 宽表结构：行为时间戳，列为机组/节点/通道名称
- 单文件超过 `max_file_size_mb` 时自动拆分为 `xxx_part1.csv`、`xxx_part2.csv`

## 特殊映射输出

以下任务会额外输出映射关系 CSV，便于反查数值含义：

| 任务 | 映射文件 | 说明 |
|------|---------|------|
| 实时输电断面约束及阻塞 | `是否越限映射.csv` | 否→0，是→1 |
| 日前机组开机安排 | `开停状态映射.csv` | 停机→0，开机→1 |
| 省调煤电机组最大出力认定考核公示 | `当日测试结果映射.csv` | 不合格→0，合格→1，不确定→2 |
| 断面约束 | `断面信息映射.csv` | 断面名称→断面描述 |

## 新增数据类型

1. 在 `data/` 下创建对应子目录并放入 Excel/CSV 文件
2. 在 `config.yaml` 的 `tasks` 列表末尾添加任务配置，选择合适的 `type`
3. 若现有 `type` 均不满足需求，在 `batch_process_data.py` 中实现新处理函数并注册到 `PROCESSOR_REGISTRY`

示例（新增一个通道数值类型任务）：

```yaml
- name: 新数据类型名称
  type: channel_numeric
  skip_rows: 2
  columns: [序号, 通道名称, 日期, 时点, 数值]
  name_col: 通道名称
  value_col: 数值
  output_dir: 新数据类型名称
  output_file: 新数据类型名称
```
