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
import base64
import logging
import os
import re
from pathlib import Path
from fastapi.responses import StreamingResponse, JSONResponse
from typing import Optional
from urllib.parse import quote

import dotenv
import uvicorn
from fastapi import FastAPI, APIRouter, Body, Header
from fastapi.responses import HTMLResponse, Response
from fastapi.responses import FileResponse
from starlette import status

from deepinsight.config.config import load_config
from deepinsight.service.conference import ConferenceService
from deepinsight.service.research.research import ResearchService
from deepinsight.service.conference.paper_extractor import PaperExtractionService, PaperParseException
from deepinsight.utils.log_utils import initRootLogger
from deepinsight.utils.file_storage import get_storage_impl
from deepinsight.utils.md_render import to_pdf
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
conference_service = ConferenceService(config)
get_storage_impl(config)
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
async def deepinsight_chat(
    request: ResearchRequest,
    ragflow_authorization: Optional[str] = Header(None, alias="ragflow-authorization")
):
    """
    Async endpoint for insight.
    """
    logging.info(f"request:  {request}")

    async def stream():
        async for event in research_service.chat(
            request=request,
            ragflow_authorization=ragflow_authorization
        ):
            yield f"data: {event.model_dump_json()}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/deepinsight/paper/parse")
async def parse_paper_meta(request: ExtractPaperMetaRequest):
    """Parse metadata (title, author, abstract, keywords and number of sections) from a paper in Markdown format."""
    try:
        return await paper_extract_service.extract_and_store(request)
    except PaperParseException as e:
        return dict(error=str(e))


@router.post("/deepinsight/paper/conference_meta")
async def get_conference_meta(
        kb_id: str = Body(description="ID of knowledge base"),
        kb_name: str = Body(description="Name of knowledge base. Currently should be in format 'conf_name+year'"
                                        " such as 'CAD+2025'.")
):
    """Get or create a conference of the specified knowledge base if it exists.

    If no conference refer to this knowledge base, create a new conference record by `kb_name`."""
    _ = kb_id  # unsupported yet
    split = kb_name.rsplit("+", 1)
    if len(split) != 2 or not split[-1].isdigit():
        return dict(error="Only knowledge base named as 'CONF+year' such as 'CAD+2025' can use Paper parser. "
                          "Rename your database or select another document parser.")
    conf_name = split[0]
    year = int(split[1])
    try:
        id_, fullname = await conference_service.get_or_create_conference.with_trace(conf_name, year)
        return dict(id=id_, fullname=fullname)
    except conference_service.ConferenceQueryException as e:
        return dict(error=str(e))


@router.post("/deepinsight/paper/parse/binary")
async def parse_paper_binary(
    filename: str = Body(),
    binary: str = Body(description="File binary in Base64 format"),
    conference_id: int = Body(),
    external_kb_id: str = Body(description="Only for storage and generate image URL."),
    from_page: int | None = Body(default=None, description="(todo) The first page index to parse (included). "
                                                           "`None` means the first page of the file."),
    to_page: int | None = Body(default=None, description="(todo) The last page index to parse (included). "
                                                         "`None` means the last page of the file."),
    img_base_url: str | None = Body(
        default=None,
        description="The prefix part of images in parsed doc. Default is the value of `workspace.resource_base_uri` "
                    "in config file (whose default value is '../../'.")
):
    """Parse metadata (title, author, abstract, keywords and number of sections) from a paper binary file."""
    _ = from_page, to_page
    binary = base64.b64decode(binary)
    try:
        doc, meta = await conference_service.ingest_single_paper.with_trace(
            conference_id=conference_id, kb_id_external=external_kb_id, filename=filename,
            binary=binary, resource_prefix=img_base_url)
        return dict(
            title=meta.paper_title,
            author_info=meta.author_info.model_dump(),
            abstract=meta.abstract,
            keywords=meta.keywords,
            topic=meta.topic,
            sections=[
                (chunk.page_content, chunk.page_content.lstrip().split("\n", 1)[0].strip(" \n#"))
                for chunk in doc.text
            ]
        )
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


@router.post("/deepinsight/deep_research/pdf/generate")
async def deep_research_pdf_generate(
        conversation_id: str = Body("Only to generate the response headers for filename. Can only be ASCII letters."),
        filename: str = Body("Only to generate the response headers for filename"),
        md_content: str = Body(description="The real markdown content to generate.")
):
    basic_filename = re.compile(r"[^A-Za-z0-9\-_=.]").sub("", conversation_id) + ".pdf"
    safe_filename = re.compile(r"""[\\/\n\r:*?"<>|]""").sub("", filename)
    if not safe_filename.lower().endswith(".pdf"):
        safe_filename += ".pdf"
    encoded_filename = quote(safe_filename, safe="")
    return Response(
        content=to_pdf(md_content, allow_local_files=False),
        media_type="text/pdf; charset=utf-8",
        headers={"Content-Disposition": f"attachment; "
                                        f'filename="deep-research-report-{basic_filename}"; '
                                        f"filename*=UTF-8''{encoded_filename}"}
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
