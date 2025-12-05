# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import argparse
import logging
import os
import re
from pathlib import Path
from fastapi.responses import StreamingResponse, JSONResponse
from typing import Optional
from urllib.parse import quote

import dotenv
import uvicorn
from fastapi import FastAPI, APIRouter, Header
from fastapi.responses import HTMLResponse
from fastapi.responses import FileResponse
from starlette import status

from deepinsight.config.config import load_config
from deepinsight.service.research.research import ResearchService
from deepinsight.service.conference.paper_extractor import PaperExtractionService, PaperParseException
from deepinsight.utils.log_utils import initRootLogger
from deepinsight.core.utils.research_utils import load_expert_config
from deepinsight.service.schemas.common import ResponseModel
from deepinsight.service.schemas.research import ResearchRequest, PPTGenerateRequest, PdfGenerateRequest
from deepinsight.service.schemas.paper_extract import ExtractPaperMetaRequest

dotenv.load_dotenv(override=True)
initRootLogger("deepinsight")

DEFAULT_CONFIG_PATH = str(Path(__file__).resolve().parent.parent.parent / 'config.yaml')
DEFAULT_EXPERT_PATH = str(Path(__file__).resolve().parent.parent.parent / 'experts.yaml')

parser = argparse.ArgumentParser(description="Start DeepInsight API server")
parser.add_argument(
    "--config",
    type=str,
    default=DEFAULT_CONFIG_PATH,
    help="Path to config.yaml file"
)
parser.add_argument(
    "--expert_config",
    type=str,
    default=DEFAULT_EXPERT_PATH,
    help="Path to config.yaml file"
)
args = parser.parse_args()

config = load_config(args.config)

research_service = ResearchService(config)
paper_extract_service = PaperExtractionService(config)
# 加载专家数据
experts = load_expert_config(args.expert_config)
router = APIRouter(tags=["deepinsight"])


@router.get("/health", response_model=ResponseModel[str])
async def health():
    return ResponseModel(data="healthy")


@router.get("/deepinsight/charts/image/{file_id}")
async def show_chart_image(file_id: str):
    """
    返回对应的 PNG 图片文件
    """
    safe_pattern = re.compile(r"[\d\w:/-]+")
    safe_file_id = "".join(safe_pattern.findall(file_id))

    chart_dir = os.path.abspath(os.path.join(config.workspace.work_root, config.workspace.chart_image_dir))
    file_name = f"{safe_file_id}.png"
    file_path = os.path.abspath(os.path.join(chart_dir, file_name))
    logging.debug(f"image_path: {file_path} {os.path.exists(file_path)}")

    if not os.path.exists(file_path):
        return get_json_result(code=100, message="image file not found")

    try:
        # FileResponse 会自动设置正确的 Content-Type (image/png)
        return FileResponse(file_path, media_type="image/png", headers={"Cache-Control": "max-age=120"})
    except Exception as e:
        return get_json_result(code=100, message=repr(e))


@router.get('/deepinsight/charts/{file_id}')
async def show_chart(file_id: str):
    safe_pattern = re.compile(r"[\d\w:/-]+")
    safe_file_id = "".join(safe_pattern.findall(file_id))

    chart_dir = os.path.abspath(os.path.join(config.workspace.work_root, config.workspace.chart_image_dir))
    file_name = f"{safe_file_id}.html"
    file_path = os.path.abspath(os.path.join(chart_dir, file_name))
    logging.debug(f"file_path: {file_path} {os.path.exists(file_path)}")

    if not os.path.exists(file_path):
        return get_json_result(code=100, message="file not found")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return HTMLResponse(content=content, headers={"Cache-Control": "max-age=120"})
    except Exception as e:
        return get_json_result(code=100, message=repr(e))


def get_json_result(code=0, message="success", data=None):
    response = {"code": code, "message": message, "data": data}
    return JSONResponse(content=response)


@router.post("/deepinsight/chat")
async def deepinsight_chat(request: ResearchRequest):
    """
    Async endpoint for insight.
    """
    logging.info(f"request:  {request}")

    async def stream():
        async for event in research_service.chat(request=request):
            yield f"data: {event.model_dump_json()}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/deepinsight/paper/parse")
async def parse_paper_meta(request: ExtractPaperMetaRequest):
    """Parse metadata (title, author, abstract, keywords and number of sections) from a paper in Markdown format."""
    try:
        return await paper_extract_service.extract_and_store(request)
    except PaperParseException as e:
        return dict(error=str(e))


@router.get("/deepinsight/experts")
async def get_experts(type: Optional[str] = None):
    """
    获取专家信息，按类型分组返回专家名字列表。
    - `type` 参数可选，用于过滤专家类型（reviewer 或 writer）。
    """
    # 按类型分组专家名字
    experts_by_type = {}
    for expert in experts:
        if expert.type not in experts_by_type:
            experts_by_type[expert.type] = []
        experts_by_type[expert.type].append({"prompt_key": expert.prompt_key, "name": expert.name})

    # 如果提供了 type 参数，返回该类型下的专家名字列表
    if type:
        if type in experts_by_type:
            return ResponseModel(data={type: experts_by_type[type]})
        else:
            return get_json_result(code=404, message=f"No experts found for type: {type}", data=None)

    # 否则返回所有类型的专家名字列表
    return ResponseModel(data=experts_by_type)



@router.post("/deepinsight/ppt/generate")
async def ppt_generate(request: PPTGenerateRequest):
    pptx_stream, output_name = await research_service.ppt_generate(request=request)
    output_name = output_name.split("/")[-1]
    encoded_file_name = quote(output_name)
    # 返回文件流
    return StreamingResponse(
        pptx_stream,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f"attachment; filename={encoded_file_name}"}
    )


@router.post("/deepinsight/pdf/generate")
async def pdf_generate(request: PdfGenerateRequest):
    try:
        pdf_stream, output_name = await research_service.pdf_generate(request=request)

        # 文件名安全处理
        output_name = output_name.split("/")[-1]
        encoded_file_name = quote(output_name)

        return StreamingResponse(
            pdf_stream,
            media_type="text/pdf; charset=utf-8",
            headers={
                "Content-Disposition": f"attachment; filename={encoded_file_name}",
                "Content-Type": "application/octet-stream",
            },
        )

    except FileNotFoundError as e:
        logging.error(f"{str(e)}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "code": status.HTTP_400_BAD_REQUEST,
                "message": f"生成失败，部分源文件未找到：{str(e)}",
                "data": None
            }
        )

    except ValueError as e:
        logging.error(f"{str(e)}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "code": status.HTTP_400_BAD_REQUEST,
                "message": f"生成失败，数据格式错误：{str(e)}",
                "data": None
            }
        )

    except Exception as e:
        logging.error(f"{str(e)}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "code": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "message": f"服务器内部错误，请稍后重试：{str(e)}",
                "data": None
            }
        )
    
    
app = FastAPI(title="DeepInsight API")

app.include_router(router, prefix=config.app.api_prefix)

if __name__ == "__main__":
    for route in app.routes:
        from fastapi.routing import APIRoute

        if isinstance(route, APIRoute):
            print(f"路径: {route.path}, 方法: {route.methods}, 名称: {route.name}")
    uvicorn.run(
        app,
        host=config.app.host,
        port=config.app.port,
        reload=config.app.reload,
    )
