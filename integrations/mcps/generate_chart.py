import json
import logging
import os
import uuid
import time
import base64
import sys
from deepinsight.config.config import load_config, Config

import plotly.express as px
import plotly.graph_objects as go
from mcp.server.fastmcp import FastMCP

# 运行期路径配置（来自 config.yaml 的 workspace）
WORK_ROOT: str | None = None
CHART_IMAGE_DIR_REL: str | None = None
CHART_IMAGE_DIR_ABS: str | None = None


def _resolve_config_path_from_args_env() -> str | None:
    """优先使用命令行指定的 config.yaml；否则读取环境变量 DEEPINSIGHT_CONFIG；再回退到当前工作目录下的 config.yaml。"""
    if len(sys.argv) == 2:
        return sys.argv[1]
    env_path = os.environ.get("DEEPINSIGHT_CONFIG")
    if env_path:
        return env_path
    # fallback to ./config.yaml
    fallback = os.path.join(os.getcwd(), "config.yaml")
    return fallback if os.path.exists(fallback) else None


def _init_paths_from_config(config_path: str | None):
    """根据 config.yaml 初始化工作路径与图片保存目录。
    - workspace.work_root: 基础工作目录（相对工程根，默认 ./data）
    - workspace.chart_image_dir: 图片保存目录（相对 work_root，默认 charts）
    """
    global WORK_ROOT, CHART_IMAGE_DIR_REL, CHART_IMAGE_DIR_ABS
    config: Config | None = None
    if config_path:
        try:
            config = load_config(config_path)
        except Exception as e:
            logging.warning(f"Failed to load config via deepinsight loader at {config_path}: {e}. Using defaults.")

    if config and getattr(config, "workspace", None):
        WORK_ROOT = config.workspace.work_root or "./data"
        CHART_IMAGE_DIR_REL = config.workspace.chart_image_dir or "charts"
    else:
        WORK_ROOT = "./data"
        CHART_IMAGE_DIR_REL = "charts"
    CHART_IMAGE_DIR_ABS = os.path.abspath(os.path.join(WORK_ROOT, CHART_IMAGE_DIR_REL))
    os.makedirs(CHART_IMAGE_DIR_ABS, exist_ok=True)

# 初始化FastMCP（移除网络相关配置，仅保留必要参数）
mcp = FastMCP(name="mcp-chart")


def _rel_tool_path(filename: str) -> str:
    """将文件名转换为工具返回的相对路径格式 '../../<image_folders>/<filename>'"""
    if WORK_ROOT is None or CHART_IMAGE_DIR_REL is None:
        # 若未初始化，按默认进行一次初始化
        _init_paths_from_config(os.environ.get("DEEPINSIGHT_CONFIG_PATH"))
    # 规范化 work_root 与相对图片目录，去掉开头的 './'
    image_dir_name = CHART_IMAGE_DIR_REL.lstrip("./") if CHART_IMAGE_DIR_REL else "charts"
    rel = f"{image_dir_name}/{filename}"
    return f"../../{rel}"


def save_chart(fig, width=1000, height=600) -> str:
    """保存图表到本地文件并返回字典结构。

    返回值示例：{"png_path": "../../../charts/<uuid>.png"}
    统一所有图表工具的输出结构，便于上层使用键访问。
    """
    fig.update_layout(
        width=width,
        height=height,
        font=dict(
            family="Noto Sans CJK SC, Noto Sans CJK TC, WenQuanYi Micro Hei, DejaVu Sans, Arial",  # 中文和英文兼容的字体
            size=16,  # 设置字体大小
            color="black"  # 字体颜色
        )
    )

    file_id = str(uuid.uuid4())
    # 确保路径已初始化
    if CHART_IMAGE_DIR_ABS is None:
        _init_paths_from_config(os.environ.get("DEEPINSIGHT_CONFIG_PATH"))
    png_name = f"{file_id}.png"
    png_path = os.path.abspath(os.path.join(CHART_IMAGE_DIR_ABS, png_name))

    try:
        start_time = time.time()
        fig.write_image(png_path)
        end_time = time.time()
        logging.info(f"PNG image generated successfully: {png_path}. Time taken: {end_time - start_time:.2f} seconds.")
    except Exception as e:
        logging.error(f"Failed to generate PNG image: {png_path}. Error: {e}")
    # 返回字典结构，包含相对路径
    return json.dumps(dict(
        png_path=_rel_tool_path(os.path.basename(png_path))
    ))


@mcp.tool()
def generate_area_chart(
        data: list,
        axisYTitle: str,
        stack: bool = False,
        width: int = 600,
        axisXTitle: str = "",
        title: str = "",
        height: int = 400
) -> str:
    """
    Generate an area chart to show data trends under continuous independent variables and observe the overall data trend,
    such as, displacement = velocity (average or instantaneous) × time: s = v × t. If the x-axis is time (t) and the y-axis
    is velocity (v) at each moment, an area chart allows you to observe the trend of velocity over time and infer the
    distance traveled by the area's size.

    Parameters:
    data (array): Data for area chart, such as, [{ time: '2018', value: 99.9 }].
    axisYTitle (string): Set the y-axis title of chart.
    stack (boolean): Whether stacking is enabled. When enabled, area charts require a 'group' field in the data.
    width (number): Set the width of chart, default is 600.
    axisXTitle (string): Set the x-axis title of chart.
    title (string): Set the title of chart.
    height (number): Set the height of chart, default is 400.
    """
    if stack and not any('group' in item for item in data):
        raise ValueError("When stack is true, data must contain 'group' field")

    x_key = next(iter(data[0].keys())) if data else 'x'
    if x_key in ['value', 'group']:
        x_key = 'x'  # fallback if keys are unusual

    fig = px.area(
        data,
        x=x_key,
        y='value',
        color='group' if stack else None,
        title=title,
        labels={x_key: axisXTitle, 'value': axisYTitle}
    )

    return save_chart(fig, width, height)


@mcp.tool()
def generate_bar_chart(
        data: list,
        axisYTitle: str,
        stack: bool = False,
        width: int = 1000,
        axisXTitle: str = "",
        title: str = "",
        group: bool = False,
        height: int = 600,
        horizontal: bool = True,
        dtick: int = 1  # ✅ Optional tick interval, default 1
) -> str:
    """
    Generate a bar chart to show data for numerical comparisons among different categories,
    such as comparing categorical data and for horizontal comparisons.

    Parameters:
    data (list): Data for bar chart, such as, [{ category: '分类一', value: 10 }].
    axisYTitle (str): Set the y-axis title of chart.
    stack (bool): Whether stacking is enabled. When enabled, bar charts require a 'group' field in the data.
                  When `stack` is true, `group` should be false.
    width (int): Set the width of chart, default is 1000.
    axisXTitle (str): Set the x-axis title of chart.
    title (str): Set the title of chart.
    group (bool): Whether grouping is enabled. When enabled, bar charts require a 'group' field in the data.
                  When `group` is true, `stack` should be false.
    height (int): Set the height of chart, default is 600.
    horizontal (bool): Whether to display as horizontal bar chart, default True.
    dtick (int, optional): Interval of ticks on the X-axis, default is 1.
                           Recommended settings based on number of categories:
                           5 (for more than 20 categories),
                           10 (for more than 40 categories),
                           20 (for more than 80 categories).
                           Users can configure this value according to actual field conditions.

    Returns:
    str: Path or identifier of the saved chart (via save_chart).
    """
    if stack and group:
        raise ValueError("stack and group cannot both be true")

    if (stack or group) and not any('group' in item for item in data):
        raise ValueError("When stack or group is true, data must contain 'group' field")

    # Determine category field
    category_key = 'category' if 'category' in data[0] else next(iter(data[0].keys()))

    # Candidate color palette (20 distinguishable colors)
    candidate_colors = [
        "#4c72b0", "#55a868", "#c44e52", "#8172b3", "#ccb974",
        "#64b5cd", "#f28e2b", "#8c564b", "#e15759", "#76b7b2",
        "#9c755f", "#bab0ac", "#7f7f7f", "#b07aa1", "#ff9da7",
        "#9edae5", "#bcbd22", "#dbdb8d", "#17becf", "#aec7e8"
    ]

    if not (stack or group):
        colors = [candidate_colors[i % len(candidate_colors)] for i in range(len(data))]
    else:
        colors = None

    fig = px.bar(
        data,
        x=category_key if not horizontal else 'value',
        y='value' if not horizontal else category_key,
        color='group' if stack or group else category_key,
        color_discrete_sequence=colors if not (stack or group) else None,
        barmode='stack' if stack else 'group' if group else 'group',
        title=title,
        labels={category_key: axisXTitle if not horizontal else axisYTitle,
                'value': axisYTitle if not horizontal else axisXTitle},
        orientation='v' if not horizontal else 'h'
    )

    # Add value labels
    fig.update_traces(text='value', textposition='inside')
    fig.update_layout(yaxis=dict(categoryorder='total ascending'))

    # Layout configuration
    fig.update_layout(
        yaxis=dict(showticklabels=False),
        template="plotly_white",
        width=width,
        height=height
    )

    # ✅ Configurable X-axis tick interval
    fig.update_xaxes(dtick=dtick)

    return save_chart(fig, width, height)


@mcp.tool()
def generate_column_chart(
        data: list,
        axisYTitle: str,
        stack: bool = False,
        width: int = 600,
        axisXTitle: str = "",
        title: str = "",
        group: bool = False,
        height: int = 400
) -> str:
    """
    Generate a column chart, which are best for comparing categorical data,
    such as, when values are close, column charts are preferable because our eyes
    are better at judging height than other visual elements like area or angles.

    Parameters:
    data (array): Data for column chart, such as, [{ category: '北京' value: 825; group: '油车' }].
    axisYTitle (string): Set the y-axis title of chart.
    stack (boolean): Whether stacking is enabled. When enabled, column charts require a 'group' field in the data.
                     When `stack` is true, `group` should be false.
    width (number): Set the width of chart, default is 600.
    axisXTitle (string): Set the x-axis title of chart.
    title (string): Set the title of chart.
    group (boolean): Whether grouping is enabled. When enabled, column charts require a 'group' field in the data.
                     When `group` is true, `stack` should be false.
    height (number): Set the height of chart, default is 400.
    """
    # 柱形图与条形图类似，只是方向不同
    if stack and group:
        raise ValueError("stack and group cannot both be true")

    if (stack or group) and not any('group' in item for item in data):
        raise ValueError("When stack or group is true, data must contain 'group' field")

    category_key = 'category' if 'category' in data[0] else next(iter(data[0].keys()))

    fig = px.bar(
        data,
        x=category_key,
        y='value',
        color='group' if stack or group else None,
        barmode='stack' if stack else 'group' if group else 'group',
        title=title,
        labels={category_key: axisXTitle, 'value': axisYTitle}
    )

    return save_chart(fig, width, height)


@mcp.tool()
def generate_pie_chart(
        data: list,
        width: int = 600,
        innerRadius: float = 0,
        title: str = "",
        height: int = 400
) -> str:
    """
    Generate a pie chart to show the proportion of parts, such as, market share and budget allocation.

    Parameters:
    data (array): Data for pie chart, such as, [{ category: '分类一', value: 27 }].
    width (number): Set the width of chart, default is 600.
    innerRadius (number): Set the innerRadius of pie chart, the value between 0 and 1.
                          Set the pie chart as a donut chart. Set the value to 0.6 or number in [0 ,1] to enable it.
    title (string): Set the title of chart.
    height (number): Set the height of chart, default is 400.
    """
    category_key = 'category' if 'category' in data[0] else next(iter(data[0].keys()))

    fig = px.pie(
        data,
        values='value',
        names=category_key,
        title=title,
        hole=innerRadius  # 用于甜甜圈图
    )

    return save_chart(fig, width, height)


@mcp.tool()
def generate_line_chart(
        data: list,
        axisYTitle: str,
        width: int = 600,
        axisXTitle: str = "",
        title: str = "",
        height: int = 400,
        group: bool = False
) -> str:
    """
    Generate a line chart to show trends over time or other continuous variables.

    Parameters:
    data (array): Data for line chart, such as, [{ x: '2018', y: 99.9, group: 'A' }].
    axisYTitle (string): Set the y-axis title of chart.
    width (number): Set the width of chart, default is 600.
    axisXTitle (string): Set the x-axis title of chart.
    title (string): Set the title of chart.
    height (number): Set the height of chart, default is 400.
    group (boolean): Whether to group data by a 'group' field.
    """
    if group and not any('group' in item for item in data):
        raise ValueError("When group is true, data must contain 'group' field")

    x_key = 'x' if 'x' in data[0] else next(iter(data[0].keys()))
    y_key = 'y' if 'y' in data[0] else 'value'

    fig = px.line(
        data,
        x=x_key,
        y=y_key,
        color='group' if group else None,
        title=title,
        labels={x_key: axisXTitle, y_key: axisYTitle}
    )

    return save_chart(fig, width, height)


@mcp.tool()
def generate_scatter_chart(
        data: list,
        axisYTitle: str,
        width: int = 600,
        axisXTitle: str = "",
        title: str = "",
        height: int = 400
) -> str:
    """
    Generate a scatter chart to show the relationship between two variables, helps discover their relationship
    or trends, such as, the strength of correlation, data distribution patterns.

    Parameters:
    data (array): Data for scatter chart, such as, [{ x: 10, y: 15 }].
    axisYTitle (string): Set the y-axis title of chart.
    width (number): Set the width of chart, default is 600.
    axisXTitle (string): Set the x-axis title of chart.
    title (string): Set the title of chart.
    height (number): Set the height of chart, default is 400.
    """
    fig = px.scatter(
        data,
        x='x',
        y='y',
        title=title,
        labels={'x': axisXTitle, 'y': axisYTitle}
    )

    return save_chart(fig, width, height)


@mcp.tool()
def generate_radar_chart(
        data: list,
        width: int = 600,
        title: str = "",
        height: int = 400
) -> str:
    """
    Generate a radar chart to display multidimensional data (four dimensions or more),
    such as, evaluate Huawei and Apple phones in terms of five dimensions: ease of use,
    functionality, camera, benchmark scores, and battery life.

    Parameters:
    data (array): Data for radar chart, such as, [{ name: 'Design', value: 70 }].
    width (number): Set the width of chart, default is 600.
    title (string): Set the title of chart.
    height (number): Set the height of chart, default is 400.
    """
    # 检查是否有多组数据
    has_groups = any('group' in item for item in data)

    if has_groups:
        groups = set(item['group'] for item in data)
        fig = go.Figure()

        for group_name in groups:
            group_data = [item for item in data if item['group'] == group_name]
            fig.add_trace(go.Scatterpolar(
                r=[item['value'] for item in group_data],
                theta=[item['name'] for item in group_data],
                fill='toself',
                name=group_name
            ))
    else:
        fig = go.Figure(data=go.Scatterpolar(
            r=[item['value'] for item in data],
            theta=[item['name'] for item in data],
            fill='toself'
        ))

    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True)),
        title=title
    )

    return save_chart(fig, width, height)


# 强制以stdio模式启动，无网络通信
if __name__ == "__main__":
    # 支持通过命令行参数或环境变量传入 config.yaml 路径
    # 命令行：python generate_chart.py /path/to/config.yaml
    # 环境变量：export DEEPINSIGHT_CONFIG_PATH=/path/to/config.yaml
    cfg_path = _resolve_config_path_from_args_env()
    _init_paths_from_config(cfg_path)
    print("Starting chart generator in STDIO mode (no network required)...")
    mcp.run(transport="stdio")
