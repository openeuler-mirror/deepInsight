#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os

import uvicorn

if __name__ == "__main__":
    # 从环境变量获取配置，或使用默认值
    # 从环境变量获取配置，或使用默认值
    host = os.getenv("HOST", "0.0.0.0")  # 从环境变量读取HOST，默认监听所有接口
    port = int(os.getenv("PORT", "8000"))  # 从环境变量读取PORT，默认8000

    # 配置日志
    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["access"]["fmt"] = '%(asctime)s - %(levelname)s - %(message)s'
    log_config["formatters"]["default"]["fmt"] = '%(asctime)s - %(levelname)s - %(message)s'

    # 启动UVicorn服务器
    uvicorn.run(
        "deepinsight.api.app:app_instance",
        host=host,
        port=port,
        reload=True,  # 开发时启用热重载
        log_config=log_config,
        workers=1,  # 生产环境可以增加worker数量
        access_log=True
    )
