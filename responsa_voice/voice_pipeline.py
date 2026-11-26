"""
עוזר קולי לפרויקט השו"ת — צינור עיבוד קולי (צעד 2)

רכיבים:
- MicrophoneRecorder  — קליטת מיקרופון + VAD (webrtcvad)
- SpeechToText       — המרה דיבור-לטקסט (faster-whisper, מודל ivrit-ai)
- Speaker            — הקראת טקסט (Windows SAPI, offline)
- VoicePipeline      — לולאת PTT: הקלטה → STT → ניתוב → משוב קולי

הפעלה (PTT): Ctrl+Alt+V — לחיצה מתחילה הקלטה, לחיצה שוב מבטלת,
סיום אוטומטי אחרי 1.5 שניות של שקט (מקסימום 30 שניות).
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
from typing import Callable, Optional

import numpy as np

from config import (
    STTConfig,
    STT_CONFIG,
    TTSConfig,
    TTS_CONFIG,
    VADConfig,
    VAD_CONFIG,
    get_whisper_initial_prompt,
)
from engine_bridge import resolve_with_engine, warm_up as warm_up_engine


# ============================================================
# 1. MicrophoneRecorder — קליטת שמע + VAD
# ============================================================

class MicrophoneRecorder:
    """מקליט דיבור מהמיקרופון עם זיהוי תחילת/סוף דיבור (webrtcvad)."""

    def __init__(self, vad_config: VADConfig = VAD_CONFIG):
        self._cfg = vad_config
        self._sample_rate = vad_config.sample_rate
        self._frame_duration_ms = vad_config.frame_duration_ms
        self._frame_size = int(self._sample_rate * self._frame_duration_ms / 1000)
        self._padding_frames = int(
            vad_config.padding_duration_ms / self._frame_duration_ms
        )
        self._max_frames = int(
            vad_config.max_speech_duration * 1000 / self._frame_duration_ms
        )
        self._silence_frames = int(
            vad_config.silence_timeout * 1000 / self._frame_duration_ms
        )

    @staticmethod
    def list_devices() -> list[dict]:
        """מחזיר רשימת התקני שמע זמינים."""
        import sounddevice as sd

        devices = sd.query_devices()
        result = []
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0:
                result.append({
                    "index": i,
                    "name": d["name"],
                    "channels": d["max_input_channels"],
                    "sample_rate": d["default_samplerate"],
                })
        return result

    def record_utterance(
        self,
        cancel_event: Optional[threading.Event] = None,
        timeout: float = 30.0,
    ) -> Optional[np.ndarray]:
        """
        מקליט ביטוי דיבור אחד.

        Args:
            cancel_event: event לביטול ההקלטה מבחוץ.
            timeout: זמן מקסימלי להמתנה לתחילת דיבור (שניות).

        Returns:
            np.ndarray float32 [-1,1] @ 16kHz, או None אם אין דיבור / בוטל / פג זמן.
        """
        import sounddevice as sd
        import webrtcvad

        vad = webrtcvad.Vad(self._cfg.aggressiveness)
        audio_queue: queue.Queue[bytes] = queue.Queue()

        pre_buffer: list[bytes] = []
        speech_frames: list[bytes] = []
        voiced_count = 0
        unvoiced_count = 0
        in_speech = False
        start_time = time.time()

        def _callback(indata, frames, time_info, status):
            if status:
                print(f"[WARN] שמע: {status}")
            audio_queue.put(bytes(indata))

        with sd.InputStream(
            samplerate=self._sample_rate,
            channels=1,
            dtype="int16",
            blocksize=self._frame_size,
            callback=_callback,
        ):
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    print("[INFO] ההקלטה בוטלה")
                    return None

                # פג זמן המתנה לתחילת דיבור
                if not in_speech and (time.time() - start_time) > timeout:
                    print("[INFO] פג זמן ההמתנה לדיבור")
                    return None

                # פג זמן מקסימלי של הקלטה
                if in_speech and len(speech_frames) >= self._max_frames:
                    print("[INFO] הגעתי למגבלת אורך ההקלטה")
                    break

                try:
                    frame = audio_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                if len(frame) < self._frame_size * 2:
                    continue

                is_voiced = vad.is_speech(frame, self._sample_rate)

                if not in_speech:
                    pre_buffer.append(frame)
                    if len(pre_buffer) > self._padding_frames:
                        pre_buffer.pop(0)

                    if is_voiced:
                        voiced_count += 1
                        if voiced_count >= 3:  # 3 פריימים קוליים רצופים = תחילת דיבור
                            in_speech = True
                            speech_frames.extend(pre_buffer)
                            pre_buffer.clear()
                            unvoiced_count = 0
                    else:
                        voiced_count = 0
                else:
                    speech_frames.append(frame)
                    if is_voiced:
                        unvoiced_count = 0
                    else:
                        unvoiced_count += 1

                    if unvoiced_count >= self._silence_frames:
                        break

        if not in_speech or len(speech_frames) < self._padding_frames:
            return None

        raw = b"".join(speech_frames)
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return audio


# ============================================================
# 2. SpeechToText — המרה דיבור-לטקסט
# ============================================================

class SpeechToText:
    """ממיר שמע לטקסט عبر faster-whisper (מודל ivrit-ai, עברית)."""

    def __init__(self, stt_config: STTConfig = STT_CONFIG):
        self._cfg = stt_config
        self._model = None

    def load(self) -> None:
        """ טוען את המודל (עצלן — רק כשצריך)."""
        if self._model is not None:
            return

        from faster_whisper import WhisperModel

        print(f"[STT] טוען מודל: {self._cfg.model_name} (עשוי לקחת כדקה בפעם הראשונה)")
        cpu_threads = os.cpu_count() or 4
        self._model = WhisperModel(
            self._cfg.model_name,
            device=self._cfg.device,
            compute_type=self._cfg.compute_type,
            cpu_threads=cpu_threads,
        )
        print("[STT] המודל נטען")

    def transcribe(self, audio: np.ndarray) -> str:
        """
        ממיר שמע float32 @ 16kHz למחרוזת טקסט.

        Returns:
            טקסט מזוהה (עברית), או "" אם אין דיבור ברור.
        """
        self.load()

        initial_prompt = get_whisper_initial_prompt()
        segments, _info = self._model.transcribe(
            audio,
            language=self._cfg.language,
            initial_prompt=initial_prompt,
            beam_size=self._cfg.beam_size,
            temperature=(0.0, 0.2, 0.4),
            vad_filter=self._cfg.vad_filter,
            condition_on_previous_text=self._cfg.condition_on_previous_text,
        )

        parts: list[str] = []
        for seg in segments:
            if seg.avg_logprob < -1.0:
                continue
            parts.append(seg.text.strip())

        return " ".join(parts).strip()


# ============================================================
# 3. Speaker — הקראת טקסט (Windows SAPI)
# ============================================================

class Speaker:
    """הקראת טקסט عبر Windows SAPI (ללא רשת)."""

    # סימני זיהוי של קול עברי ב-Windows
    _HEBREW_MARKERS = ("40d", "he-il", "he_il", "עברית", "hebrew", "he ")

    def __init__(self, tts_config: TTSConfig = TTS_CONFIG):
        self._cfg = tts_config
        self._voice = None
        self._sapi = None

    @staticmethod
    def list_voices() -> list[dict]:
        """מחזיר רשימת כל הקולות הזמינים (SAPI5 + OneCore)."""
        import win32com.client

        voices = []
        try:
            sapi = win32com.client.Dispatch("SAPI.SpVoice")
            for v in sapi.GetVoices():
                desc = v.GetDescription()
                voices.append({
                    "name": desc,
                    "token_id": v.Id,
                    "source": "SAPI5",
                    "is_hebrew": Speaker._is_hebrew(desc),
                })
        except Exception as e:
            print(f"[WARN] שגיאה בקריאת קולות SAPI5: {e}")

        # חיפוש קולות OneCore (לא גלויים ל-SAPI5 ישירות)
        try:
            import winreg

            for hive, label in (
                (winreg.HKEY_LOCAL_MACHINE, "OneCore"),
            ):
                for sub in (
                    r"SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens",
                ):
                    try:
                        key = winreg.OpenKey(hive, sub)
                    except OSError:
                        continue
                    i = 0
                    while True:
                        try:
                            token_name = winreg.EnumKey(key, i)
                            i += 1
                            tkey = winreg.OpenKey(key, token_name)
                            try:
                                desc, _ = winreg.QueryValueEx(tkey, "")
                            except OSError:
                                desc = token_name
                            voices.append({
                                "name": desc,
                                "token_id": token_name,
                                "source": label,
                                "is_hebrew": Speaker._is_hebrew(desc) or Speaker._is_hebrew(token_name),
                            })
                        except OSError:
                            break
                    winreg.CloseKey(key)
        except Exception:
            pass

        return voices

    @classmethod
    def _is_hebrew(cls, text: str) -> bool:
        low = text.lower()
        return any(m in low for m in cls._HEBREW_MARKERS)

    @staticmethod
    def _find_onecore_hebrew_token_id() -> Optional[str]:
        """מחזיר את מזהה ה-token של קול עברי ב-OneCore, או None."""
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens",
            )
            i = 0
            while True:
                try:
                    token_name = winreg.EnumKey(key, i)
                    i += 1
                    tkey = winreg.OpenKey(key, token_name)
                    try:
                        desc, _ = winreg.QueryValueEx(tkey, "")
                    except OSError:
                        desc = token_name
                    if Speaker._is_hebrew(desc) or Speaker._is_hebrew(token_name):
                        winreg.CloseKey(tkey)
                        winreg.CloseKey(key)
                        return (
                            rf"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore"
                            rf"\Voices\Tokens\{token_name}"
                        )
                    winreg.CloseKey(tkey)
                except OSError:
                    break
            winreg.CloseKey(key)
        except Exception:
            pass
        return None

    def select_voice(self) -> bool:
        """
        בוחר קול עברי. סדר:
        1. override מפורש (TTSConfig.voice_name_override)
        2. קול עברי ב-SAPI5
        3. קול עברי ב-OneCore (דרך SpObjectToken)
        """
        self._ensure_sapi()
        if self._sapi is None:
            return False

        # 1. override מפורש
        if self._cfg.voice_name_override:
            for v in self._sapi.GetVoices():
                desc = v.GetDescription()
                if self._cfg.voice_name_override.lower() in desc.lower():
                    self._sapi.Voice = v
                    self._voice = v
                    print(f"[TTS] נבחר קול (override): {desc}")
                    return True

        # 2. חיפוש אוטומטי של קול עברי ב-SAPI5
        for v in self._sapi.GetVoices():
            desc = v.GetDescription()
            if self._is_hebrew(desc):
                self._sapi.Voice = v
                self._voice = v
                print(f"[TTS] נבחר קול עברי (SAPI5): {desc}")
                return True

        # 3. חיפוש קול עברי ב-OneCore (SpObjectToken)
        onecore_id = self._find_onecore_hebrew_token_id()
        if onecore_id:
            try:
                import win32com.client

                tok = win32com.client.Dispatch("SAPI.SpObjectToken")
                tok.SetId(onecore_id)
                self._sapi.Voice = tok
                self._voice = tok
                print(f"[TTS] נבחר קול עברי (OneCore): {tok.GetDescription()}")
                return True
            except Exception as e:
                print(f"[TTS] שגיאה בטעינת קול OneCore: {e}")

        return False

    def warm_up(self) -> None:
        """אתחול SAPI + בחירת קול מראש. לקרוא מתוך thread הקורא גם ל-speak (COM)."""
        self._ensure_sapi()
        self.select_voice()

    def speak(self, text: str, block: bool = True) -> bool:
        """
        מקריא טקסט. אם אין קול עברי — מדפיס במקום.

        Returns:
            True אם ההקראת בוצעה, False אם fallback to print.
        """
        self._ensure_sapi()

        if self._sapi is None:
            if self._cfg.fallback_to_print:
                print(f"[TTS-PRINT] {text}")
            return False

        if self._voice is None:
            if not self.select_voice():
                if self._cfg.fallback_to_print:
                    print(f"[TTS-PRINT] {text}")
                return False

        try:
            import pythoncom

            pythoncom.CoInitialize()
            # SAPI5 Speak flags: 0=SVSFDefault (synchronous/blocking), 1=SVSFlagsAsync
            self._sapi.Speak(text, 0 if block else 1)
            return True
        except Exception as e:
            print(f"[WARN] שגיאת TTS: {e}")
            if self._cfg.fallback_to_print:
                print(f"[TTS-PRINT] {text}")
            return False

    def _ensure_sapi(self) -> None:
        """אתחל אובייקט SAPI (בלי בחירת קול — select_voice עושה את זה)."""
        if self._sapi is not None:
            return

        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            self._sapi = win32com.client.Dispatch("SAPI.SpVoice")
            self._sapi.Rate = int((self._cfg.rate_wpm - 170) / 10)  # SAPI rate -10..10
            self._sapi.Volume = int(self._cfg.volume * 100)
        except Exception as e:
            print(f"[ERROR] לא ניתן לאתחל SAPI: {e}")
            self._sapi = None


# ============================================================
# 4. VoicePipeline — לולאת PTT ראשית
# ============================================================

class VoicePipeline:
    """
    לולאת עיבוד קולי: PTT → הקלטה → STT → ניתוב → משוב.

    threading: callback של hotkey רק מפעיל Event-ים (לא נוגע ב- audio/COM).
    ההקלטה, STT ו-TTS רצים ברצף על pipeline thread.
    """

    def __init__(
        self,
        on_transcript: Callable[[str], Optional[str]],
        *,
        recorder: Optional[MicrophoneRecorder] = None,
        stt: Optional[SpeechToText] = None,
        tts: Optional[Speaker] = None,
        on_status: Optional[Callable[[str, dict], None]] = None,
        tts_enabled: bool = True,
    ):
        self._on_transcript = on_transcript
        self._recorder = recorder or MicrophoneRecorder()
        self._stt = stt or SpeechToText()
        self._tts = tts or Speaker()
        self._on_status = on_status
        self._tts_enabled = tts_enabled

        self._record_event = threading.Event()
        self._cancel_event = threading.Event()
        self._running = threading.Event()
        self._ready_event = threading.Event()
        self._hotkey_lock = threading.Lock()
        self._hotkey_registered = False
        self._pipeline_thread: Optional[threading.Thread] = None

    def set_tts_enabled(self, enabled: bool) -> None:
        self._tts_enabled = bool(enabled)

    def _speak(self, text: str) -> None:
        if self._tts_enabled:
            self._tts.speak(text)

    def _register_hotkey(self, hotkey: Optional[str] = None) -> None:
        with self._hotkey_lock:
            import keyboard

            target = (hotkey or VAD_CONFIG.ptt_hotkey or "space").strip()
            try:
                keyboard.unhook_all_hotkeys()
            except Exception:
                pass
            keyboard.add_hotkey(target, self._on_hotkey, suppress=False)
            self._hotkey_registered = True
            print(f"[PIPELINE] פועל — PTT: {target.upper()}")
            print("[PIPELINE] לחץ את ה-hotkey כדי לדבר, לחץ שוב כדי לבטל")

    def update_hotkey(self, new_hotkey: str) -> None:
        """מעדכן את מקש ה-PTT בזמן ריצה."""
        VAD_CONFIG.ptt_hotkey = (new_hotkey or "space").strip()
        if self._running.is_set() and self._ready_event.is_set():
            self._register_hotkey(VAD_CONFIG.ptt_hotkey)

    def start(self, *, blocking_warmup: bool = True) -> None:
        """מפעיל את ה-PTT hotkey ולולאת pipeline."""
        if self._running.is_set():
            print("[WARN] ה-pipeline כבר רץ")
            return

        self._running.set()
        self._pipeline_thread = threading.Thread(target=self.run_forever, daemon=True)
        self._pipeline_thread.start()

        if blocking_warmup:
            self._ready_event.wait(timeout=300)
            if not self._hotkey_registered and self._running.is_set():
                self._register_hotkey(VAD_CONFIG.ptt_hotkey)

    def stop(self) -> None:
        """עוצר את ה-pipeline."""
        self._running.clear()
        self._cancel_event.set()
        self._record_event.set()

        try:
            import keyboard
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass

        if self._pipeline_thread and self._pipeline_thread.is_alive():
            self._pipeline_thread.join(timeout=3.0)
        print("[PIPELINE] נעצר")

    def _warm_up(self) -> None:
        """טעינה מוקדמת של STT, מנוע הזיהוי, ספריות מיקרופון ו-TTS — לפני רישום ה-PTT."""
        t0 = time.monotonic()
        try:
            warm_up_engine()
        except Exception as e:
            print(f"[WARMUP] אזהרה בטעינת מנוע הזיהוי: {e}")
        print("[WARMUP] טוען מודל Whisper (עשוי לקחת כדקה בפעם הראשונה)...")
        self._stt.load()
        print(f"[WARMUP] מודל Whisper נטען ({time.monotonic() - t0:.1f}s)")
        import sounddevice  # noqa: F401
        import webrtcvad  # noqa: F401
        self._tts.warm_up()
        print(f"[WARMUP] מוכן ({time.monotonic() - t0:.1f}s)")

    def _emit_status(self, state: str, data: Optional[dict] = None) -> None:
        """שולח עדכון מצב ל-HUD (אם סופק). לעולם לא חוסם/זורק."""
        if self._on_status is None:
            return
        try:
            self._on_status(state, data or {})
        except Exception:
            pass

    def _on_hotkey(self) -> None:
        """callback של hotkey — רק מפעיל Event-ים (thread-safe)."""
        if self._record_event.is_set() and not self._cancel_event.is_set():
            # הקלטה פעילה → ביטול
            self._cancel_event.set()
            print("[PTT] ביטול הקלטה")
        else:
            # לא מקליטים → התחלה
            self._cancel_event.clear()
            self._record_event.set()
            print("[PTT] מתחיל הקלטה...")

    def run_forever(self) -> None:
        """לולאת pipeline עיקרית: הקלטה → STT → on_transcript → משוב."""
        import pythoncom

        pythoncom.CoInitialize()
        self._emit_status(
            "loading",
            {
                "text": "טוען מודל זיהוי דיבור…",
                "corrected": "הפעלה ראשונה מורידה מודל (~1.5GB)",
            },
        )
        try:
            self._warm_up()
        except Exception as e:
            print(f"[WARMUP] שגיאה בטעינה מוקדמת: {e}")
            self._emit_status("error", {"text": "שגיאה בטעינת המודל", "corrected": str(e)[:120]})
        finally:
            self._ready_event.set()
        if self._running.is_set():
            try:
                self._register_hotkey(VAD_CONFIG.ptt_hotkey)
                self._emit_status(
                    "idle",
                    {
                        "text": "",
                        "corrected": f"מוכן — לחץ {VAD_CONFIG.ptt_hotkey.upper()} לדיבור",
                    },
                )
            except Exception as e:
                print(f"[PIPELINE] שגיאה ברישום hotkey: {e}")
                self._emit_status("error", {"text": "שגיאת מקש קיצור", "corrected": str(e)[:120]})
        try:
            while self._running.is_set():
                # מחכה ל-PTT
                self._record_event.wait(timeout=0.5)
                if not self._running.is_set():
                    break
                if not self._record_event.is_set():
                    continue

                # מקליט
                print("[PIPELINE] מקליט...")
                self._emit_status("listening")
                audio = self._recorder.record_utterance(cancel_event=self._cancel_event)
                self._record_event.clear()
                self._cancel_event.clear()

                if audio is None:
                    print("[PIPELINE] לא זוהה דיבור")
                    self._emit_status("idle")
                    continue

                # STT
                print("[PIPELINE] מזהה דיבור...")
                self._emit_status("transcribing")
                text = self._stt.transcribe(audio)
                if not text:
                    print("[PIPELINE] לא הצלחתי לזהות טקסט")
                    self._emit_status("error", {"text": ""})
                    self._speak("לא הובן")
                    continue

                print(f"[PIPELINE] זוהה: {text}")
                self._emit_status("understanding", {"text": text})

                # on_transcript — מחזיר feedback או None
                feedback = self._on_transcript(text)
                if feedback:
                    self._speak(feedback)
                else:
                    self._speak("לא הובן")
                self._emit_status("idle", {"text": text})
        finally:
            pythoncom.CoUninitialize()

    def transcribe_once(self) -> str:
        """מקליט ביטוי אחד ומחזיר את הטקסט המזוהה (ל-CLI)."""
        import pythoncom

        pythoncom.CoInitialize()
        try:
            self._stt.load()
            print("[PIPELINE] מקליט ביטוי אחד...")
            audio = self._recorder.record_utterance()
            if audio is None:
                return ""
            print("[PIPELINE] מזהה דיבור...")
            return self._stt.transcribe(audio)
        finally:
            pythoncom.CoUninitialize()


# ============================================================
# 5. CLI
# ============================================================

def _cmd_test_mic(recorder: MicrophoneRecorder) -> int:
    """מקליט 3 שניות ומדפיס RMS + מספר פריימים קוליים."""
    import sounddevice as sd
    import webrtcvad

    print("[TEST] מקליט 3 שניות... דבר עכשיו")
    frames = []
    vad = webrtcvad.Vad(VAD_CONFIG.aggressiveness)
    frame_size = int(VAD_CONFIG.sample_rate * VAD_CONFIG.frame_duration_ms / 1000)

    with sd.InputStream(
        samplerate=VAD_CONFIG.sample_rate,
        channels=1,
        dtype="int16",
        blocksize=frame_size,
    ) as stream:
        start = time.time()
        while time.time() - start < 3.0:
            data, _ = stream.read(frame_size)
            raw = bytes(data)
            frames.append(raw)

    if not frames:
        print("[FAIL] לא נקלט שמע")
        return 1

    raw = b"".join(frames)
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(audio ** 2)))

    voiced = sum(
        1 for f in frames
        if len(f) >= frame_size * 2 and vad.is_speech(f, VAD_CONFIG.sample_rate)
    )

    print(f"[TEST] RMS: {rms:.4f} (0=silence, >0.01=speech)")
    print(f"[TEST] פריימים קוליים: {voiced}/{len(frames)}")
    if voiced == 0:
        print("[WARN] לא זוהה דיבור — בדוק את המיקרופון")
        return 1
    print("[PASS] מיקרופון עובד")
    return 0


def _cmd_check_voices() -> int:
    """בודק קולות עבריים ב-SAPI + OneCore."""
    voices = Speaker.list_voices()
    hebrew = [v for v in voices if v["is_hebrew"]]

    print(f"[VOICES] נמצאו {len(voices)} קולות בסך הכל")
    for v in voices:
        marker = " ← עברי" if v["is_hebrew"] else ""
        print(f"  [{v['source']}] {v['name']}{marker}")

    if hebrew:
        print(f"\n[PASS] נמצא קול עברי: {hebrew[0]['name']}")
        return 0

    print("\n[WARN] לא נמצא קול עברי ב-SAPI.")
    print("  פתרונות:")
    print("  1. התקן חבילת דיבור עברית:")
    print("     הגדרות → זמן ושפה → שפה → עברית → דיבור")
    print("  2. מיזוג רישום (למתקדמים):")
    print("     העתק tokens מ-")
    print("       HKLM\\SOFTWARE\\Microsoft\\Speech_OneCore\\Voices\\Tokens")
    print("     אל")
    print("       HKLM\\SOFTWARE\\Microsoft\\Speech\\Voices\\Tokens")
    print("  בלי קול עברי, ההקראת תודפס במקום (fallback_to_print).")
    return 1


def main() -> int:
    import argparse

    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="עוזר קולי לפרויקט השו\"ת — צינור עיבוד קולי (צעד 2)"
    )
    parser.add_argument("--execute", action="store_true",
                        help="הרצה מלאה: PTT → STT → ניתוב → ביצוע בפועל")
    parser.add_argument("--no-hud", action="store_true",
                        help="בלי HUD צף (במקום --execute עם HUD)")
    parser.add_argument("--test-mic", action="store_true",
                        help="בודק מיקרופון: מקליט 3 שניות ומדפיס סטטיסטיקות")
    parser.add_argument("--check-voices", action="store_true",
                        help="בודק קולות עבריים ב-SAPI + OneCore")
    parser.add_argument("--transcribe-once", action="store_true",
                        help="מקליט ביטוי אחד ומדפיס את הטקסט המזוהה")
    parser.add_argument("--list-devices", action="store_true",
                        help="מציג התקני שמע זמינים")
    args = parser.parse_args()

    if args.list_devices:
        devices = MicrophoneRecorder.list_devices()
        print(f"[DEVICES] נמצאו {len(devices)} התקני קלט:")
        for d in devices:
            print(f"  [{d['index']}] {d['name']} ({d['channels']}ch, {int(d['sample_rate'])}Hz)")
        return 0

    if args.check_voices:
        return _cmd_check_voices()

    if args.test_mic:
        recorder = MicrophoneRecorder()
        return _cmd_test_mic(recorder)

    if args.transcribe_once:
        recorder = MicrophoneRecorder()
        stt = SpeechToText()
        pipeline = VoicePipeline(on_transcript=lambda t: t, recorder=recorder, stt=stt)
        text = pipeline.transcribe_once()
        if text:
            print(f"[RESULT] {text}")
            return 0
        print("[FAIL] לא זוהה טקסט")
        return 1

    if args.execute:
        from ai_verifier import resolve
        from config import VOICE_COMMANDS
        from responsa_controller import create_controller

        controller = create_controller()

        # HUD: tkinter mainloop ירוץ ב-thread הראשי; העדכונים מגיעים דרך queue
        OverlayHUD = None
        if not args.no_hud:
            try:
                from overlay import OverlayHUD  # type: ignore[no-redef]
            except Exception as e:
                print(f"[HUD] לא זמין: {e}")

        hud_q: Optional["queue.Queue[tuple[str, dict]]"] = (
            queue.Queue() if OverlayHUD is not None else None
        )

        def _emit(state: str, data: Optional[dict] = None) -> None:
            if hud_q is not None:
                hud_q.put((state, data or {}))

        _DETERMINISTIC_ERRORS = (
            "INVALID_RANGE",
            "INVALID_COMBINATION",
            "AMBIGUOUS_SECTION",
        )

        def _on_transcript(text: str) -> Optional[str]:
            print(f"[DEBUG] raw whisper text: {text!r}")
            # נסה קודם את המנוע החדש (בלי גרש/גרשיים)
            try:
                r = resolve_with_engine(text)
                print(
                    f"[DEBUG] engine result: status={r.status!r} "
                    f"cmd={r.command!r} payload={r.payload!r}"
                )
            except Exception as e:
                print(f"[ENGINE] שגיאה במנוע החדש: {e}")
                import traceback
                traceback.print_exc()
                r = None

            # שגיאות דטרמיניסטיות: לא מפעילים fallback LLM ולא מדביקים לשו"ת — מחזירים משוב TTS ישירות
            if r is not None and r.status in _DETERMINISTIC_ERRORS:
                feedback_msg = r.tts_feedback or "פקודה לא תקינה"
                print(f"[הבנה] שגיאה ({r.status}): '{text}' → {feedback_msg}")
                _emit("error", {"text": text, "corrected": feedback_msg})
                return feedback_msg

            # רק ב-NEEDS_FALLBACK (או קריסה) ננסה את ה-LLM הוותיק — עם אימות מקומי חובה
            if r is None or (
                r.command is None and getattr(r, "status", "NEEDS_FALLBACK") == "NEEDS_FALLBACK"
            ):
                try:
                    r_old = resolve(text)
                    if r_old is not None and r_old.command is not None:
                        if r_old.command == "open" and r_old.payload:
                            recheck = resolve_with_engine(f"פתח {r_old.payload}")
                            if recheck.status in _DETERMINISTIC_ERRORS:
                                feedback_msg = recheck.tts_feedback or "פקודה לא תקינה"
                                print(f"[הבנה] אימות חסם fallback ({recheck.status}): {feedback_msg}")
                                _emit("error", {"text": text, "corrected": feedback_msg})
                                return feedback_msg
                            if recheck.status == "OK" and recheck.command is not None:
                                r = recheck
                            else:
                                r = r_old
                        else:
                            r = r_old
                except Exception:
                    pass

            if r is None or r.command is None:
                print(f"[הבנה] לא זיהיתי פקודה: '{text}'")
                _emit("error", {"text": text, "corrected": "לא הובן"})
                return None  # "לא הובן" ייאמר אוטומטית

            # ניקוי כל תווי גרש/גרשיים מה-payload כהגנת עומק (השו"ת לא מזהה אותם)
            payload = (
                r.payload.replace('"', "")
                .replace("'", "")
                .replace("\u05f4", "")
                .replace("\u05f3", "")
                .replace("\u2019", "")
            )

            print(
                f"[הבנה] '{text}' → {r.command}"
                + (f" | '{payload}'" if payload else "")
                + f"  [{r.source}]"
            )
            if r.command == "open" and not getattr(r, "book_ok", True):
                print(f"[אזהרה] שם הספר לא מוכר בקטלוג: '{payload}'")
            _emit("executing",
                  {"text": text, "command": r.command, "payload": payload})
            ok = controller.execute_command(r.command, payload)
            feedback = getattr(r, "tts_feedback", "") or VOICE_COMMANDS[r.command]["feedback"]
            if not ok:
                _emit("error", {"text": text, "command": r.command,
                                "payload": payload, "corrected": "הפעולה נכשלה"})
            return feedback if ok else "הפעולה נכשלה"

        from app_settings import apply_settings, load_settings

        user_settings = load_settings()
        apply_settings(user_settings)

        pipeline = VoicePipeline(
            on_transcript=_on_transcript,
            on_status=_emit,
            tts_enabled=user_settings.tts_enabled,
        )

        def _on_settings_saved(updated) -> None:
            pipeline.update_hotkey(updated.ptt_hotkey)
            pipeline.set_tts_enabled(updated.tts_enabled)

        hud = None
        if OverlayHUD is not None:
            try:
                hud = OverlayHUD(
                    hud_q,
                    corner=user_settings.hud_corner,
                    on_close=pipeline.stop,
                    on_settings_saved=_on_settings_saved,
                    hotkey_hint=user_settings.ptt_hotkey,
                )
            except Exception as e:
                print(f"[HUD] לא זמין: {e}")

        try:
            pipeline.start(blocking_warmup=(hud is None))
            if hud is not None:
                print("[PIPELINE] פועל — PTT להקלטה, Esc/✕ לסגירת ה-HUD")
                hud.run()
            else:
                print("[PIPELINE] פועל — Ctrl+C לעצירה")
                while True:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n[PIPELINE] נעצר על ידי המשתמש")
        finally:
            pipeline.stop()
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
