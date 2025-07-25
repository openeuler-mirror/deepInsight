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
from pathlib import Path

from camel.types import ModelPlatformType, ModelType

from deepinsight.config.model import ModelConfig
from deepinsight.core.orchestration import Orchestration, OrchestrationArtifact
from deepinsight.utils.console_utils import display_stream


def save_artifact(output_dir: Path, data: OrchestrationArtifact) -> None:
    """Save research artifacts to files"""
    output_dir.mkdir(exist_ok=True)

    # Example saving logic
    if data.report:
        (output_dir / 'report.md').write_text(data.report)


def main():
    parser = argparse.ArgumentParser(
        description="DeepInsight Research CLI",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "query",
        type=str,
        help="Research question or topic"
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        help="Directory to save research artifacts",
        default=None
    )
    parser.add_argument(
        "--verbose", "-v",
        action="count",
        default=1,
        help="Increase output verbosity"
    )

    args = parser.parse_args()

    print(f"\n🚀 Starting research: {args.query}")

    try:
        orchestration = Orchestration(
            model_config=ModelConfig(
                model_platform=ModelPlatformType.DEEPSEEK,
                model_type=ModelType.DEEPSEEK_CHAT,
                model_config_dict=dict(
                    stream=True
                ),
            ),
            mcp_tools_config_path="./mcp_config.json",
            research_round_limit=1,
        )
        result: OrchestrationArtifact = display_stream(orchestration.run(args.query))
        if args.output:
            save_artifact(args.output, result)
        print("\n🔍 Research Completed!")

        if args.output:
            print(f"\nArtifacts saved to: {args.output.absolute()}")

    except KeyboardInterrupt:
        print("\nResearch cancelled by user")
    except Exception as e:
        print(f"\n❌ Research failed: {str(e)}")
        if args.verbose > 0:
            import traceback
            traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
