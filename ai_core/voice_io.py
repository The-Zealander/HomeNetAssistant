import os
import shutil
import subprocess
import tempfile
import threading
import time
import numpy as np  # Import numpy at the top of the file
from pathlib import Path
from typing import Optional

class LokalStemme:
    """Lokal dansk talegenkendelse og talesyntese til SARAH."""

    SAMPLE_RATE = 16000
    MAKS_OPTAGELSE_SEKUNDER = 30

    def __init__(self):
        try:
            import sounddevice
            from faster_whisper import WhisperModel
        except ImportError as fejl:
            raise RuntimeError(
                "Stemmeafhængigheder mangler. Kør "
                "'python -m pip install -r requirements.txt'."
            ) from fejl

        self._sounddevice = sounddevice
        if not shutil.which("espeak-ng"):
            raise RuntimeError("Dansk talesyntese mangler. Installér espeak-ng.")
        self._afspiller = shutil.which("paplay") or shutil.which("aplay")
        if not self._afspiller:
            raise RuntimeError("Ingen lydafspiller fundet. Installér paplay eller aplay.")

        modelnavn = os.environ.get("SARAH_TALEMODEL", "small")
        modelmappe = Path.home() / ".cache" / "sarah" / "whisper"
        print(
            f"Indlæser lokal Whisper-model '{modelnavn}'. "
            "Første start kan hente modellen og tage lidt tid."
        )
        try:
            self._model = WhisperModel(
                modelnavn,
                device="cpu",
                compute_type="int8",
                download_root=str(modelmappe),
            )
        except Exception as fejl:
            raise RuntimeError(
                "Whisper-modellen kunne ikke indlæses. Kontrollér internetforbindelsen "
                "ved første start og prøv igen."
            ) from fejl

    def optag(self) -> Optional[np.ndarray]:
        startet = threading.Event()
        faerdig = threading.Event()
        input_lukket = threading.Event()

        def vent_paa_enter():
            try:
                input("Tryk Enter og tal; tryk Enter igen, når du er færdig: ")
                startet.set()
                input()
            except EOFError:
                input_lukket.set()
            finally:
                faerdig.set()
                startet.set()

        inputstraad = threading.Thread(target=vent_paa_enter, daemon=True)
        inputstraad.start()
        if not startet.wait():
            return None
        if input_lukket.is_set():
            raise EOFError

        maks_frames = self.SAMPLE_RATE * self.MAKS_OPTAGELSE_SEKUNDER
        lydblokke = []

        def modtag_lyd(indata, frames, tidspunkt, status):
            lydblokke.append(indata.copy())

        print("Lytter...")
        tid_start = time.monotonic()
        try:
            with self._sounddevice.InputStream(
                samplerate=self.SAMPLE_RATE,
                channels=1,
                dtype="float32",
                callback=modtag_lyd,
            ):
                faerdig.wait(self.MAKS_OPTAGELSE_SEKUNDER)
        except self._sounddevice.PortAudioError as fejl:
            raise RuntimeError(
                "Mikrofonen kunne ikke åbnes. Kontrollér mikrofonens tilladelser og valg."
            ) from fejl

        optagetid = min(time.monotonic() - tid_start, self.MAKS_OPTAGELSE_SEKUNDER)
        lyd = np.concatenate(lydblokke, axis=0) if lydblokke else np.empty((0, 1))
        lyd = lyd[: min(int(optagetid * self.SAMPLE_RATE), maks_frames)]
        if lyd.size == 0 or np.max(np.abs(lyd)) < 0.005:
            return None
        return lyd.reshape(-1)

    def genkend(self, lyd: np.ndarray) -> str:
        segmenter, _ = self._model.transcribe(
            lyd,
            language="da",
            beam_size=5,
            vad_filter=True,
        )
        return " ".join(segment.text.strip() for segment in segmenter).strip()

    def sig(self, tekst: str):
        if not tekst.strip():
            return

        with tempfile.TemporaryDirectory(prefix="sarah-stemme-") as tempmappe:
            lydfil = Path(tempmappe) / "svar.wav"
            resultat = subprocess.run(
                ["espeak-ng", "-v", "da", "-s", "165", "-w", str(lydfil)],
                input=tekst,
                capture_output=True,
                text=True,
                check=False,
            )
            if resultat.returncode:
                raise RuntimeError(f"Dansk talesyntese fejlede: {resultat.stderr.strip()}")
            subprocess.run(
                [self._afspiller, "-q", str(lydfil)]
                if Path(self._afspiller).name == "aplay"
                else [self._afspiller, str(lydfil)],
                check=True,
            )
