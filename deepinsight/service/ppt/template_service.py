from __future__ import annotations

import io
import json
import os
from typing import Dict, Any, Tuple
from pathlib import Path
import json
import re
from copy import deepcopy
import csv

from pptx import Presentation
from pptx.enum.text import PP_ALIGN
from pptx.dml.color import RGBColor
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches, Pt, Cm

SLIDE_INDEX_MAP = {
    "cover_page": 0,
    "content_page": 1,
    "conf_overview_page": 2,
    "research_fields_page": 3,
    "country_analysis_page": 4,
    "institution_analysis_page": 5,
    "first_author_page": 6,
    "coauthor_page": 7,
    "keynote_page": 8,
    "topic_content_page": 9,
    "topic_detail_page": 10,
    "valuable_paper_page": 11,
    "conf_summary_page": 12,
}

TABLE_DEFAULT_STYLE={
    "font_name": "微软雅黑",
    "font_size": 11,
    "bold": False,
    "color": [0, 0, 0],
    "align": PP_ALIGN.LEFT,
    "bg_color": [255, 255, 255],

    # 表头样式
    "header_font_name": "微软雅黑",
    "header_font_size": 13,
    "header_bold": True,
    "header_color": [0, 0, 0],
    "header_bg_color": [233, 233, 233],  # 蓝色背景
    "header_align": PP_ALIGN.LEFT,

    # 尺寸控制
    "row_height": None,
    "first_col_width": None
}

STYLE_CONFIG = {
    "cover_page": {
        "conference_name": {"font_size": 44, "bold": True, "color": [192, 0, 0], "align": PP_ALIGN.LEFT},
        "date": {"font_size": 24, "bold": False, "color": [0, 0, 0], "align": PP_ALIGN.LEFT},
    },
    "content_page": {},
    "conf_overview_page": {
        "conf_info": {"font_size": 12, "bold": False, "color": [0, 0, 0]},
        "organizer_level": {"font_size": 12, "bold": False, "color": [50, 50, 50]},
        "conf_topics": {"font_size": 12, "bold": False, "color": [50, 50, 50]},
        "conf_loc": {"font_size": 12, "bold": False, "color": [50, 50, 50]},
        "conf_date": {"font_size": 12, "bold": False, "color": [50, 50, 50]},
        "conf_sponsor": {"font_size": 12, "bold": False, "color": [50, 50, 50]},
        "conf_chair": {"font_size": 12, "bold": False, "color": [50, 50, 50]},
        "conf_committee": {"font_size": 12, "bold": False, "color": [50, 50, 50]},
        "conf_institution": {"font_size": 12, "bold": False, "color": [50, 50, 50]},
        "submit_papers": {"font_size": 12, "bold": False, "color": [50, 50, 50]},
        "total_trend": {"font_size": 12, "bold": False, "color": [0, 0, 0]},
    },
    "research_fields_page": {
        "research_trend": {"font_size": 12, "bold": False, "color": [0, 0, 0]},
        "research_fields_png": {"width": 5, "height": 4},
    },
    "country_analysis_page": {
        "country_trend": {"font_size": 12, "bold": False, "color": [0, 0, 0]},
        "country_png": {"width": 5, "height": 4},
    },
    "institution_analysis_page": {
        "institution_trend": {"font_size": 12, "bold": False, "color": [0, 0, 0]},
        "institution_png": {"width": 5, "height": 4},
    },
    "first_author_page": {
        "first_author_statistic_csv": TABLE_DEFAULT_STYLE
    },
    "coauthor_page": {
        "coauthor_statistic_csv": TABLE_DEFAULT_STYLE
    },
    "keynote_page": {
        "keynote_title": {"font_size": 28, "bold": True, "color": [153, 0, 0]},
        "speaker": {"font_size": 12, "bold": False, "color": [0, 0, 0]},
        "keynote_abstract": {"font_size": 12, "bold": False, "color": [0, 0, 0]},
        "keynote_summary": {"font_size": 12, "bold": False, "color": [255, 255, 255]},
        "keynote_background": {"font_size": 12, "bold": False, "color": [0, 0, 0]},
        "keynote_objective": {"font_size": 12, "bold": False, "color": [0, 0, 0]},
        "keynote_method": {"font_size": 12, "bold": False, "color": [0, 0, 0]},
        "keynote_inspiration": {"font_size": 12, "bold": False, "color": [0, 0, 0]},
        "keynote_picture": {"width": 3.5, "height": 2.5},
    },
    "topic_content_page": {
        "topic_content_csv": TABLE_DEFAULT_STYLE
    },
    "topic_detail_page": {
        "topic_title": {"font_size": 24, "bold": True, "color": [153, 0, 0]},
        "topic_overview": {"font_size": 12, "bold": False, "color": [0, 0, 0]},
        "topic_reason": {"font_size": 12, "bold": False, "color": [0, 0, 0]},
        "topic_summary":  {"font_size": 12, "bold": False, "color": [255, 255, 255]},
        "topic_method_innovation": {"font_size": 12, "bold": False, "color": [0, 0, 0]},
        "topic_inspiration": {"font_size": 12, "bold": False, "color": [0, 0, 0]},
    },
    "valuable_paper_page": {
        "paper_title": {"font_size": 24, "bold": True, "color": [153, 0, 0]},
        "paper_background": {"font_size": 12, "bold": False, "color": [0, 0, 0]},
        "paper_challenges": {"font_size": 12, "bold": False, "color": [0, 0, 0]},
        "paper_tech_resource": {"font_size": 12, "bold": False, "color": [0, 0, 0]},
        "paper_key_tech": {"font_size": 12, "bold": False, "color": [0, 0, 0]},
        "paper_result": {"font_size": 12, "bold": False, "color": [0, 0, 0]},
        "paper_summary": {"font_size": 12, "bold": False, "color": [255, 255, 255]},
        "key_tech_png": {"width_cm": 7.4, "height_cm": 3},
        "exp_result_png": {"width_cm": 7.4, "height_cm": 3},
    },
    "conf_summary_page": {
        "key_trends": {"font_size": 12, "bold": False, "color": [0, 0, 0]},
        "suggestions": {"font_size": 12, "bold": False, "color": [0, 0, 0]},
    }
}

PRE_STR_MAP = {
    "keynote_page": "",
    "topic_detail_page": "",
    "valuable_paper_page": ""
}

class PPTTemplateService:
    """
    根据 JSON 数据填充 PPT 模板的服务。

    使用规则：
    - 文本占位符：形状或表格单元格中的文本包含 `{{key}}` 时，替换为 JSON 中的对应值。
    - 暂不处理图片与图表的动态替换，后续可扩展。
    """

    def fill_from_json_file(self, template_path: str, json_file_path: str, output_name: str | None = None) -> Presentation:
        """
        从 JSON 文件读取数据并填充 PPT 模板。

        Args:
            template_path: PPT 模板文件路径 (.pptx)
            json_file_path: 内容 JSON 文件路径
            output_name: 输出文件名（可选），默认 `result.pptx`

        Returns:
            Presentation: 填充后的 Presentation 对象
        """
        with open(json_file_path, "r", encoding="utf-8") as f:
            list_json = json.load(f)
        return self.fill_from_json(template_path, list_json, output_name=output_name)

    def fill_from_json(self, template_path: str, list_json: Dict[str, Any], output_name: str | None = None) -> Presentation:
        """
        使用内存中的 JSON 数据填充 PPT 模板。

        Args:
            template_path: PPT 模板文件路径 (.pptx)
            data: 用于填充的字典数据
            output_name: 输出文件名（可选），默认 `result.pptx`

        Returns:
            Presentation: 填充后的 Presentation 对象
        """
        if not os.path.exists(template_path):
            raise FileNotFoundError(f"PPT 模板不存在: {template_path}")

        pres = Presentation(template_path)
        original_count = len(pres.slides)

        for item in list_json:
            t = item.get("type")
            content = item.get("content")
            skip_fill = item.get("skip_fill", False)
            if t not in SLIDE_INDEX_MAP:
                continue
            template_slide = pres.slides[SLIDE_INDEX_MAP[t]]
            new_slide = self.duplicate_slide_to_end(template_slide, pres)
            if content and not skip_fill:
                self.replace_content(new_slide, t, content)

        self.delete_slides(pres, list(range(original_count)))
        return pres
    
    def duplicate_slide_to_end(self, template_slide, pres):
        new_slide = pres.slides.add_slide(template_slide.slide_layout)
        for shape in list(new_slide.shapes):
            shape.element.getparent().remove(shape.element)
        for shape in template_slide.shapes:
            new_el = deepcopy(shape.element)
            new_slide.shapes._spTree.append(new_el)
        if template_slide.background:
            try:
                new_slide.background._element = deepcopy(template_slide.background._element)
            except:
                pass
        return new_slide

    def replace_content(self, slide, type_name: str, data: Any):
        if not isinstance(data, dict):
            return
        placeholders = {self._shape_text(s): s for s in slide.shapes if self._shape_text(s)}
        for key, val in data.items():
            if key not in placeholders:
                continue
            shape = placeholders[key]
            conf = STYLE_CONFIG.get(type_name, {}).get(key, {})
            if isinstance(val, dict) and val.get("type") == "image":
                path = val["path"]
                if path and Path(path).exists():
                    self._insert_image(slide, shape, path, conf)
            elif (isinstance(val, dict) and val.get("type") == "table") or (
                    isinstance(val, str) and val.endswith(".csv")):
                path = val.get("path") if isinstance(val, dict) else val
                conf_table = STYLE_CONFIG.get(type_name, {}).get(key, {})
                csv_str = val.get("content") if isinstance(val, dict) else val
                if path and Path(path).exists():
                    self._insert_table_from_csv(slide, shape, csv_path=path, conf=conf_table)
                elif csv_str:
                    self._insert_table_from_csv(slide, shape, csv_str=csv_str, conf=conf_table)


            else:
                text = val["text"] if isinstance(val, dict) and val.get("text") else val
                pre_str = PRE_STR_MAP.get(type_name, "")
                if not text:
                    text = ""
                if "TITLE" in key:
                    text = pre_str + text
                self._insert_text_from_markdown(shape, text, conf)

    def _shape_text(self, shape) -> str:
        return getattr(shape, "text", "").strip() if hasattr(shape, "text") else ""


    def _replace_text(self, shape, text: str, conf: dict):
        if hasattr(shape, "text_frame"):
            shape.text_frame.clear()
            p = shape.text_frame.paragraphs[0]
            p.text = str(text)
            font_size = Pt(conf.get("font_size")) if conf.get("font_size") else None
            bold = conf.get("bold")
            font_color = RGBColor(*conf["color"]) if conf.get("color") else None
            align = conf.get("align")
            font_name = conf.get("font_name", "Microsoft YaHei")  # 默认微软雅黑
            if font_size or bold is not None or font_color:
                for run in p.runs:
                    if font_name:
                        run.font.name = font_name
                    if font_size:
                        run.font.size = font_size
                    if bold is not None:
                        run.font.bold = bold
                    if font_color:
                        run.font.color.rgb = font_color
            if align:
                p.alignment = align


    def _insert_image(self, slide, shape, image_path: str, conf: dict):
        left, top = shape.left, shape.top
        width = Inches(conf.get("width")) if conf.get("width") else shape.width
        height = Inches(conf.get("height")) if conf.get("height") else shape.height
        width = Cm(conf.get("width_cm")) if conf.get("width_cm") else width
        height = Cm(conf.get("height_cm")) if conf.get("height_cm") else height
        print(image_path)
        try:
            slide.shapes.add_picture(image_path, left, top, width=width, height=height)
        except Exception as e:
            self._replace_text(shape, "", conf)
            return
        slide.shapes._spTree.remove(shape.element)  # 删除模板图占位



    def _add_solid_fill_to_tcPr(self, tcPr, rgb_tuple):
        """
        在单元格 tcPr 内附加一个 <a:solidFill><a:srgbClr val="RRGGBB"/></a:solidFill>
        使用 OxmlElement，兼容不同 pptx 版本。
        """
        # create a:solidFill
        solidFill = OxmlElement('a:solidFill')
        srgbClr = OxmlElement('a:srgbClr')
        # set hex color value
        hexval = "%02X%02X%02X" % tuple(rgb_tuple)
        srgbClr.set('val', hexval)
        solidFill.append(srgbClr)
        tcPr.append(solidFill)


    def _insert_table_from_csv(self, slide, template_shape, csv_path: str = None, csv_str: str = None, conf: dict = None):
        conf = conf or {}
        rows = []
        if csv_path:
            with open(csv_path, newline='', encoding='utf-8') as f:
                reader = csv.reader(f)
                for row in reader:
                    rows.append(row)

        elif csv_str:
            csv_content = io.StringIO(csv_str)
            reader = csv.reader(csv_content)
            for row in reader:
                rows.append(row)

        if not rows:
            return

        n_rows = len(rows)
        n_cols = max(len(r) for r in rows)

        left, top = template_shape.left, template_shape.top

        # 计算总宽度（EMU），优先使用 conf width（英寸），否则用模板占位的宽（EMU）
        width_in = conf.get("width", None)
        height_in = conf.get("height", None)
        total_width = Inches(width_in) if width_in else template_shape.width
        total_height = Inches(height_in) if height_in else template_shape.height

        # 创建表格
        table_shape = slide.shapes.add_table(n_rows, n_cols, left, top, total_width, total_height)
        table = table_shape.table

        # 样式配置
        font_name = conf.get("font_name", "Microsoft YaHei")
        font_size = Pt(conf.get("font_size", 11)) if conf.get("font_size") else Pt(11)
        bold = conf.get("bold", False)
        color = conf.get("color", [0, 0, 0])
        align = conf.get("align", PP_ALIGN.CENTER)

        # header
        header_font_name = conf.get("header_font_name", font_name)
        header_font_size = Pt(conf.get("header_font_size", int(font_size.pt)+1)) if conf.get("header_font_size") else Pt(int(font_size.pt)+1)
        header_bold = conf.get("header_bold", True)
        header_color = conf.get("header_color", [255, 255, 255])
        header_align = conf.get("header_align", PP_ALIGN.CENTER)
        header_bg_color = conf.get("header_bg_color", None)  # RGB or None
        bg_color = conf.get("bg_color", None)  # RGB or None

        row_height_in = conf.get("row_height", None)  # inches or None
        first_col_width_in = conf.get("first_col_width", None)  # inches or None

        # === 计算并分配列宽（EMU） ===
        # 如果只有一列，直接使用总宽
        if n_cols == 1:
            try:
                table.columns[0].width = total_width
            except Exception:
                pass
        else:
            # 将 first_col_width（若存在）换算为 EMU
            if first_col_width_in:
                first_col_emu = Inches(first_col_width_in)
                remaining = total_width - first_col_emu
                # 防御性：如果 remaining <= 0，则把所有列平均
                if remaining <= 0:
                    per_col = total_width // n_cols
                    for j in range(n_cols):
                        try:
                            table.columns[j].width = per_col
                        except Exception:
                            pass
                else:
                    # 首列
                    try:
                        table.columns[0].width = first_col_emu
                    except Exception:
                        pass
                    # 平均分配剩余给其他列
                    per_col = remaining // (n_cols - 1)
                    for j in range(1, n_cols):
                        try:
                            table.columns[j].width = per_col
                        except Exception:
                            pass
            else:
                # 没有首列宽度，所有列等分
                per_col = total_width // n_cols
                for j in range(n_cols):
                    try:
                        table.columns[j].width = per_col
                    except Exception:
                        pass

        # === 行高 ===
        if row_height_in:
            try:
                for r in table.rows:
                    r.height = Inches(row_height_in)
            except Exception:
                pass

        # 填充数据并应用样式
        for i, row_vals in enumerate(rows):
            for j in range(n_cols):
                val = row_vals[j] if j < len(row_vals) else ""
                cell = table.cell(i, j)
                # 设置文本
                cell.text = str(val)

                # 应用段落与 run 样式
                for p in cell.text_frame.paragraphs:
                    p.alignment = header_align if i == 0 else align
                    # 确保至少有 run（通常有）
                    if not p.runs:
                        continue
                    for run in p.runs:
                        if i == 0:
                            # header
                            if header_font_name:
                                run.font.name = header_font_name
                            if header_font_size:
                                run.font.size = header_font_size
                            run.font.bold = header_bold
                            run.font.color.rgb = RGBColor(*header_color)
                        else:
                            if font_name:
                                run.font.name = font_name
                            if font_size:
                                run.font.size = font_size
                            run.font.bold = bold
                            run.font.color.rgb = RGBColor(*color)

                # 表头背景色（用 OxmlElement 安全添加）
                if i == 0 and header_bg_color:
                    try:
                        tc = cell._tc
                        tcPr = tc.get_or_add_tcPr()
                        # 添加 solidFill
                        solidFill = OxmlElement('a:solidFill')
                        srgbClr = OxmlElement('a:srgbClr')
                        hexval = "%02X%02X%02X" % tuple(header_bg_color)
                        srgbClr.set('val', hexval)
                        solidFill.append(srgbClr)
                        tcPr.append(solidFill)
                    except Exception:
                        pass

                if i > 0 and bg_color:
                    try:
                        tc = cell._tc
                        tcPr = tc.get_or_add_tcPr()
                        # 添加 solidFill
                        solidFill = OxmlElement('a:solidFill')
                        srgbClr = OxmlElement('a:srgbClr')
                        hexval = "%02X%02X%02X" % tuple(bg_color)
                        srgbClr.set('val', hexval)
                        solidFill.append(srgbClr)
                        tcPr.append(solidFill)
                    except Exception:
                        pass


        # 最后尽量删除模板占位 shape（best-effort）
        try:
            slide.shapes._spTree.remove(template_shape.element)
        except Exception:
            pass

    def _insert_text_from_markdown(self, shape, value, conf: dict):
        """
        Robust Markdown-lite -> PPT paragraphs renderer.
        Fixes invisible-char-before-* issue by normalizing lines before detecting bullets.
        Supports:
        - bullet lines starting with '* ', '- ', '+ ' (real bullet via para.level=0)
        - inline **bold** anywhere (including inside bullets)
        - newline -> new paragraph
        conf supports: font_name, font_size (pt), bold (default), color [R,G,B], align (PP_ALIGN)
        """
        if not hasattr(shape, "text_frame"):
            return

        raw_text = "" if value is None else str(value)
        conf = conf or {}

        font_name = conf.get("font_name", "Microsoft YaHei")
        font_size = Pt(conf.get("font_size")) if conf.get("font_size") else None
        default_bold = conf.get("bold", None)
        color = conf.get("color", None)
        align = conf.get("align", None)

        tf = shape.text_frame
        tf.clear()

        lines = raw_text.splitlines()

        def _create_run(paragraph, txt, make_bold=None):
            run = paragraph.add_run()
            run.text = txt
            if font_name:
                run.font.name = font_name
            if font_size:
                run.font.size = font_size
            # bold precedence: explicit make_bold (True/False) > default_bold if provided
            if make_bold is not None:
                run.font.bold = make_bold
            elif default_bold is not None:
                run.font.bold = default_bold
            if color:
                run.font.color.rgb = RGBColor(*color)
            return run

        first_para = True
        for orig_line in lines:
            # Normalize leading characters that commonly break ^\s*\* detection:
            # remove BOM, zero-width-space, non-breaking space, left/right quotes, and surrounding control chars
            line = orig_line.lstrip('\ufeff\u200b\u00A0 \t"\'\u201c\u201d\u2018\u2019')

            # After stripping control/invisible chars, detect bullet at line start
            is_bullet = bool(re.match(r'^[\*\-\+]\s+', line))

            # If bullet detected, remove first marker and following spaces
            if is_bullet:
                line = re.sub(r'^[\*\-\+]\s+', '', line, count=1)

            # Get paragraph: first exists by default
            if first_para:
                para = tf.paragraphs[0]
                para.clear()   # remove any default run
                first_para = False
            else:
                para = tf.add_paragraph()
                para.clear()

            # set alignment if provided
            if align:
                try:
                    para.alignment = align
                except Exception:
                    pass

            # enable real bullet (relies on template placeholder having bullet style)
            if is_bullet:
                try:
                    para.level = 0
                except Exception:
                    pass

            # parse inline bold segments using non-greedy finditer
            last_idx = 0
            bold_found = False
            for m in re.finditer(r'\*\*(.+?)\*\*', line):
                bold_found = True
                s, e = m.span()
                # text before bold
                if s > last_idx:
                    seg = line[last_idx:s]
                    if seg:
                        _create_run(para, seg, make_bold=None)
                # bold text
                inner = m.group(1)
                _create_run(para, inner, make_bold=True)
                last_idx = e
            # trailing text after last bold
            if last_idx < len(line):
                trailing = line[last_idx:]
                if trailing:
                    _create_run(para, trailing, make_bold=None)

            # if no bold matches and paragraph has no runs (shouldn't happen because we added runs),
            # ensure we add the whole line
            if not bold_found and not para.runs:
                _create_run(para, line, make_bold=None)

    def delete_slides(self, pres, indices: List[int]):
        sldIdLst = pres.slides._sldIdLst
        for offset, idx in enumerate(indices):
            rId = sldIdLst[idx - offset]
            sldIdLst.remove(rId)
