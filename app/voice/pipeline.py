import asyncio
import os
import sys
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure repository root is in sys.path
REPO_ROOT = Path(__file__).parent.parent.parent.resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    StartFrame,
    TextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.piper.tts import PiperTTSService
from pipecat.services.whisper.stt import WhisperSTTService
from pipecat.turns.user_turn_processor import UserTurnProcessor

from app.api.config import get_settings
from app.voice.llm_client import run_chat_turn

settings = get_settings()
TARGET_SAMPLE_RATE = 16000


def load_and_resample_wav(wav_path: str) -> Tuple[bytes, int, int]:
    """
    Reads a WAV file, resampling to 16000 Hz 16-bit mono PCM if needed.
    Returns (raw_pcm_bytes, sample_rate, num_channels).
    """
    with wave.open(wav_path, "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        pcm_bytes = wf.readframes(n_frames)

    if sampwidth != 2:
        raise ValueError(f"Unsupported sample width: {sampwidth} bytes (must be 2 bytes / 16-bit PCM)")

    audio_int16 = np.frombuffer(pcm_bytes, dtype=np.int16)

    # Convert multi-channel to mono
    if n_channels > 1:
        audio_int16 = audio_int16.reshape(-1, n_channels).mean(axis=1).astype(np.int16)
        n_channels = 1

    # Resample to 16000 Hz if necessary
    if framerate != TARGET_SAMPLE_RATE:
        duration = len(audio_int16) / float(framerate)
        target_len = int(round(duration * TARGET_SAMPLE_RATE))
        orig_indices = np.linspace(0, duration, len(audio_int16), endpoint=False)
        target_indices = np.linspace(0, duration, target_len, endpoint=False)
        resampled_float = np.interp(target_indices, orig_indices, audio_int16.astype(np.float32))
        audio_int16 = np.clip(resampled_float, -32768, 32767).astype(np.int16)
        framerate = TARGET_SAMPLE_RATE

    return audio_int16.tobytes(), framerate, n_channels


class LLMToolCallingProcessor(FrameProcessor):
    """
    Pipecat FrameProcessor that listens for TranscriptionFrames, calls `run_chat_turn`
    from llm_client.py (which handles tool calls against FastAPI), and emits TextFrames.
    """

    def __init__(self, history: Optional[List[Dict[str, Any]]] = None):
        super().__init__()
        self.history: List[Dict[str, Any]] = history if history is not None else []
        self.transcribed_text: Optional[str] = None
        self.response_text: Optional[str] = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            text = frame.text.strip()
            if text:
                self.transcribed_text = text
                logger.info(f"[STT] Transcribed user audio: '{text}'")
                response_text, self.history = run_chat_turn(self.history, text)
                self.response_text = response_text
                logger.info(f"[LLM] Generated response: '{response_text}'")
                await self.push_frame(TextFrame(text=response_text), direction)
        else:
            await self.push_frame(frame, direction)


class AudioSinkWriter(FrameProcessor):
    """
    FrameProcessor that captures synthesized output audio frames from TTS, measures the
    end-to-end latency relative to STT start, and accumulates output PCM bytes.
    Resets accumulated audio buffer when an InterruptionFrame is received.
    """

    def __init__(self, stt_start_time_fn):
        super().__init__()
        self.stt_start_time_fn = stt_start_time_fn
        self.first_audio_byte_time: Optional[float] = None
        self.latency_ms: Optional[float] = None
        self.audio_chunks: List[bytes] = []
        self.sample_rate: int = 22050
        self.num_channels: int = 1
        self.interrupted: bool = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InterruptionFrame):
            logger.info("[Barge-in] Interruption frame received in AudioSinkWriter. Clearing interrupted audio buffer.")
            self.audio_chunks.clear()
            self.first_audio_byte_time = None
            self.latency_ms = None
            self.interrupted = True
        elif isinstance(frame, (OutputAudioRawFrame, TTSAudioRawFrame)):
            if self.first_audio_byte_time is None:
                self.first_audio_byte_time = time.perf_counter()
                stt_start = self.stt_start_time_fn()
                if stt_start is not None:
                    self.latency_ms = (self.first_audio_byte_time - stt_start) * 1000.0
                    logger.info(f"[Metrics] End-to-end latency (STT start -> 1st TTS byte): {self.latency_ms:.2f} ms")

            self.audio_chunks.append(frame.audio)
            self.sample_rate = frame.sample_rate
            self.num_channels = frame.num_channels

        await self.push_frame(frame, direction)

    def write_wav(self, output_path: str):
        if not self.audio_chunks:
            logger.warning(f"No audio frames collected to write to {output_path}")
            return

        raw_pcm = b"".join(self.audio_chunks)
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with wave.open(output_path, "wb") as wf:
            wf.setnchannels(self.num_channels)
            wf.setsampwidth(2)  # 16-bit PCM
            wf.setframerate(self.sample_rate)
            wf.writeframes(raw_pcm)

        logger.info(f"Wrote output audio to {output_path} ({self.sample_rate}Hz, {self.num_channels}ch, {len(raw_pcm)} bytes)")


async def run_voice_pipeline(
    input_wav_path: str,
    output_wav_path: str,
    stt_model: str = "tiny.en",
    piper_voice: str = "en_US-lessac-medium",
) -> Dict[str, Any]:
    """
    Executes a full file-in, file-out Pipecat pipeline with VAD & barge-in interruption support:
    Input WAV -> Silero VAD -> UserTurnProcessor -> faster-whisper STT -> LLM tool-calling -> Piper TTS -> Output WAV.
    Measures and logs end-to-end latency (STT start to first TTS audio byte).
    """
    logger.info(f"Loading input audio file: {input_wav_path}")
    pcm_data, sample_rate, num_channels = load_and_resample_wav(input_wav_path)

    fixtures_dir = Path(__file__).parent / "audio_test_fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    # 1. Instantiate VAD, STT & TTS services
    vad_analyzer = SileroVADAnalyzer(sample_rate=sample_rate)
    vad_processor = VADProcessor(vad_analyzer=vad_analyzer)
    user_turn_processor = UserTurnProcessor()

    stt = WhisperSTTService(
        settings=WhisperSTTService.Settings(model=stt_model),
        device="cpu",
    )

    tts = PiperTTSService(
        settings=PiperTTSService.Settings(voice=piper_voice),
        download_dir=fixtures_dir,
    )

    # 2. Instantiate custom processors
    llm_processor = LLMToolCallingProcessor()

    stt_start_time: Optional[float] = None

    def get_stt_start():
        return stt_start_time

    audio_sink = AudioSinkWriter(get_stt_start)

    # 3. Assemble Pipecat pipeline with VAD and interruption support
    pipeline = Pipeline(
        [
            vad_processor,
            user_turn_processor,
            stt,
            llm_processor,
            tts,
            audio_sink,
        ]
    )

    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True))

    runner = PipelineRunner()
    runner_task = asyncio.create_task(runner.run(task))

    # Give pipeline background worker task a brief moment to initialize
    await asyncio.sleep(0.5)

    # 4. Feed audio frames into pipeline
    stt_start_time = time.perf_counter()
    logger.info("Starting audio stream feed into Pipecat pipeline...")

    # Feed in 20ms chunks (640 bytes for 16kHz mono 16-bit PCM)
    chunk_size = 640
    for i in range(0, len(pcm_data), chunk_size):
        chunk = pcm_data[i : i + chunk_size]
        frame = InputAudioRawFrame(audio=chunk, sample_rate=sample_rate, num_channels=num_channels)
        await task.queue_frame(frame)

    # Add short trailing silence so VAD detects speech stop at end of input
    silence_chunk = b"\x00" * chunk_size
    for _ in range(25):  # ~500ms trailing silence
        await task.queue_frame(InputAudioRawFrame(audio=silence_chunk, sample_rate=sample_rate, num_channels=num_channels))

    await task.queue_frame(EndFrame())

    # Wait for pipeline to finish
    await runner_task

    # 5. Write output WAV and compile metrics
    audio_sink.write_wav(output_wav_path)

    result = {
        "transcribed_text": llm_processor.transcribed_text,
        "response_text": llm_processor.response_text,
        "latency_ms": audio_sink.latency_ms,
        "output_wav_path": output_wav_path,
    }

    print("\n" + "=" * 50)
    print("PIPECAT PIPELINE RUN RESULT")
    print("=" * 50)
    print(f"Input WAV         : {input_wav_path}")
    print(f"Transcribed Text  : {result['transcribed_text']}")
    print(f"LLM Response Text : {result['response_text']}")
    print(f"End-to-End Latency: {result['latency_ms']:.2f} ms" if result['latency_ms'] else "End-to-End Latency: N/A")
    print(f"Output WAV        : {output_wav_path}")
    print("=" * 50 + "\n")

    return result


def main():
    default_input = str(Path(__file__).parent / "audio_test_fixtures" / "sample_greeting.wav")
    default_output = str(Path(__file__).parent / "audio_test_fixtures" / "output_greeting.wav")

    input_path = sys.argv[1] if len(sys.argv) > 1 else default_input
    output_path = sys.argv[2] if len(sys.argv) > 2 else default_output

    if not os.path.exists(input_path):
        print(f"Input file does not exist: {input_path}")
        sys.exit(1)

    asyncio.run(run_voice_pipeline(input_path, output_path))


if __name__ == "__main__":
    main()
