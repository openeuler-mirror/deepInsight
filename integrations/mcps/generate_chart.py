import json
import logging
import os
import uuid
import time
import base64

import plotly.express as px
import plotly.graph_objects as go
from mcp.server.fastmcp import FastMCP

DEFAULT_CHART_TEMP_DIR = "charts"
DEFAULT_CHART_APP_URL = f"http://{os.getenv('CHART_SERVER_HOST', '127.0.0.1:9380')}/api/v1/deepinsight/charts"
DEFAULT_CHART_PNG_URL = f"http://{os.getenv('CHART_SERVER_HOST', '127.0.0.1:9380')}/api/v1/deepinsight/charts/image"
CHART_DIR = DEFAULT_CHART_TEMP_DIR
os.makedirs(CHART_DIR, exist_ok=True)

# 初始化FastMCP（移除网络相关配置，仅保留必要参数）
mcp = FastMCP(name="mcp-chart")


def gen_chart_url(file_id: str):
    """生成图表访问URL（本地文件标识，非网络地址）"""
    app_server_url = DEFAULT_CHART_APP_URL
    return f"{app_server_url}/{file_id}"


def gen_chart_png_url(file_id: str):
    """生成图表访问URL（本地文件标识，非网络地址）"""
    app_server_url = DEFAULT_CHART_PNG_URL
    return f"{app_server_url}/{file_id}"


def save_chart(fig, width=1000, height=600) -> str:
    """保存图表到本地文件并返回标识URL"""
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
    file_name = f"{file_id}.html"
    file_path = os.path.abspath(os.path.join(CHART_DIR, file_name))

    png_name = f"{file_id}.png"
    png_path = os.path.abspath(os.path.join(CHART_DIR, png_name))

    fig.write_html(file_path)
    png_b64: str | None = None
    try:
        start_time = time.time()
        fig.write_image(png_path)
        end_time = time.time()
        logging.info(f"PNG image generated successfully: {png_path}. Time taken: {end_time - start_time:.2f} seconds.")
        # 生成 data URI，便于在无 Web 的 CLI 环境中直接内嵌到 Markdown
        with open(png_path, "rb") as f:
            png_b64 = base64.b64encode(f.read()).decode("ascii")
    except Exception as e:
        logging.error(f"Failed to generate PNG image: {png_path}. Error: {e}")
    chart_url = gen_chart_url(file_id)
    png_url = gen_chart_png_url(file_id)
    result = {
        "url_html": chart_url,
        "url_png": png_url,
        # 额外返回本地文件路径，供需要的调用方直接读取或嵌入
        "file_path_html": file_path,
        "file_path_png": png_path,
    }
    if png_b64:
        result["data_uri_png"] = f"data:image/png;base64,{png_b64}"
    return json.dumps(result)


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
        width: int = 600,
        axisXTitle: str = "",
        title: str = "",
        group: bool = False,
        height: int = 400
) -> str:
    """
    Generate a bar chart to show data for numerical comparisons among different categories,
    such as, comparing categorical data and for horizontal comparisons.

    Parameters:
    data (array): Data for bar chart, such as, [{ category: '分类一', value: 10 }].
    axisYTitle (string): Set the y-axis title of chart.
    stack (boolean): Whether stacking is enabled. When enabled, bar charts require a 'group' field in the data.
                     When `stack` is true, `group` should be false.
    width (number): Set the width of chart, default is 600.
    axisXTitle (string): Set the x-axis title of chart.
    title (string): Set the title of chart.
    group (boolean): Whether grouping is enabled. When enabled, bar charts require a 'group' field in the data.
                     When `group` is true, `stack` should be false.
    height (number): Set the height of chart, default is 400.
    """
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
    print("Starting chart generator in STDIO mode (no network required)...")
    mcp.run(transport="stdio")
