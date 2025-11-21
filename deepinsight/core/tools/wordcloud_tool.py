import os
import uuid
import json
from typing import Dict

import matplotlib
from matplotlib.font_manager import fontManager
from wordcloud import WordCloud
from langchain_core.tools import tool
from deepinsight.config.config import load_config, Config
WORK_ROOT: str | None = None
CHART_IMAGE_DIR_REL: str | None = None
CHART_IMAGE_DIR_ABS: str | None = None
IMAGE_BASE_URL: str | None = None
IMAGE_PATH_MODE: str | None = None


def _init_paths_from_config(config_path: str | None):
    global WORK_ROOT, CHART_IMAGE_DIR_REL, CHART_IMAGE_DIR_ABS, IMAGE_BASE_URL, IMAGE_PATH_MODE
    config: Config | None = None
    resolved_path = config_path
    if resolved_path and os.path.exists(resolved_path):
        try:
            config = load_config(resolved_path)
        except Exception:
            config = None
    else:
        fallback = os.path.join(os.getcwd(), "config.yaml")
        if os.path.exists(fallback):
            try:
                config = load_config(fallback)
            except Exception:
                config = None
    if config and getattr(config, "workspace", None):
        WORK_ROOT = config.workspace.work_root or "./data"
        CHART_IMAGE_DIR_REL = config.workspace.chart_image_dir or "charts"
        IMAGE_BASE_URL = (
            config.workspace.image_base_url
            or f"http://127.0.0.1:{getattr(config.app, 'port', 8888)}{getattr(config.app, 'api_prefix', '/api/v1')}/deepinsight/charts/image"
        )
        IMAGE_PATH_MODE = config.workspace.image_path_mode or "relative"
    else:
        WORK_ROOT = "./data"
        CHART_IMAGE_DIR_REL = "charts"
        IMAGE_BASE_URL = None
        IMAGE_PATH_MODE = "relative"
    CHART_IMAGE_DIR_ABS = os.path.abspath(os.path.join(WORK_ROOT, CHART_IMAGE_DIR_REL))
    os.makedirs(CHART_IMAGE_DIR_ABS, exist_ok=True)


def _rel_tool_path(filename: str) -> str:
    if WORK_ROOT is None or CHART_IMAGE_DIR_REL is None:
        _init_paths_from_config(None)
    image_dir_name = CHART_IMAGE_DIR_REL.lstrip("./") if CHART_IMAGE_DIR_REL else "charts"
    if (IMAGE_PATH_MODE or "relative").lower() == "base_url" and (IMAGE_BASE_URL or ""):
        file_id = os.path.splitext(filename)[0]
        return f"{IMAGE_BASE_URL}/{file_id}"
    return f"../../{image_dir_name}/{filename}"


def get_font_path():
    # 优先中文字体
    chinese_keywords = ['SimHei', 'SimSun', 'Kai', 'Hei', 'Song', 'Fang', 'PingFang', 'Source Han', 'STHeiti']
    english_keywords = ['Arial', 'DejaVu Sans', 'Liberation Sans', 'Noto Sans']

    for font in fontManager.ttflist:
        name = font.name.lower()
        if any(k.lower() in name for k in chinese_keywords + english_keywords):
            return font.fname
    return None


def tech_color_func(word=None, font_size=None, position=None, orientation=None, random_state=None, **kwargs):
    import random
    colors = [
        (0, 255, 255),  # Cyan
        (102, 255, 204),  # Aqua Green
        (0, 128, 255),  # Deep Blue
        (102, 178, 255),  # Sky Blue
        (255, 165, 0),  # Orange
        (255, 200, 102),  # Light Orange
        (135, 206, 250),  # Light Sky Blue
        (173, 216, 230),  # Light Blue
        (255, 99, 132),  # Light Red
        (255, 150, 170)  # Soft Pink
    ]
    return random.choice(colors)


@tool("generate_wordcloud", return_direct=True)
def generate_wordcloud(word_freq: Dict[str, int]) -> str:
    """
    根据词频字典生成词云图片并返回文件路径。
    参数:
        word_freq: 词频字典，例如 {"AI": 30, "Machine Learning": 20}
    返回:
        输出图片文件相对路径JSON字符串，例如 {"png_path": "../../charts/<uuid>.png"}
    """
    matplotlib.use('Agg')  # 适用于无GUI环境
    file_id = str(uuid.uuid4())
    png_name = f"{file_id}.png"
    if CHART_IMAGE_DIR_ABS is None:
        _init_paths_from_config(os.environ.get("DEEPINSIGHT_CONFIG_PATH"))
    png_path = os.path.abspath(os.path.join(CHART_IMAGE_DIR_ABS or "./charts", png_name))
    wc = WordCloud(
        background_color="white",
        width=800,
        height=600,
        max_words=300,
        color_func=tech_color_func,
        min_font_size=12,
        max_font_size=160,
    )
    font_path = get_font_path()
    if font_path:
        wc.font_path = font_path
    wc.generate_from_frequencies(word_freq)
    wc.to_file(png_path)
    return json.dumps({"png_path": _rel_tool_path(os.path.basename(png_path))})


if __name__ == '__main__':
    word_freq = {'memory management': 7, 'operating systems': 7, 'large language models': 7, 'performance optimization': 4, 'operating system': 4, 'llm serving': 4, 'formal verification': 4, 'ebpf': 4, 'scheduling': 3, 'cloud computing': 3, 'datacenter': 3, 'embedded systems': 3, 'distributed systems': 3, 'kv cache': 3, 'distributed training': 3, 'gpu': 2, 'performance': 2, 'deep learning': 2, 'data placement': 2, 'heterogeneous computing': 2, 'process isolation': 2, 'address spaces': 2, 'scalability': 2, 'virtual memory': 2, 'multicore architectures': 2, 'resource efficiency': 2, 'cold starts': 2, 'fault tolerance': 2, 'optimization': 2, 'latency': 2, 'file system': 2, 'scheduling framework': 2, 'virtualization': 2, 'verification': 2, 'correctness guarantees': 2, 'linux kernel': 2, 'webassembly': 2, 'testing framework': 2, 'static analysis': 2, 'gpu memory': 2, 'memory allocation': 2, 'resource allocation': 2, 'storage performance': 2, 'throughput': 2, 'hardware acceleration': 1, 'performance simulation': 1, 'end-to-end simulation': 1, 'hardware-software stacks': 1, 'cycle-accurate simulation': 1, 'system performance': 1}
    generate_wordcloud(word_freq)
