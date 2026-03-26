# 山西电力交易平台爬虫

自动化爬取**山西电力交易中心电力交易平台（SXPX）**电力现货市场信息披露数据的 Python 爬虫脚本。

---

## 目录

- [功能概述](#功能概述)
- [支持的数据类别](#支持的数据类别)
- [环境要求](#环境要求)
- [安装步骤](#安装步骤)
- [快速开始](#快速开始)
- [命令行参数](#命令行参数)
- [配置文件说明](#配置文件说明)
- [数据存储结构](#数据存储结构)
- [项目结构](#项目结构)
- [模块说明](#模块说明)
- [常见问题](#常见问题)
- [注意事项](#注意事项)

---

## 功能概述

- **连接已有浏览器**：通过 CDP 连接到已登录的 Chrome，无需重复登录
- **自动化浏览器操作**：基于 Playwright + Chrome，完整模拟用户操作
- **多页面数据爬取**：支持 18 种数据类别的自动化爬取
- **双页面类型适配**：同时支持 Element UI 页面和 FineReport 报表页面，自动检测并选择对应操作策略
- **iframe 自动穿透**：自动检测和切换 iframe 上下文（支持 2 层和 3 层 iframe 嵌套）
- **智能导出**：优先使用页面"导出"功能，回退到 HTML 表格解析
- **全量导出模式**：部分任务支持一次导出所有下拉选项数据（`export_all`），无需逐一遍历
- **日期迭代**：支持按日逐日爬取指定日期范围的数据
- **下拉筛选**：自动获取并遍历下拉选项（节点、断面、机组等）
- **分页处理**：自动处理分页和滚动加载（Element UI + FineReport）
- **增量更新**：自动跳过已爬取日期，仅抓取新数据
- **出清概况解析**：使用正则表达式结构化解析长文本出清概况数据
- **数据质量校验**：内置数据完整性和质量检查
- **页面恢复**：自动检测页面被刷新回首页的情况并重新导航恢复
- **定时调度**：支持按小时间隔定时执行
- **缺失数据补充**：支持通过 `--loss-file` 读取数据校验生成的缺失记录，批量补充下载指定任务的缺失日期数据
- **详细日志**：全流程日志记录，支持文件滚动，导航失败自动保存诊断截图

---

## 支持的数据类别

### 现货出清结果
| 数据类别 | 下拉筛选 | 分页 | 导出方式 | 全量导出 |
|---------|---------|------|---------|---------|
| 实时市场出清概况 | 无 | 有 | 无（表格解析） | - |
| 日前市场出清概况 | 无 | 有 | 原样导出 | - |
| 日前备用总量 | 无 | 无 | 原样导出 | - |

### 现货实时数据
| 数据类别 | 下拉筛选 | 分页 | 导出方式 | 全量导出 |
|---------|---------|------|---------|---------|
| 实时各时段出清现货电量 | 无 | 无 | 原样导出 | - |
| 实时备用总量 | 无 | 无 | 原样导出 | - |
| 实时节点边际电价 | 节点名称 | 无 | 导出 | **是** |
| 实时输电断面约束及阻塞 | 断面名称（不选=全量） | 无 | 原样导出 | - |
| 断面约束情况及影子价格 | 断面名称（不选=全量） | 无 | 原样导出 | - |
| 重要通道实际输电情况 | 断面名称（不选=全量） | 无 | 原样导出 | - |
| 机组实际发电曲线 | 机组名称（不选=全量） | 有 | 原样导出 | - |

### 现货日前信息
| 数据类别 | 下拉筛选 | 分页 | 导出方式 | 全量导出 |
|---------|---------|------|---------|---------|
| 抽蓄电站水位 | 无 | 无 | 原样导出 | - |
| 断面约束 | 无 | 有 | 原样导出 | - |
| 日前联络线计划 | 联络线名称 | 无 | 原样导出 | -（支持 `--batch-file` 按文件批量查询） |
| 输变电设备检修计划 | 无 | 有 | 原样导出 | - |
| 输电通道可用容量 | 通道名称（不选=全量） | 有 | 原样导出 | - |
| 日前机组开机安排 | 机组名称 | 无 | 导出 | **是** |
| 日前节点边际电价 | 节点名称 | 无 | 导出 | **是** |
| 日前正负备用需求 | 无 | 无 | 原样导出 | - |

### 综合查询
| 数据类别 | 下拉筛选 | 分页 | 导出方式 | 全量导出 |
|---------|---------|------|---------|---------|
| 节点分配因子 | 无 | 有 | 原样导出 | - |

> **全量导出说明**：标记为「是」的任务，导出按钮会忽略下拉选择，一次性导出该日期下所有选项（如所有节点/机组）的数据到一个文件，无需逐一遍历下拉选项，显著减少爬取时间。
>
> **不选优化说明**：下拉筛选标注「不选=全量」的任务，当下拉选项含「不选」时，脚本仅选择「不选」即可获取全部数据，无需逐一遍历每个选项，显著减少爬取时间。

---

## 环境要求

- **Python**：3.9 及以上
- **操作系统**：Windows / macOS / Linux
- **网络**：需能访问 `https://pmos.sx.sgcc.com.cn`
- **Chrome 浏览器**：需已安装 Google Chrome（用于 CDP 连接模式）
- **Playwright**：Python 包（`pip install playwright`），用于浏览器自动化

---

## 安装步骤

### 1. 克隆或下载项目

```bash
cd /path/to/your/workspace
# 如果是 git 仓库
git clone <repo-url>
cd crawler
```

### 2. 创建虚拟环境（推荐）

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. 安装 Python 依赖

```bash
pip install -r requirements.txt
```

### 4. 安装 Playwright 浏览器

```bash
playwright install chromium
```

> 首次安装 Playwright 浏览器可能需要几分钟时间，也会自动下载所需系统依赖。
> 如果使用 CDP 连接模式（默认），Playwright 不会启动新浏览器，但仍需安装以提供运行时依赖。

---

## 快速开始

### 前置步骤：启动 Chrome（CDP 连接模式）

脚本默认通过 **Chrome DevTools Protocol (CDP)** 连接到已打开且已登录的 Chrome 浏览器，而非启动新浏览器。使用前需：

**1. 以远程调试模式启动 Chrome：**

```bash
# Linux 服务器
google-chrome --remote-debugging-port=9222

# macOS
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222

# Windows
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```

**2. 在 Chrome 中手动完成操作：**
- 打开 `https://pmos.sx.sgcc.com.cn`
- 完成登录认证
- 确认已进入系统首页（能看到左侧菜单栏）

**3. 保持 Chrome 运行，然后执行爬虫脚本。**

> **重要提示：**
> - Chrome 必须带 `--remote-debugging-port=9222` 参数启动，否则脚本无法连接
> - 如果 Chrome 已在运行但没带该参数，需关闭后重新启动
> - 脚本结束后只断开连接，**不会关闭 Chrome 和已打开的页面**
> - CDP 端口号可在 `config.yaml` 的 `browser.cdp_url` 中修改

### 1. 查看可用任务

```bash
python main.py --list-tasks
```

输出示例：
```
可用爬取任务:
------------------------------------------------------------
  [启用] 现货出清结果 > 实时市场出清概况
  [启用] 现货出清结果 > 日前市场出清概况
  [启用] 现货出清结果 > 日前备用总量
  [启用] 现货实时数据 > 实时各时段出清现货电量
  ...
共 18 个任务
```

### 2. 爬取所有已启用任务（使用默认日期范围）

```bash
python main.py
```

### 3. 爬取指定任务

```bash
# 爬取单个任务
python main.py --task 日前备用总量

# 爬取多个任务（逗号分隔）
python main.py --task "日前备用总量,断面约束,实时备用总量"
```

### 4. 指定日期范围

```bash
python main.py --start 2025-06-01 --end 2025-06-30
```

### 5. 组合使用

```bash
python main.py --task 实时节点边际电价 --start 2025-06-01 --end 2025-06-03
```

### 6. 数据质量校验

```bash
python main.py --validate
```

### 7. 定时调度模式

```bash
python main.py --schedule
```

定时模式下，脚本先根据 `schedule.date_mode` 计算“基准日期”，再按每个任务的 `schedule_date_offset_days` 计算实际爬取日期。当前默认配置为：

- `日前各时段出清现货电量`、`日前节点边际电价` 抓取当天
- `断面约束`、`非市场化机组出力` 抓取明天
- 其他未单独配置任务抓取昨天

---

## 命令行参数

| 参数 | 说明 | 默认值 | 示例 |
|------|------|--------|------|
| `--config` | 配置文件路径 | `config.yaml` | `--config prod.yaml` |
| `--task` | 指定任务名称（逗号分隔） | 全部已启用任务 | `--task 日前备用总量` |
| `--start` | 起始日期 | 配置文件中的值 | `--start 2025-06-01` |
| `--end` | 结束日期 | 当天日期 | `--end 2025-06-30` |
| `--validate` | 仅执行数据质量校验 | - | `--validate` |
| `--schedule` | 以定时调度模式运行 | - | `--schedule` |
| `--list-tasks` | 列出所有可用任务 | - | `--list-tasks` |
| `--loss-file` | 从数据校验生成的缺失文件（loss.txt）批量补充下载缺失数据 | - | `--loss-file ../data-verify/loss.txt` |

---

### 缺失数据补充：`--loss-file`

当数据校验（`--validate` 或外部工具）发现某些日期的数据缺失时，可通过 `--loss-file` 指定缺失记录文件，批量补充下载缺失数据，而无需重新爬取整个日期范围。

**文件格式（UTF-8 编码）：**

- 每行一条记录，两种格式：
  - **两列**：`任务名称,日期` → 无下拉筛选的任务（如机组实际发电曲线）
  - **三列**：`任务名称,日期,通道名称` → 含下拉筛选的任务（如日前联络线计划）
- 空行、以 `#` 开头的行会被忽略
- 日期格式必须为 `YYYY-MM-DD`

**示例文件 `loss.txt`：**

```text
# 无下拉筛选的任务
日前备用总量,2025-06-01
断面约束,2025-06-03
# 含下拉筛选的任务（日前联络线计划需指定联络线名称）
日前联络线计划,2025-06-01,山西-河北
日前联络线计划,2025-06-02,山西-山东
```

**运行方式：**

```bash
# 从 loss.txt 补充下载所有缺失记录
python main.py --loss-file ../data-verify/loss.txt

# 仅补充特定任务的缺失数据
python main.py --task 日前备用总量 --loss-file ../data-verify/loss.txt
```

- `--loss-file` 模式下，日期来自文件，`--start` / `--end` 参数不参与日期范围验证。
- 文件中涉及的任务若不在配置中（或已禁用），会被自动忽略并打印警告。
- 对于日前联络线计划，三列格式中第三列（通道名称）为空的记录会被跳过。

---

## 配置文件说明

配置文件为 `config.yaml`，主要配置项：

### 浏览器设置

```yaml
browser:
  # 连接模式: "connect"(连接已有Chrome) / "launch"(启动新浏览器)
  mode: "connect"
  cdp_url: "http://localhost:9222"  # CDP 连接地址
  target_url_pattern: "pmos.sx.sgcc.com.cn"  # 匹配目标标签页的 URL 关键词
  headless: false        # 仅 launch 模式：是否无头模式
  slow_mo: 300           # 仅 launch 模式：操作间隔（毫秒）
  timeout: 30000         # 全局超时（毫秒）
  download_dir: "./data/exports"  # 导出文件下载目录
  viewport:
    width: 1920
    height: 1080
```

| 配置项 | 说明 | 默认值 |
|-------|------|--------|
| `mode` | `"connect"`: 通过 CDP 连接已有 Chrome；`"launch"`: 启动新 Chromium | `"connect"` |
| `cdp_url` | Chrome 远程调试地址 | `"http://localhost:9222"` |
| `target_url_pattern` | 用于查找目标标签页的 URL 关键词 | `"pmos.sx.sgcc.com.cn"` |
| `headless` | 仅 launch 模式生效，是否无头运行 | `false` |
| `slow_mo` | 仅 launch 模式生效，操作间隔（毫秒） | `300` |
| `timeout` | 全局超时（毫秒） | `30000` |
| `download_dir` | 导出文件的下载目录 | `"./data/exports"` |

### 日期范围

```yaml
date_range:
  start_date: "2025-01-01"  # 起始日期
  end_date: ""               # 留空表示到当天
```

### 请求控制（反爬）

```yaml
request:
  page_interval: 2       # 翻页间隔（秒）
  query_interval: 3      # 查询间隔（秒）
  date_interval: 0.2     # 日期迭代间隔（秒）
  retry_times: 3         # 失败重试次数
  retry_interval: 5      # 重试间隔（秒）
  export_timeout: 60     # 导出下载超时（秒）
```

### 定时调度

```yaml
schedule:
  time: "08:30"                    # 每天触发时间；为空则立即执行并按 interval_hours 轮询
  interval_hours: 24               # time 为空时生效
  date_mode: "today"               # 定时模式基准日期：yesterday / today / tomorrow / range
  use_task_date_offsets: true      # 是否启用任务级日期偏移
  default_task_date_offset_days: -1  # 未单独配置任务时默认偏移天数
```

### 任务开关

每个任务均可单独启用/禁用，并配置爬取参数：

```yaml
tasks:
  日前备用总量:
    enabled: true              # 设为 false 可禁用此任务
    category: "现货出清结果"    # 分类目录
    has_export: true           # 是否有导出按钮
    has_dropdown: false        # 是否有下拉筛选
    has_pagination: false      # 是否有分页
    export_type: "原样导出"    # 导出按钮文本

  实时节点边际电价:
    enabled: true
    category: "现货实时数据"
    has_export: true
    has_dropdown: true
    dropdown_label: "节点名称"  # 下拉框标签
    has_pagination: false
    export_type: "导 出"       # 按钮文字含空格
    export_all: true           # 导出按钮一次导出所有数据

  断面约束:
    enabled: true
    category: "现货日前信息"
    schedule_date_offset_days: 1   # 定时模式抓取基准日 + 1 天
    has_export: true
    has_dropdown: false
    has_pagination: true
    has_page_size: true        # 支持设置每页条数
    export_type: "原样导出"

  节点分配因子:
    enabled: true
    category: "综合查询"
    subcategory: "供需与约束 > 参数信息"  # 综合查询页面内导航路径
    has_export: true
    has_dropdown: false
    has_pagination: true
    export_type: "原样导出"
```

**任务配置字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `enabled` | bool | 是否启用任务 |
| `category` | string | 分类目录名（用于导航和文件存储） |
| `subcategory` | string | 综合查询页面内的子导航路径，格式："顶部标签 > 左侧分类" |
| `has_export` | bool | 页面是否有导出按钮 |
| `has_dropdown` | bool | 页面是否有下拉筛选 |
| `dropdown_label` | string | 下拉框的标签名称（如"节点名称"） |
| `has_pagination` | bool | 页面是否有分页 |
| `has_page_size` | bool | 是否支持设置每页条数 |
| `export_type` | string | 导出按钮上的文字（"原样导出" / "导出" / "导 出"） |
| `export_all` | bool | 导出按钮是否一次性导出所有下拉选项的数据 |
| `dropdown_select_none` | bool | 下拉含「不选」时，仅选「不选」获取全量数据，跳过逐一遍历 |

---

## 数据存储结构

```
data/
├── exports/                          # 导出功能下载的原始文件
├── 现货出清结果/
│   ├── 实时市场出清概况_2025-06-01.csv
│   ├── 日前市场出清概况_2025-06-01.csv
│   └── 日前备用总量_2025-06-01.csv
├── 现货实时数据/
│   ├── 实时各时段出清现货电量_2025-06-01.csv
│   ├── 实时节点边际电价_2025-06-01.csv    # export_all 模式：单文件包含所有节点
│   ├── 实时输电断面约束及阻塞_2025-06-01_洪善主变.csv
│   └── ...
├── 现货日前信息/
│   ├── 断面约束_2025-06-01.csv
│   ├── 日前机组开机安排_2025-06-01.csv    # export_all 模式：单文件包含所有机组
│   ├── 日前节点边际电价_2025-06-01.csv    # export_all 模式：单文件包含所有节点
│   ├── 日前正负备用需求_2025-06-01.csv
│   └── ...
└── 综合查询/
    └── 节点分配因子_2025-06-01.csv
```

### CSV 文件命名规则

```
{数据类别}_{日期}_{筛选条件}.csv
```

示例：
- `日前备用总量_2025-06-01.csv`（无下拉筛选）
- `实时节点边际电价_2025-06-01.csv`（export_all 模式，所有节点数据在一个文件中）
- `实时输电断面约束及阻塞_2025-06-01_洪善主变.csv`（逐一遍历下拉选项模式）
- `断面约束_2025-06-01.csv`

---

## 项目结构

```
crawler/
├── main.py                    # 主入口脚本
├── config.yaml                # 配置文件
├── requirements.txt           # Python 依赖
├── README.md                  # 使用说明文档
├── 山西电力交易平台爬虫脚本提示词.md  # 开发规格文档
├── crawler/                   # 爬虫核心模块
│   ├── __init__.py
│   ├── browser.py             # 浏览器管理（Playwright CDP/Launch）
│   ├── navigator.py           # 页面导航（el-tree 菜单 / 综合查询页面内导航）
│   ├── filter_handler.py      # 筛选条件（日期/下拉框/每页条数，Element UI + FineReport）
│   ├── export_handler.py      # 导出处理（原样导出/导出按钮，Element UI + FineReport）
│   ├── pagination.py          # 分页与滚动加载（Element UI + FineReport）
│   ├── data_extractor.py      # HTML 表格数据提取（标准表格 + FineReport 报表）
│   └── page_crawler.py        # 页面爬取逻辑编排（iframe 管理/重试/恢复）
├── storage/                   # 数据存储
│   ├── __init__.py
│   └── csv_storage.py         # CSV 文件存储管理（增量检查/命名/保存）
├── utils/                     # 工具模块
│   ├── __init__.py
│   ├── logger.py              # 日志管理（控制台+文件双输出/滚动日志）
│   ├── parser.py              # 出清概况文本解析（正则提取结构化指标）
│   └── validator.py           # 数据质量校验（非空/字段/数值/日期连续性）
├── data/                      # 数据输出目录（运行后自动创建）
└── logs/                      # 日志及诊断截图目录（运行后自动创建）
```

---

## 模块说明

### `crawler/browser.py` - 浏览器管理
- 支持两种工作模式：
  - **connect 模式**（默认）：通过 CDP 连接到已打开且已登录的 Chrome，脚本结束只断开连接
  - **launch 模式**：启动全新 Chromium 实例，支持 headless/headed 切换，无 DISPLAY 环境自动切换无头模式
- 自动查找包含目标 URL 的标签页
- 上下文管理器（with 语句）自动管理生命周期

### `crawler/navigator.py` - 页面导航
- 侧边栏使用 **el-tree（树形控件）** 而非 el-menu，通过 `span[title="..."]` 属性精确匹配菜单项
- 点击 `.el-tree-node__content` 触发展开，检查 `aria-expanded` 属性避免误触 toggle
- 展开失败时双重尝试：先点击 content → 再点击 expand-icon
- 导航失败时自动保存诊断截图到 `./logs/`
- 支持内容区 Tab 切换
- 综合查询的独立页面导航：顶部标签页 + 左侧面板 + 目标叶子项，同时在主页面和 iframe 中搜索元素

### `crawler/filter_handler.py` - 筛选条件
- **自动检测页面类型**：Element UI 或 FineReport，使用对应策略
- **日期设置**：适配多种控件类型，多层回退定位策略，支持 quick_mode（跳过面板关闭）
- **下拉框处理**：
  - Element UI：打开面板 → 精确定位可见面板 → 收集/选择选项 → 关闭面板
  - FineReport：优先 JS API (`getItems()`/`setValue()`) → 回退 DOM 操作
- **每页条数**：FineReport 通过 PAGESIZE widgetname 控件 / Element UI 通过分页组件
- 查询按钮适配：Element UI `<button>` + FineReport `div[widgetname^="SEARCH"]`

### `crawler/export_handler.py` - 导出处理
- 查找并点击「原样导出」或「导出」按钮，自动匹配按钮文字空格变体
- 同时支持 Element UI 按钮和 FineReport `button.x-emb-excel` 按钮
- 通过 Playwright 下载事件捕获文件（在主 Page 上监听 download 事件）
- 自动命名保存导出文件

### `crawler/pagination.py` - 分页处理
- **Element UI**：`.el-pagination` 组件（下一页按钮/页码输入框）
- **FineReport**：优先 JS API (`gotoPage()`) → 回退页面导航工具栏按钮
- 获取总页数、翻页、跳转
- 滚动加载（无分页控件时）

### `crawler/data_extractor.py` - 数据提取
- **标准表格**：从 HTML 解析表格（BeautifulSoup + lxml），支持 thead/tbody 结构
- **FineReport 报表**：优先解析 `table.x-table`（使用 tridx 属性区分表头/数据行）
- 提取「最新更新日期」
- 备用 JavaScript 提取方案

### `crawler/page_crawler.py` - 页面爬取编排
- **iframe 上下文管理**：自动检测 iframe（2 层/3 层嵌套），切换所有 handler 的操作上下文
- **iframe 有效性检测**：Frame detached 时自动重新检测并恢复上下文
- **页面自动刷新恢复**：通过任务 iframe ID 可用性检测和侧边栏展开状态检测，识别页面跳转并自动重新导航
- 日期迭代 + 下拉选项遍历 / export_all 全量导出
- 优先导出 → 回退表格解析 → 分页提取
- 出清概况特殊解析
- 数据清洗和保存
- 自动重试机制（默认 3 次）

### `utils/parser.py` - 出清概况解析
- 正则表达式提取长文本中的各项指标
- 支持负荷、电价、机组数、容量、调频等多种指标
- 批量处理

### `utils/validator.py` - 数据校验
- 非空检查
- 必填字段检查
- 数值范围检查
- 日期连续性检查
- CSV 文件质量校验（空行/重复行检测）

---

## 常见问题

### Q: 无法连接到 Chrome？

**错误信息：** `无法连接到 Chrome，请确认...`

检查以下几点：
1. Chrome 是否已启动，且带 `--remote-debugging-port=9222` 参数
2. 如果 Chrome 已在运行但没带该参数，需**完全关闭**后重新启动
3. `config.yaml` 中 `cdp_url` 地址是否正确
4. 确认端口未被防火墙阻挡

```bash
# 验证 CDP 是否可用
curl http://localhost:9222/json/version
```

### Q: 连接成功但找不到目标标签页？

**错误信息：** `未找到包含「pmos.sx.sgcc.com.cn」的标签页`

- 确认 Chrome 中已打开 `https://pmos.sx.sgcc.com.cn` 并完成登录
- 检查 `config.yaml` 中 `target_url_pattern` 是否与实际 URL 匹配

### Q: 侧边栏菜单展开失败？

导航失败时会自动保存诊断截图到 `./logs/debug_*.png`，检查截图可快速定位问题：
- 如果截图显示**登录页面** → Chrome 未登录或连接到了错误的标签页
- 如果截图显示**首页但菜单未展开** → 可能是选择器或等待时间问题

### Q: 提示"iframe 已 detached"？

这是正常现象。平台的 Vue.js 应用在页面切换或异步加载时会替换 iframe 元素，脚本会自动重新检测 iframe 并恢复上下文。如果频繁出现：
- 增大 `config.yaml` 中的 `request.query_interval` 值
- 检查网络连接是否稳定

### Q: 页面被刷新回首页？

平台可能在长时间操作期间自动刷新页面或会话超时。脚本内置了自动恢复机制：
- 通过比对 iframe ID 检测页面跳转
- 自动重新导航到目标页面
- 如果频繁发生，建议缩短单次爬取的日期范围

### Q: 浏览器启动失败（launch 模式）？
确保已执行 `playwright install chromium` 安装浏览器。如果在 Linux 服务器上运行，可能需要安装系统依赖：
```bash
playwright install-deps chromium
```

### Q: 页面加载超时？
- 检查网络是否能访问 `https://pmos.sx.sgcc.com.cn`
- 增大 `config.yaml` 中的 `browser.timeout` 值
- 增大 `request.query_interval` 等间隔参数

### Q: 导出文件下载失败？
- 检查 `browser.download_dir` 目录是否有写权限
- 增大 `request.export_timeout` 值（默认 60 秒）
- 部分页面可能无导出按钮（如实时市场出清概况），爬虫会自动回退到表格解析

### Q: 如何只爬取特定日期的数据？
```bash
python main.py --start 2025-06-15 --end 2025-06-15
```

### Q: 如何断点续爬？
爬虫内置增量更新机制。如果某日数据的 CSV 文件已存在，会自动跳过。直接重新运行即可从中断处继续。

### Q: 如何调试特定页面？
1. 将 `logging.level` 设为 `DEBUG`
2. 使用 `--task` 参数只运行目标任务
3. 检查 `./logs/debug_*.png` 诊断截图

---

## 注意事项

1. **Chrome 启动方式**：必须使用 `--remote-debugging-port=9222` 参数启动 Chrome，否则脚本无法连接。已在运行的 Chrome（无该参数）需关闭后重新启动
2. **登录状态**：脚本不处理登录流程，需在 Chrome 中手动完成登录并进入系统首页后再运行脚本
3. **脚本不关闭浏览器**：connect 模式下脚本结束只断开 CDP 连接，Chrome 和所有标签页保持不变
4. **侧边栏结构**：左侧导航栏使用 Element UI 的 **el-tree（树形控件）**，菜单项通过 `span[title="..."]` 属性定位，如果网站前端改版，选择器可能需要更新
5. **双页面类型**：平台同时使用 Element UI 和 FineReport 渲染页面，脚本会自动检测并适配，如有新增页面类型需扩展相应 handler
6. **iframe 嵌套**：页面内容在 iframe 内（2 层或 3 层嵌套），脚本自动穿透 iframe 切换上下文，如果网站改版 iframe 结构可能需要调整检测逻辑
7. **诊断截图**：导航失败时会自动保存截图到 `./logs/debug_*.png`，便于远程调试
8. **合规使用**：请遵守目标网站的 robots.txt 和使用条款，合理控制爬取频率
9. **反爬策略**：脚本已内置合理的请求间隔，请勿将间隔调得过低
10. **网络环境**：需确保运行环境能正常访问山西电力交易平台
11. **数据准确性**：建议定期使用 `--validate` 进行数据质量检查
12. **存储空间**：长期大量爬取需注意磁盘空间，单日全量数据约 10-50MB

---

## License

本项目仅供学习和研究使用，使用者需自行遵守相关法律法规和网站使用条款。
