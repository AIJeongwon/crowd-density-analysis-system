"""고정 버전 YOLOv5와 제한된 체크포인트 로더를 준비합니다."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

YOLOV5_COMMIT = "915bbf294bb74c859f0b41f1c23bc395014ea679"


def configure(root: Path):
    revision = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if revision != YOLOV5_COMMIT:
        raise ValueError("학습 코드는 공식 YOLOv5 v7.0 고정 버전이어야 합니다.")
    os.environ["YOLOv5_AUTOINSTALL"] = "false"
    os.environ["TORCH_FORCE_WEIGHTS_ONLY_LOAD"] = "1"
    os.environ.pop("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", None)
    os.environ["WANDB_MODE"] = "disabled"
    os.environ["COMET_MODE"] = "DISABLED"
    os.environ["CLEARML_OFFLINE_MODE"] = "1"
    os.environ["YOLOV5_CONFIG_DIR"] = str((root.parent / "lsi-config").resolve())
    os.environ["MPLCONFIGDIR"] = str((root.parent / "lsi-config" / "matplotlib").resolve())
    sys.path.insert(0, str(root.resolve()))
    import numpy as np
    import torch
    from models import common, yolo

    torch.set_num_threads(4)
    # 임의 피클 실행을 허용하지 않고, 실제 모델에 필요한 형식만 등록합니다.
    allowed = [
        torch.nn.Sequential, torch.nn.ModuleList, torch.nn.Conv2d,
        torch.nn.BatchNorm2d, torch.nn.SiLU, torch.nn.Upsample, torch.nn.MaxPool2d,
        common.Conv, common.Focus, common.SPP, common.SPPF, common.Bottleneck,
        common.C3, common.Concat, yolo.Detect, yolo.DetectionModel,
        (yolo.DetectionModel, "models.yolo.Model"),
        np.ndarray, np.dtype, np.core.multiarray._reconstruct, np.core.multiarray.scalar,
        np.dtypes.Float32DType, np.dtypes.Float64DType, np.dtypes.Int64DType,
    ]
    return torch.serialization.safe_globals(allowed)
