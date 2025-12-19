# MinerU 环境准备指南

DeepInsight同时支持MinerU的两种版本：

- 在线版本：支持2025年9月启用的V4 API
- 离线版本：使用MinerU通过PYPI/DockerFile安装与部署的本地解析能力（仅支持WEB API接口形式）

如果您部署的网络环境不允许访问`https://mineru.net/api/v4`（及其子路径）或该API接口依赖的源文件上传/解析结果下载使用的OBS服务（可能为`https://mineru.oss-cn-shanghai.aliyuncs.com`等），您请参考章节[部署MinerU离线解析环境](#部署mineru离线解析环境)以使用MinerU或使用LlamaIndex等其他受支持的解析服务（但部分依赖MinerU的功能可能不可用）。

## 使用MinerU在线解析环境

注意：MinerU在线解析服务为由MinerU提供的云化环境，所有被解析的文档（及其原始二进制）均会**被MinerU在云端保存约30天**。如您的文档对信息安全有要求，请使用[部署MinerU离线解析环境](#部署mineru离线解析环境)或其他离线解析环境以避免信息安全风险。

为了让DeepInsight可使用MinerU在线解析服务，您需要在MinerU提供的[API管理页面](https://mineru.net/apiManage/token)获得一个API KEY。首次使用可能需要填写调查问卷以开通服务（该问卷由MinerU收集，被搜集内容与DeepInsight无关）。

截止2025年12月，MinerU在线解析服务API KEY存在为期14天的有效时长约束，请注意及时为您的DeepInsight服务刷新有效的API KEY。

## 部署MinerU离线解析环境

- 使用NVIDIA加速卡（或CPU）的完整的容器化部署说明请参考[使用Docker部署MinerU](https://opendatalab.github.io/MinerU/quick_start/docker_deployment/)。
- 使用昇腾加速卡（Ascend）的部署说明可参考[Ascend-MinerU页面](https://opendatalab.github.io/MinerU/zh/usage/acceleration_cards/Ascend/)。
- 其他加速卡的部署方法请在[使用文档](https://opendatalab.github.io/MinerU/zh/usage/)中查找。

本小节上述三个页面均由MinerU维护。此外，MinerU还提供了支持Ascend等加速卡的容器构建说明。示例Dockerfile可以参考`opendatalab/MinerU`仓库的`docker/china/`目录。

这些文档构造的容器可用于部署Gradio/OpenAI/WEB等风格的解析服务。DeepInsight**仅支持其WEB API风格接口**。

如您自行构建/安装MinerU服务，除了`mineru[core]`软件包及必要的运行时，请参考MinerU提供的容器构建脚本安装额外的依赖（如字体库及OpenGL支持），否则可能遇到图片显示异常等问题。

### 生产环境安全说明

截止MinerU 3.6.8版本（[PyPI](https://pypi.org/project/mineru/2.6.8/)），MinerU标准WEB API服务（`mineru.cli.fast_api`包或`mineru-api`命令）包含以下可能的风险行为，可导致成为意外的攻击入口：

- 将来自请求体的路径参数`output_dir`与文件名作为解析过程中产生的临时文件存放位置（且部分临时文件不会被删除）。
- 如未设置环境变量`MINERU_API_ENABLE_FASTAPI_DOCS`，或该环境变量的值为`1`, `true`, `yes`中的一个（不区分大小写），则会在服务中通过主流路径（`/docs`, `/redoc`与`/openapi.json`）且无需身份认证的方式挂载SwaggerUI文档展示与编辑页面及OpenAPI JSON文件。

因此：

- 如您使用该服务端代码进行部署，请注意服务器安全性及磁盘容量问题。DeepInsight建议您设置`MINERU_API_ENABLE_FASTAPI_DOCS=0`，并使该容器的HTTP端口对集群外不可访问，且定期清理磁盘或重置MinerU服务。
- 如您因安全性需要决定自行编写兼容服务端，应支持[参数兼容性说明](#参数兼容性说明)小节给出的所有参数。

### MinerU版本兼容性说明

DeepInsight已验证与以下版本的MinerU Wheel包（[PyPI](https://pypi.org/project/mineru/2.6.8/)）的兼容性：

- `mineru[core]==2.6.8`（发布于2025年12月）

不同版本的MinerU WEB API接口定义可能发生变更，如遇兼容性问题，可尝试部署已验证兼容性的MinerU WEB API服务。该风格的接口通常具有以下特征（根据2.6.8版本）：

- 默认监听端口为`8000`；
- 默认容器名称为`mineru-api`（根据docker compose yaml）；
- 使用`docker compose`启动容器时，命令带有参数`--profile api`；
- 使用`mineru-api`命令启动；
- 或通过python模块`mineru.cli.fast_api`启动；
- 访问该解析服务的`/docs`路径（比如`http://localhost:8000/docs`）可以得到一个Swagger UI接口说明页面，其中包含接受POST请求的接口`/file_parse`。

如您已确定使用了已兼容的版本但仍无法使用离线MinerU解析，请按上述特征检查已启动的服务是否为WEB API服务。

### 参数兼容性说明

> 仅当您因安全性需要而自行编写一个兼容MinerU的服务端时需要阅读此小节。

DeepInsight的MinerU WEB API客户端会对指定的`base_url`（允许包含path字段的URL）的子路径`/file_parse`发送一个`multipart/form-data`的POST请求，使用到的参数字段包括：

- `parse_method`：总是为`"auto"`；
- `return_md`：总是为`True`（请求体中为`true`）；
- `return_images`：总是为`True`（请求体中为`true`）；
- `response_format_zip`：可配置为`True`或`False`）（请求体中为`true`或`false`）；
- 文件上传使用的字段`files`。

您的服务端不应拒绝接受上述参数且其行为（响应体结构）应与MinerU服务端保持一致。

您可以将服务路径设置在更深层目录，比如`http://127.0.0.1/example/file_parse`，此时您将`base_url`设置为`http://127.0.0.1/example`即可使DeepInsight正确连接到您的MinerU兼容解析服务。
