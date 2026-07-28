import asyncio
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from loguru import logger
import uvicorn

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    TTSAudioRawFrame,
    TextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.services.piper.tts import PiperTTSService
from pipecat.services.whisper.stt import WhisperSTTService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.turns.user_turn_processor import UserTurnProcessor

from app.api.config import get_settings
from app.api.main import app
from app.voice.conversation_logger import ConversationLogger
from app.voice.pipeline import LLMToolCallingProcessor

settings = get_settings()


class AudioFrameSerializer(FrameSerializer):
    """
    Serializer for browser WebSocket audio streaming.
    Receives raw 16kHz 16-bit PCM binary audio frames from mic.
    Sends output 22.05kHz 16-bit PCM binary audio frames to browser.
    Sends InterruptionFrame as JSON message: {"type": "interruption"}.
    """

    async def serialize(self, frame: Frame) -> Optional[str | bytes]:
        if isinstance(frame, (OutputAudioRawFrame, TTSAudioRawFrame)):
            return frame.audio
        elif isinstance(frame, InterruptionFrame):
            import json
            return json.dumps({"type": "interruption"})
        return None

    async def deserialize(self, data: str | bytes) -> Optional[Frame]:
        if isinstance(data, bytes):
            return InputAudioRawFrame(audio=data, sample_rate=16000, num_channels=1)
        return None


@app.get("/")
async def get_index():
    index_path = Path(__file__).parent.parent / "frontend" / "index.html"
    if not index_path.exists():
        return HTMLResponse("<h1>Frontend index.html not found</h1>", status_code=404)
    return HTMLResponse(content=index_path.read_text(), status_code=200)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("WebSocket connection accepted from client.")

    conv_logger = ConversationLogger()

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=AudioFrameSerializer(),
        ),
    )

    fixtures_dir = Path(__file__).parent / "audio_test_fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    vad_analyzer = SileroVADAnalyzer(
        sample_rate=16000,
        params=VADParams(
            confidence=0.7,
            start_secs=0.3,
            stop_secs=0.2,
            min_volume=0.6,
        ),
    )
    vad_processor = VADProcessor(vad_analyzer=vad_analyzer)
    user_turn_processor = UserTurnProcessor()

    stt_model_name = getattr(settings, "STT_MODEL", "distil-small.en")
    stt = WhisperSTTService(
        settings=WhisperSTTService.Settings(model=stt_model_name),
        device="cpu",
        ttfs_p99_latency=0.5,
    )

    tts = PiperTTSService(
        settings=PiperTTSService.Settings(voice="en_US-lessac-medium"),
        download_dir=fixtures_dir,
    )

    llm_processor = LLMToolCallingProcessor(logger_instance=conv_logger)

    pipeline = Pipeline(
        [
            transport.input(),
            vad_processor,
            user_turn_processor,
            stt,
            llm_processor,
            tts,
            transport.output(),
        ]
    )

    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True))
    runner = PipelineRunner()

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected to voice pipeline, sending initial greeting.")
        greeting_text = "Hi, thanks for calling in Medical Clinic, how may I help you?"
        llm_processor.history.append({"role": "assistant", "content": greeting_text})
        await task.queue_frame(TextFrame(text=greeting_text))

    try:
        await runner.run(task)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected.")
    except Exception as e:
        logger.error(f"Error running voice pipeline over WebSocket: {e}")


def main():
    uvicorn.run("app.voice.server:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
