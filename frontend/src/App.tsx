import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  Mic,
  MicOff,
  Phone,
  RotateCcw,
  Send,
  Settings,
  Square,
  Stethoscope,
  Wifi,
  WifiOff
} from "lucide-react";

type LogEntry = {
  ts: string;
  direction: "system" | "user" | "assistant" | "stt" | "error";
  text: string;
};

type VadSettings = {
  wlk_min_duration_real_silence: number;
  vad_energy_threshold: number;
  vad_silence_ratio: number;
  silero_threshold: number;
  silero_speech_pad_ms: number;
  silero_min_silence_ms: number;
};

const API_BASE = import.meta.env.VITE_API_BASE ?? window.location.origin;
const WS_BASE = API_BASE.replace(/^http/, "ws");

const DEFAULT_VAD: VadSettings = {
  wlk_min_duration_real_silence: 1,
  vad_energy_threshold: 0.004,
  vad_silence_ratio: 0.45,
  silero_threshold: 0.45,
  silero_speech_pad_ms: 120,
  silero_min_silence_ms: 250
};

const vadControls = [
  {
    key: "wlk_min_duration_real_silence",
    label: "Пауза до отправки (сек)",
    hint: "0.5 = быстро, но режет слова; 3.0 = медленнее, но точнее",
    min: 0.5,
    max: 3,
    step: 0.1
  },
  {
    key: "vad_energy_threshold",
    label: "Порог обнаружения речи",
    hint: "0.003 = ловит шепот; 0.01 = только громкая речь",
    min: 0.003,
    max: 0.01,
    step: 0.001
  },
  {
    key: "vad_silence_ratio",
    label: "Чувствительность к тишине",
    hint: "0.3 = быстрее переключается; 0.8 = медленнее",
    min: 0.3,
    max: 0.8,
    step: 0.01
  },
  {
    key: "silero_threshold",
    label: "Silero: порог речи",
    hint: "0.35 = тихие короткие ответы; 0.5 = дефолт",
    min: 0.35,
    max: 0.5,
    step: 0.01
  },
  {
    key: "silero_speech_pad_ms",
    label: "Silero: паддинг речи (мс)",
    hint: "200 мс не режет края; 30 мс быстрее",
    min: 30,
    max: 200,
    step: 10
  },
  {
    key: "silero_min_silence_ms",
    label: "Silero: пауза до конца фразы (мс)",
    hint: "400 мс не рвет фразу; 100 мс быстрее",
    min: 100,
    max: 400,
    step: 10
  }
] as const;

function timestamp() {
  return new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function downsampleTo16k(samples: Float32Array, inputRate: number): Float32Array {
  if (inputRate === 16000) {
    return samples;
  }
  const ratio = inputRate / 16000;
  const length = Math.floor(samples.length / ratio);
  const result = new Float32Array(length);
  for (let i = 0; i < length; i += 1) {
    const start = Math.floor(i * ratio);
    const end = Math.min(Math.floor((i + 1) * ratio), samples.length);
    let sum = 0;
    for (let j = start; j < end; j += 1) {
      sum += samples[j];
    }
    result[i] = sum / Math.max(1, end - start);
  }
  return result;
}

function floatToPcm16(samples: Float32Array): ArrayBuffer {
  const buffer = new ArrayBuffer(samples.length * 2);
  const view = new DataView(buffer);
  for (let i = 0; i < samples.length; i += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(i * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
  }
  return buffer;
}

export function App() {
  const [connected, setConnected] = useState(false);
  const [recording, setRecording] = useState(false);
  const [sessionId, setSessionId] = useState(() => crypto.randomUUID());
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [text, setText] = useState("");
  const [rms, setRms] = useState(0);
  const [vad, setVad] = useState<VadSettings>(DEFAULT_VAD);
  const [slots, setSlots] = useState<Record<string, unknown>>({});
  const [phase, setPhase] = useState("idle");

  const wsRef = useRef<WebSocket | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const workletRef = useRef<AudioWorkletNode | null>(null);
  const playerContextRef = useRef<AudioContext | null>(null);
  const activeTtsSourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());
  const recordingRef = useRef(false);

  const addLog = useCallback((direction: LogEntry["direction"], line: string) => {
    setLogs((prev) => [{ ts: timestamp(), direction, text: line }, ...prev].slice(0, 300));
  }, []);

  const stopTtsPlayback = useCallback((reason: string, notifyBackend = true) => {
    const sources = activeTtsSourcesRef.current;
    const hadSources = sources.size > 0;
    if (hadSources) {
      sources.forEach((source) => {
        try {
          source.stop();
        } catch {
          // Source may already be stopped by the AudioContext.
        }
        try {
          source.disconnect();
        } catch {
          // Ignore disconnected nodes.
        }
      });
      sources.clear();
    }
    if (notifyBackend && wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ event: "barge_in", reason }));
    }
    if (hadSources) {
      addLog("system", "Озвучка прервана");
    }
  }, [addLog]);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }
    const ws = new WebSocket(`${WS_BASE}/ws?session_id=${sessionId}&clinic_id=medcenter`);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => {
      setConnected(true);
      addLog("system", "WebSocket подключен");
    };
    ws.onclose = () => {
      setConnected(false);
      stopTtsPlayback("websocket_closed", false);
      addLog("system", "WebSocket отключен");
    };
    ws.onerror = () => addLog("error", "Ошибка WebSocket");
    ws.onmessage = async (event) => {
      if (event.data instanceof ArrayBuffer) {
        try {
          const ctx = playerContextRef.current ?? new AudioContext();
          playerContextRef.current = ctx;
          const decoded = await ctx.decodeAudioData(event.data.slice(0));
          const source = ctx.createBufferSource();
          source.buffer = decoded;
          source.connect(ctx.destination);
          source.onended = () => {
            activeTtsSourcesRef.current.delete(source);
          };
          activeTtsSourcesRef.current.add(source);
          if (ctx.state === "suspended") {
            await ctx.resume();
          }
          source.start();
        } catch (error) {
          addLog("error", `Не удалось проиграть TTS: ${String(error)}`);
        }
        return;
      }
      const payload = JSON.parse(event.data);
      if (payload.event === "assistant_response") {
        addLog("assistant", payload.text);
        setSlots(payload.slots ?? {});
        setPhase(payload.phase ?? "unknown");
      } else if (payload.event === "final_transcript") {
        addLog("stt", payload.text);
      } else if (payload.event === "slots_update") {
        setSlots(payload.slots ?? {});
        setPhase(payload.phase ?? "unknown");
      } else if (payload.event === "session_started") {
        setVad(payload.vad_settings ?? DEFAULT_VAD);
        addLog("system", `Сессия ${payload.session_id}`);
      } else if (payload.event === "tts_error") {
        addLog("error", payload.message);
      } else if (payload.event === "conversation_closed") {
        setPhase(payload.status ?? "finalized");
        addLog("system", "Разговор завершен");
      }
    };
    wsRef.current = ws;
  }, [addLog, sessionId, stopTtsPlayback]);

  useEffect(() => {
    fetch(`${API_BASE}/admin/vad-settings`)
      .then((r) => r.json())
      .then(setVad)
      .catch(() => addLog("error", "Не удалось загрузить VAD настройки"));
  }, [addLog]);

  const applyVad = useCallback(async () => {
    const params = new URLSearchParams();
    Object.entries(vad).forEach(([key, value]) => params.set(key, String(value)));
    const response = await fetch(`${API_BASE}/admin/vad-settings?${params.toString()}`, { method: "POST" });
    const data = await response.json();
    setVad(data);
    addLog("system", "VAD настройки применены");
  }, [addLog, vad]);

  const resetSession = useCallback(() => {
    stopTtsPlayback("reset_session");
    wsRef.current?.close();
    setSessionId(crypto.randomUUID());
    setSlots({});
    setPhase("idle");
    addLog("system", "Создана новая локальная сессия");
  }, [addLog, stopTtsPlayback]);

  const startMic = useCallback(async () => {
    connect();
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1
      }
    });
    const ctx = new AudioContext();
    await ctx.audioWorklet.addModule("/audio-processor.worklet.js");
    const source = ctx.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(ctx, "medjarvis-audio-processor");
    node.port.onmessage = (event) => {
      const { samples, sampleRate, rms: chunkRms } = event.data as {
        samples: Float32Array;
        sampleRate: number;
        rms: number;
      };
      setRms(chunkRms);
      if (!recordingRef.current || wsRef.current?.readyState !== WebSocket.OPEN) {
        return;
      }
      const downsampled = downsampleTo16k(samples, sampleRate);
      wsRef.current.send(floatToPcm16(downsampled));
    };
    source.connect(node);
    const silentGain = ctx.createGain();
    silentGain.gain.value = 0;
    node.connect(silentGain).connect(ctx.destination);
    streamRef.current = stream;
    audioContextRef.current = ctx;
    workletRef.current = node;
    recordingRef.current = true;
    setRecording(true);
    addLog("system", "Микрофон включен, отправка PCM16 активна");
  }, [addLog, connect]);

  const flushAudio = useCallback(() => {
    wsRef.current?.send(JSON.stringify({ event: "flush_audio" }));
  }, []);

  const stopMic = useCallback(() => {
    recordingRef.current = false;
    setRecording(false);
    flushAudio();
    workletRef.current?.disconnect();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    audioContextRef.current?.close();
    workletRef.current = null;
    streamRef.current = null;
    audioContextRef.current = null;
    addLog("system", "Микрофон выключен");
  }, [addLog, flushAudio]);

  const sendText = useCallback(() => {
    connect();
    const value = text.trim();
    if (!value) {
      return;
    }
    stopTtsPlayback("user_text");
    wsRef.current?.send(JSON.stringify({ event: "user_text", text: value }));
    addLog("user", value);
    setText("");
  }, [addLog, connect, stopTtsPlayback, text]);

  const slotEntries = useMemo(() => Object.entries(slots), [slots]);

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand">
          <Stethoscope size={26} aria-hidden />
          <div>
            <h1>MedJarvis Registry</h1>
            <p>Локальный MVP голосовой регистратуры</p>
          </div>
        </div>
        <div className="status">
          {connected ? <Wifi size={18} /> : <WifiOff size={18} />}
          <span>{connected ? "online" : "offline"}</span>
          <span className="phase">{phase}</span>
        </div>
      </header>

      <section className="workspace">
        <section className="callPanel" aria-label="Звонок">
          <div className="toolbar">
            <button className="primary" onClick={connect} title="Подключить WebSocket">
              <Phone size={18} />
              <span>Подключить</span>
            </button>
            <button onClick={recording ? stopMic : startMic} title={recording ? "Остановить микрофон" : "Запустить микрофон"}>
              {recording ? <MicOff size={18} /> : <Mic size={18} />}
              <span>{recording ? "Стоп" : "Микрофон"}</span>
            </button>
            <button onClick={flushAudio} title="Отправить текущий аудио-сегмент">
              <Square size={18} />
              <span>Flush</span>
            </button>
            <button onClick={resetSession} title="Новая сессия">
              <RotateCcw size={18} />
              <span>Новая</span>
            </button>
          </div>

          <div className="meterRow">
            <Activity size={18} />
            <div className="meter" aria-label="Уровень микрофона">
              <span style={{ width: `${Math.min(100, rms * 900)}%` }} />
            </div>
            <code>{rms.toFixed(4)}</code>
          </div>

          <div className="textSend">
            <input
              value={text}
              onChange={(event) => setText(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") sendText();
              }}
              placeholder="Текстовый тест без микрофона"
            />
            <button className="iconOnly" onClick={sendText} title="Отправить текст">
              <Send size={18} />
            </button>
          </div>

          <div className="slots">
            <h2>Слоты заявки</h2>
            {slotEntries.length === 0 ? (
              <p className="muted">Пока нет собранных данных.</p>
            ) : (
              <dl>
                {slotEntries.map(([key, value]) => (
                  <div key={key}>
                    <dt>{key}</dt>
                    <dd>{typeof value === "object" ? JSON.stringify(value, null, 2) : String(value)}</dd>
                  </div>
                ))}
              </dl>
            )}
          </div>
        </section>

        <section className="vadPanel" aria-label="VAD настройки">
          <div className="panelTitle">
            <Settings size={20} />
            <h2>Настройки распознавания (VAD)</h2>
          </div>
          {vadControls.map((control) => {
            const key = control.key as keyof VadSettings;
            return (
              <label className="slider" key={control.key}>
                <span className="sliderHeader">
                  <span>{control.label}</span>
                  <strong>{vad[key]}</strong>
                </span>
                <input
                  type="range"
                  min={control.min}
                  max={control.max}
                  step={control.step}
                  value={vad[key]}
                  onChange={(event) =>
                    setVad((prev) => ({
                      ...prev,
                      [key]: control.step >= 1 ? Number.parseInt(event.target.value, 10) : Number.parseFloat(event.target.value)
                    }))
                  }
                />
                <small>{control.hint}</small>
              </label>
            );
          })}
          <button className="apply" onClick={applyVad}>
            <Settings size={18} />
            <span>Применить настройки</span>
          </button>
          <div className="presets">
            <span>Точность</span>
            <span>Скорость</span>
            <span>Тихий голос</span>
          </div>
        </section>

        <section className="logPanel" aria-label="Логи диалога">
          <h2>Логи</h2>
          <div className="logList">
            {logs.map((entry, index) => (
              <article className={`log ${entry.direction}`} key={`${entry.ts}-${index}`}>
                <time>{entry.ts}</time>
                <span>{entry.direction}</span>
                <p>{entry.text}</p>
              </article>
            ))}
          </div>
        </section>
      </section>
    </main>
  );
}
