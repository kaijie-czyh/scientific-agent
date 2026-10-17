"""在独立临时目录运行真实 API，供浏览器测试研究主线，不接触用户项目。"""
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["SRA_WEB_SKIP_GUARD"] = "1"
os.environ["SRA_DRY_RUN"] = "true"

import core.config as configuration
import uvicorn

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="sra-flow-ui-") as temporary:
        configuration._default_config = configuration.GlobalConfig(
            paths=configuration.ProjectPaths.from_root(temporary), dry_run=True,
        )
        from web.api import app
        uvicorn.run(app, host="127.0.0.1", port=8012, log_level="warning")
