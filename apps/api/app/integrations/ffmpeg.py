"""Deterministic, non-shell FFmpeg assembly behind a small adapter."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from apps.api.app.integrations.media import inspect_path


class FFmpegError(RuntimeError):
    pass


class FFmpeg:
    def __init__(self, settings: Any = None):
        self.ffmpeg = getattr(settings, "ffmpeg_binary", "ffmpeg")
        self.ffprobe = getattr(settings, "ffprobe_binary", "ffprobe")
        self.timeout = int(getattr(settings, "ffmpeg_timeout_seconds", 900))

    async def _run(self, *args: str) -> None:
        process = await asyncio.create_subprocess_exec(
            self.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "AV_LOG_FORCE_NOCOLOR": "1"},
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout)
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise FFmpegError("ffmpeg timed out") from exc
        if process.returncode != 0:
            raise FFmpegError(stderr.decode("utf-8", errors="replace")[-2000:])

    async def probe(self, path: Path) -> dict[str, Any]:
        return await inspect_path(path, self.ffprobe)

    async def _normalize(
        self, source: Path, destination: Path, width: int, height: int, fps: int
    ) -> dict[str, Any]:
        metadata = await self.probe(source)
        video_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p"
        )
        args = ["-i", str(source)]
        if metadata.get("has_audio"):
            args += [
                "-vf",
                video_filter,
                "-af",
                "aresample=48000:async=1:first_pts=0",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
            ]
        else:
            args += [
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-vf",
                video_filter,
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-shortest",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
            ]
        args += ["-movflags", "+faststart", "-y", str(destination)]
        await self._run(*args)
        return await self.probe(destination)

    async def _concat_cut(self, clips: list[Path], output: Path) -> None:
        manifest = output.parent / "concat.txt"
        lines = []
        for clip in clips:
            value = clip.resolve().as_posix().replace("'", "'\\''")
            lines.append(f"file '{value}'")
        await asyncio.to_thread(manifest.write_text, "\n".join(lines), encoding="utf-8")
        await self._run(
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(manifest),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-y",
            str(output),
        )

    async def _concat_crossfade(
        self, clips: list[Path], durations: list[float], output: Path, seconds: float
    ) -> None:
        inputs = [item for clip in clips for item in ("-i", str(clip))]
        filters: list[str] = []
        video_label = "0:v"
        audio_label = "0:a"
        elapsed = durations[0]
        for index in range(1, len(clips)):
            offset = max(0.0, elapsed - seconds)
            next_video = f"v{index}"
            next_audio = f"a{index}"
            filters.append(
                f"[{video_label}][{index}:v]xfade=transition=fade:duration={seconds}:offset={offset}[{next_video}]"
            )
            filters.append(
                f"[{audio_label}][{index}:a]acrossfade=d={seconds}:c1=tri:c2=tri[{next_audio}]"
            )
            video_label, audio_label = next_video, next_audio
            elapsed += durations[index] - seconds
        await self._run(
            *inputs,
            "-filter_complex",
            ";".join(filters),
            "-map",
            f"[{video_label}]",
            "-map",
            f"[{audio_label}]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-y",
            str(output),
        )

    async def assemble(
        self, inputs: list[Path], output: Path, config: dict[str, Any]
    ) -> dict[str, Any]:
        if not inputs:
            raise FFmpegError("assembly needs at least one clip")
        width = int(config.get("width", 1080))
        height = int(config.get("height", 1920))
        fps = int(config.get("fps", 24))
        transition = config.get("transition", "CUT")
        crossfade = float(config.get("crossfade_seconds", 0.5))
        if width <= 0 or height <= 0 or width % 2 or height % 2 or fps not in {24, 25, 30}:
            raise FFmpegError("invalid assembly dimensions or frame rate")
        output.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="studio-assembly-", dir=output.parent) as directory:
            normalized: list[Path] = []
            durations: list[float] = []
            for index, source in enumerate(inputs):
                destination = Path(directory) / f"scene-{index:03d}.mp4"
                metadata = await self._normalize(source, destination, width, height, fps)
                normalized.append(destination)
                durations.append(float(metadata.get("duration_seconds") or 0))
            assembled = Path(directory) / "assembled.mp4"
            if transition == "CUT" or len(normalized) == 1:
                await self._concat_cut(normalized, assembled)
            elif transition == "CROSSFADE":
                if min(durations) <= crossfade:
                    raise FFmpegError("crossfade must be shorter than every scene")
                await self._concat_crossfade(normalized, durations, assembled, crossfade)
            else:
                raise FFmpegError("unsupported transition")

            audio_mode = config.get("audio_mode", "KEEP_SCENE_AUDIO")
            background = config.get("background_audio_path")
            if background:
                volume = float(config.get("background_volume", 0.3))
                filter_value = (
                    f"[0:a]volume={0 if audio_mode == 'MUTE_SCENE_AUDIO' else 1}[scene];"
                    f"[1:a]volume={volume}[music];[scene][music]amix=inputs=2:duration=first:normalize=0[a]"
                )
                await self._run(
                    "-i",
                    str(assembled),
                    "-stream_loop",
                    "-1",
                    "-i",
                    str(background),
                    "-filter_complex",
                    filter_value,
                    "-map",
                    "0:v:0",
                    "-map",
                    "[a]",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-shortest",
                    "-movflags",
                    "+faststart",
                    "-y",
                    str(output),
                )
            elif audio_mode == "MUTE_SCENE_AUDIO":
                await self._run(
                    "-i",
                    str(assembled),
                    "-map",
                    "0:v:0",
                    "-an",
                    "-c:v",
                    "copy",
                    "-movflags",
                    "+faststart",
                    "-y",
                    str(output),
                )
            else:
                await asyncio.to_thread(shutil.copyfile, assembled, output)
        metadata = await self.probe(output)
        if not metadata.get("has_video"):
            raise FFmpegError("assembled output has no video stream")
        return metadata

    async def extract_frame(self, source: Path, output: Path, last: bool = True) -> dict[str, Any]:
        metadata = await self.probe(source)
        args = ["-sseof", "-0.1"] if last else []
        await self._run(
            *args,
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-y",
            str(output),
        )
        return {"width": metadata.get("width"), "height": metadata.get("height")}
