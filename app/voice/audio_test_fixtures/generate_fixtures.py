import os
import wave
from pathlib import Path
import numpy as np
from piper import PiperVoice
from piper.download_voices import download_voice

FIXTURES_DIR = Path(__file__).parent.resolve()
VOICE_ID = "en_US-lessac-medium"
TARGET_SAMPLE_RATE = 16000


def resample_pcm_to_16k_mono(pcm_bytes: bytes, orig_sample_rate: int) -> bytes:
    """
    Resamples 16-bit PCM audio bytes from orig_sample_rate to 16000 Hz mono.
    """
    audio_int16 = np.frombuffer(pcm_bytes, dtype=np.int16)
    if orig_sample_rate == TARGET_SAMPLE_RATE:
        return audio_int16.tobytes()

    # Time axes for original and target resampling
    duration = len(audio_int16) / float(orig_sample_rate)
    target_sample_count = int(round(duration * TARGET_SAMPLE_RATE))

    orig_indices = np.linspace(0, duration, len(audio_int16), endpoint=False)
    target_indices = np.linspace(0, duration, target_sample_count, endpoint=False)

    resampled_float = np.interp(target_indices, orig_indices, audio_int16.astype(np.float32))
    resampled_int16 = np.clip(resampled_float, -32768, 32767).astype(np.int16)
    return resampled_int16.tobytes()


def generate_wav_fixture(voice: PiperVoice, text: str, output_path: Path):
    """
    Synthesizes text using PiperVoice and writes a 16kHz 16-bit mono WAV file.
    """
    pcm_chunks = []
    for chunk in voice.synthesize(text):
        pcm_chunks.append(chunk.audio_int16_bytes)
    raw_pcm = b"".join(pcm_chunks)

    orig_sample_rate = voice.config.sample_rate
    resampled_pcm = resample_pcm_to_16k_mono(raw_pcm, orig_sample_rate)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)  # 16-bit PCM
        wav_file.setframerate(TARGET_SAMPLE_RATE)
        wav_file.writeframes(resampled_pcm)

    print(f"Generated fixture: {output_path} ({TARGET_SAMPLE_RATE}Hz mono WAV, {len(resampled_pcm)} bytes)")


def main():
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    model_path = FIXTURES_DIR / f"{VOICE_ID}.onnx"
    if not model_path.exists():
        print(f"Downloading Piper voice model '{VOICE_ID}' to {FIXTURES_DIR}...")
        download_voice(VOICE_ID, FIXTURES_DIR)

    voice = PiperVoice.load(str(model_path))

    greeting_text = "Hello! What services does the medical clinic offer?"
    booking_text = "Hello, my name is John Doe, born January 1st 1990. I would like to check available appointments."

    generate_wav_fixture(voice, greeting_text, FIXTURES_DIR / "sample_greeting.wav")
    generate_wav_fixture(voice, booking_text, FIXTURES_DIR / "sample_booking_request.wav")


if __name__ == "__main__":
    main()
