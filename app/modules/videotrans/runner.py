"""
PyVideoTrans Internal Core Runner.
===================================
Direct Python execution handler for Speech-to-Text (STT), Subtitle Translation (STS),
Text-to-Speech (TTS), and Video Translation (VTV).
"""

import os
import sys
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

# Ensure current module directory and nested videotrans package are in sys.path
MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
if MODULE_DIR not in sys.path:
    sys.path.insert(0, MODULE_DIR)


class PyVideoTransRunner:
    """Integrated Python runner for pyVideoTrans tasks."""

    def __init__(self, repo_path: Optional[str] = None):
        self.repo_path = repo_path or MODULE_DIR
        self.cli_path = os.path.join(self.repo_path, "cli.py")

    def execute_task_native(self, task_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Attempts direct in-process Python invocation of videotrans task functions.
        Falls back cleanly to subprocess CLI execution if in-process invocation raises or is unsupported.
        """
        try:
            if task_type == "stt":
                from videotrans.task.speech2text import SpeechToText
                from videotrans.task.taskcfg import TaskCfgSTT
                trk = SpeechToText(cfg=TaskCfgSTT(**params), out_format="srt")
                trk.prepare()
                trk.recogn()
                trk.task_done()
                return {"status": "success", "mode": "native"}

            elif task_type == "sts":
                from videotrans.task.translate_srt import TranslateSrt
                from videotrans.task.taskcfg import TaskCfgSTS
                trk = TranslateSrt(cfg=TaskCfgSTS(**params), out_format=0)
                trk.prepare()
                trk.trans()
                trk.task_done()
                return {"status": "success", "mode": "native"}

            elif task_type == "tts":
                from videotrans.task.dubbing import DubbingSrt
                from videotrans.task.taskcfg import TaskCfgTTS
                trk = DubbingSrt(cfg=TaskCfgTTS(**params), out_ext="wav")
                trk.prepare()
                trk.dubbing()
                trk.align()
                trk.task_done()
                return {"status": "success", "mode": "native"}

            elif task_type == "vtv":
                from videotrans.task.trans_video import TransVideo
                from videotrans.task.taskcfg import TaskCfgVTV
                trk = TransVideo(cfg=TaskCfgVTV(**params))
                trk.prepare()
                trk.trans()
                trk.task_done()
                return {"status": "success", "mode": "native"}

            else:
                raise ValueError(f"Unknown PyVideoTrans task type: {task_type}")

        except Exception as e:
            logger.debug(f"Native in-process PyVideoTrans execution for {task_type} skipped/failed ({e}); using subprocess CLI runner.")
            return self.execute_task_cli(task_type, params)

    def execute_task_cli(self, task_type: str, params: Dict[str, Any], timeout: int = 600) -> Dict[str, Any]:
        """Executes task via CLI runner."""
        if not os.path.exists(self.cli_path):
            raise RuntimeError(f"pyVideoTrans CLI script not found at: {self.cli_path}")

        args_list = ["--task", task_type]
        for key, val in params.items():
            flag = "output-dir" if key in ("output_dir", "output-dir") else key
            if val is True:
                args_list.append(f"--{flag}")
            elif val is False or val is None:
                continue
            else:
                args_list.extend([f"--{flag}", str(val)])

        python_executable = sys.executable
        cmd = [python_executable, self.cli_path] + args_list

        env = dict(os.environ)
        parent_modules = str(Path(self.repo_path).resolve().parent)
        env["PYTHONPATH"] = parent_modules + os.pathsep + env.get("PYTHONPATH", "")

        logger.info(f"Executing PyVideoTrans CLI: {' '.join(cmd)}")
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            cwd=self.repo_path,
            env=env
        )

        if proc.returncode != 0:
            stderr_msg = proc.stderr.strip() if proc.stderr else "Unknown error"
            logger.error(f"PyVideoTrans CLI error ({proc.returncode}): {stderr_msg}")
            raise RuntimeError(f"PyVideoTrans error: {stderr_msg}")

        return {
            "status": "success",
            "mode": "cli",
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip()
        }

    def _find_srt_file(self, source_path: str, search_dir: str) -> Optional[str]:
        """Discovers generated SRT file matching input name."""
        if not os.path.exists(search_dir):
            return None

        base_stem = os.path.splitext(os.path.basename(source_path))[0]
        candidates = [
            os.path.join(search_dir, f"{base_stem}.srt"),
            os.path.join(search_dir, f"{base_stem}.auto.srt"),
        ]
        for c in candidates:
            if os.path.exists(c):
                return c

        if os.path.isdir(search_dir):
            for fname in os.listdir(search_dir):
                if fname.endswith(".srt") and (base_stem in fname or fname.startswith(base_stem[:5])):
                    return os.path.join(search_dir, fname)

        return None

    def speech_to_text(
        self,
        video_or_audio_path: str,
        output_dir: Optional[str] = None,
        model_name: str = "large-v3",
        recogn_type: int = 0,
        detect_lang: str = "auto"
    ) -> Dict[str, Any]:
        """Speech-to-Text transcription task."""
        if not os.path.exists(video_or_audio_path):
            raise FileNotFoundError(f"Input file not found: {video_or_audio_path}")

        params = {
            "name": os.path.abspath(video_or_audio_path),
            "recogn_type": str(recogn_type),
            "model_name": model_name
        }
        if output_dir:
            params["output_dir"] = os.path.abspath(output_dir)
        if detect_lang and detect_lang != "auto":
            params["detect_language"] = detect_lang

        res = self.execute_task_cli("stt", params)
        target_dir = os.path.abspath(output_dir) if output_dir else os.path.dirname(os.path.abspath(video_or_audio_path))
        srt_path = self._find_srt_file(video_or_audio_path, target_dir)
        if srt_path:
            res["srt_path"] = srt_path

        return res

    def translate_subtitles(
        self,
        subtitle_file_path: str,
        target_lang: str = "vi",
        output_dir: Optional[str] = None,
        translate_provider: int = 0
    ) -> Dict[str, Any]:
        """Subtitle translation task."""
        if not os.path.exists(subtitle_file_path):
            raise FileNotFoundError(f"Subtitle file not found: {subtitle_file_path}")

        params = {
            "name": os.path.abspath(subtitle_file_path),
            "target_language_code": target_lang,
            "translate_type": str(translate_provider)
        }
        if output_dir:
            params["output_dir"] = os.path.abspath(output_dir)

        res = self.execute_task_cli("sts", params)
        target_dir = os.path.abspath(output_dir) if output_dir else os.path.dirname(os.path.abspath(subtitle_file_path))
        srt_path = self._find_srt_file(subtitle_file_path, target_dir)
        res["srt_path"] = srt_path or subtitle_file_path

        return res

    def text_to_speech(
        self,
        subtitle_file_path: str,
        voice_role: str = "vi-VN-HoaiMyNeural",
        output_dir: Optional[str] = None,
        tts_type: int = 0
    ) -> Dict[str, Any]:
        """Text-to-Speech synthesis task."""
        if not os.path.exists(subtitle_file_path):
            raise FileNotFoundError(f"Subtitle file not found: {subtitle_file_path}")

        params = {
            "name": os.path.abspath(subtitle_file_path),
            "tts_type": str(tts_type),
            "voice_role": voice_role
        }
        if output_dir:
            params["output_dir"] = os.path.abspath(output_dir)

        return self.execute_task_cli("tts", params)

    def translate_video(
        self,
        video_path: str,
        source_lang: str = "zh-cn",
        target_lang: str = "vi",
        voice_role: str = "vi-VN-HoaiMyNeural",
        output_dir: Optional[str] = None,
        use_cuda: bool = False
    ) -> Dict[str, Any]:
        """Video Translation (VTV) task."""
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        params = {
            "name": os.path.abspath(video_path),
            "source_language_code": source_lang,
            "target_language_code": target_lang,
            "voice_role": voice_role
        }
        if output_dir:
            params["output_dir"] = os.path.abspath(output_dir)
        if use_cuda:
            params["cuda"] = True

        return self.execute_task_cli("vtv", params)
