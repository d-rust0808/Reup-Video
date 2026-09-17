import pytest


@pytest.fixture(autouse=True)
def _assume_vieneu_runtime_ok():
    """Existing TTS tests mock VieNeu; don't treat the poisoned Windows ORT as unavailable."""
    from app.modules.tts.providers import VieNeuTTSProvider

    previous = VieNeuTTSProvider._runtime_ok
    VieNeuTTSProvider._runtime_ok = True
    try:
        yield
    finally:
        VieNeuTTSProvider._runtime_ok = previous
